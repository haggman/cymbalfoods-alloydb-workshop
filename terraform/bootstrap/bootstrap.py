#!/usr/bin/env python3
"""
CymbalGoal setup VM bootstrap: brings a fresh cluster to "this lab's data is
loaded, indexed, and owned by the student" before the student arrives.

WHERE: the setup VM (setup_vm.tf), started by startup.sh on first boot.
CONFIG: the `cg-config` metadata attribute, written by Terraform from the
        module's column of the feature map (modules.tf).
OUTPUT: gs://<staging bucket>/setup/status     one line, read by Task 0
        gs://<staging bucket>/setup/handback.json   timings and checks

THE OWNERSHIP RULE, and why every SQL statement here runs AS the student.
Whoever creates a database owns it, and alloydbsuperuser is not a true
superuser: a database or table owned by `postgres` or by this VM's service
account cannot be fully managed by the student (no ALTER ... OWNER, no DROP,
no REINDEX). So this script first makes its own login a member of the
student's role, then runs `SET ROLE "<student>"` on every connection. The
database, extensions, tables and indexes it creates are the student's from the
start. Ways to become a member, tried in order and reported in the handback:
  1. the Admin API (users.patch, add the student's role to databaseRoles):
     AlloyDB's control plane runs the GRANT as a real superuser
  2. a SQL GRANT, in case alloydbsuperuser is allowed to do it
If neither works the script stops with a FAILED status. The fallback is the
mkt014/mkt015 pattern: the student runs the load as themselves in Task 0.

LOADING is ported from mkt014's lab2-setup.py (COPY path, header detection,
manifest column order, profile pass 2) and the 02 load test (server-side import
through the in-project staging bucket). The default is AlloyDB's server-side
import, one call per file, with client-side COPY as the automatic fallback per
file. Every load records which path it took and how long it ran.

RE-RUNS: every step skips what is already done, so `gcloud compute instances
reset cymbalgoal-setup` after a failure picks up where it stopped.
"""

import csv
import gzip
import io
import json
import math
import subprocess
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, timezone

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
C = requests.get(
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/cg-config",
    headers={"Metadata-Flavor": "Google"}, timeout=10).json()

MODULE = C["module"]
PROJECT = C["project"]
REGION = C["region"]
STUDENT = C["student"]
SA_USER = C["setup_db_user"]
BUCKET = C["bucket"]
DB = C["database"]
CORE_SRC = C["core_src"].rstrip("/")
REPORTS_SRC = C["reports_src"].rstrip("/")
DATASETS = list(C.get("datasets") or [])
INDEXES = C.get("indexes", "none")
METHOD = C.get("load_method", "import")
DATA_API = bool(C.get("data_api"))

INSTANCE_URI = f"projects/{PROJECT}/locations/{REGION}/clusters/{C['cluster']}/instances/{C['instance']}"
API = "https://alloydb.googleapis.com/v1"
CLUSTER_URL = f"{API}/projects/{PROJECT}/locations/{REGION}/clusters/{C['cluster']}"
STAGE = f"gs://{BUCKET}/setup/data"
STATUS_URI = f"gs://{BUCKET}/setup/status"

CORE_TABLES = ["competitions", "clubs", "players", "games",
               "appearances", "game_events", "player_valuations", "transfers"]
PASS2_COLS = {"profile_text", "profile_embedding"}
EXPECT = {"players": 13439, "clubs": 796, "appearances": 832193}

# mkt015's baseline: the indexes a SEARCH app needs, and none of the ones deadline
# day needs. Read lab3-setup.py's comment before adding to this list: the
# aldb005 workload and the Index Advisor task depend on these gaps.
BASELINE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_appearances_player ON appearances (player_id)",
    "CREATE INDEX IF NOT EXISTS idx_appearances_game ON appearances (game_id)",
    "CREATE INDEX IF NOT EXISTS idx_game_events_game ON game_events (game_id)",
    "CREATE INDEX IF NOT EXISTS idx_player_valuations_player ON player_valuations (player_id)",
    "CREATE INDEX IF NOT EXISTS idx_transfers_player ON transfers (player_id)",
    "CREATE INDEX IF NOT EXISTS idx_games_competition_season ON games (competition_id, season)",
]

BOOT = time.time() - float(open("/proc/uptime").read().split()[0])
HB = {"module": MODULE, "project": PROJECT, "datasets": DATASETS, "indexes": INDEXES,
      "load_method": METHOD, "vm_boot_utc": datetime.fromtimestamp(BOOT, timezone.utc).isoformat(),
      "steps": [], "loads": [], "notes": [], "checks": {}}


class Fatal(Exception):
    pass


def log(msg=""):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def since_boot():
    s = int(time.time() - BOOT)
    return f"{s // 60}m{s % 60:02d}s"


def note(msg):
    log(f"NOTE {msg}")
    HB["notes"].append(msg)


def sh(cmd, check=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Fatal(f"`{cmd[:120]}` failed: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def put(uri, text):
    path = "/opt/cymbalgoal/_put.tmp"
    with open(path, "w") as fh:
        fh.write(text)
    subprocess.run(["gcloud", "storage", "cp", "--quiet", path, uri], capture_output=True)


def status(line):
    log(f"STATUS {line}")
    put(STATUS_URI, line + "\n")


def cat(uri):
    r = subprocess.run(["gcloud", "storage", "cat", uri], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fatal(f"could not read {uri}: {r.stderr.strip()[:300]}")
    return r.stdout


def ident(name):
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Steps and the status line
# ---------------------------------------------------------------------------
class Steps:
    def __init__(self, names):
        self.names, self.i, self.t = names, 0, None
        self.current = "starting"

    def next(self, name):
        self.close()
        self.i += 1
        self.current = name
        self.t = time.time()
        status(f"loading {self.i}/{len(self.names)}: {name} ({since_boot()} since the VM started)")

    def close(self):
        if self.t is not None:
            HB["steps"].append({"step": self.current, "seconds": round(time.time() - self.t, 1)})
            log(f"  done: {self.current} in {time.time() - self.t:.1f}s")
            self.t = None


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------
_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
S = AuthorizedSession(_creds)
S.headers["X-Goog-User-Project"] = PROJECT


def api(method, url, retry_s=300, **kw):
    """Admin API call. Retries what time fixes: IAM that hasn't propagated
    (403, first minutes of the VM), another operation on the cluster (409 /
    FAILED_PRECONDITION), quota and server errors. Anything else is returned."""
    t_end = time.time() + retry_s
    while True:
        r = S.request(method, url, timeout=60, **kw)
        busy = r.status_code == 409 or (r.status_code == 400 and "progress" in r.text.lower())
        if r.ok or not (busy or r.status_code in (403, 429, 500, 502, 503, 504)):
            return r
        if time.time() > t_end:
            return r
        log(f"  {method} {url.split('/v1/')[-1][:80]} -> {r.status_code}, retrying in 15s")
        time.sleep(15)


def wait_op(name, timeout_s=1800):
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        op = api("GET", f"{API}/{name}").json()
        if op.get("done"):
            return op
        time.sleep(5)
    raise Fatal(f"operation {name} still running after {timeout_s}s")


def wait_instance_ready(timeout_s=900):
    t_end = time.time() + timeout_s
    while time.time() < t_end:
        st = api("GET", f"{API}/{INSTANCE_URI}").json().get("state")
        if st == "READY":
            return
        time.sleep(10)
    raise Fatal(f"instance not READY after {timeout_s}s")


# ---------------------------------------------------------------------------
# Data API (aldb003). No Terraform attribute and no gcloud flag exist for it
# (mkt014, verified). The enum is ENABLED; the docs prose's ALLOW_DATA_API is a
# 400. ⚠️ It RESTARTS the instance (mkt015, measured), so it is fired first and
# waited on before any SQL runs, and the VM's other prep overlaps the ~2 min.
# ---------------------------------------------------------------------------
def data_api_fire():
    r = api("PATCH", f"{API}/{INSTANCE_URI}", params={"updateMask": "dataApiAccess"},
            json={"dataApiAccess": "ENABLED"})
    if not r.ok:
        raise Fatal(f"Data API PATCH -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json().get("name")


def data_api_state():
    return api("GET", f"{API}/{INSTANCE_URI}").json().get("dataApiAccess", "<absent>")


# ---------------------------------------------------------------------------
# SQL sessions: the VM's IAM login over the private IP, SET ROLE to the student
# ---------------------------------------------------------------------------
from google.cloud.alloydb.connector import Connector, IPTypes  # noqa: E402

CONNECTOR = Connector()


def connect(db, as_student=True, retry_s=300):
    t_end = time.time() + retry_s
    while True:
        try:
            c = CONNECTOR.connect(INSTANCE_URI, "pg8000", user=SA_USER, db=db,
                                  enable_iam_auth=True, ip_type=IPTypes.PRIVATE)
            c.autocommit = True
            if as_student:
                cur = c.cursor()
                cur.execute(f"SET ROLE {ident(STUDENT)}")
                cur.close()
            return c
        except Exception as e:  # noqa: BLE001
            if time.time() > t_end:
                raise Fatal(f"cannot connect to {db} as {SA_USER}: {type(e).__name__}: {e}")
            log(f"  connect {db}: {type(e).__name__}: {str(e)[:160]}; retrying in 15s")
            time.sleep(15)


class Session:
    """One live connection to the lab database, reconnected if AlloyDB drops it."""

    def __init__(self):
        self.c = None

    def conn(self):
        if self.c is not None:
            try:
                cur = self.c.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return self.c
            except Exception:  # noqa: BLE001
                pass
        self.c = connect(DB)
        return self.c

    def run(self, sql, params=None):
        cur = self.conn().cursor()
        cur.execute(sql, params) if params else cur.execute(sql)
        cur.close()

    def rows(self, sql, params=None):
        cur = self.conn().cursor()
        cur.execute(sql, params) if params else cur.execute(sql)
        out = cur.fetchall()
        cur.close()
        return out

    def one(self, sql, params=None):
        r = self.rows(sql, params)
        return r[0][0] if r else None


def q1(c, sql, params=None):
    cur = c.cursor()
    cur.execute(sql, params) if params else cur.execute(sql)
    r = cur.fetchall() if cur.description else []
    cur.close()
    return r[0][0] if r else None


# ---------------------------------------------------------------------------
# Ownership: join the student's role, then create the database as the student
# ---------------------------------------------------------------------------
def is_member(c):
    try:
        if not q1(c, "SELECT pg_has_role(current_user, %s, 'MEMBER')", (STUDENT,)):
            return False
        cur = c.cursor()
        cur.execute(f"SET ROLE {ident(STUDENT)}")
        cur.execute("RESET ROLE")
        cur.close()
        return True
    except Exception as e:  # noqa: BLE001
        log(f"  membership check: {e}")
        return False


def ensure_membership():
    c = connect("postgres", as_student=False)
    HB["checks"]["roles"] = {
        "vm_login": q1(c, "SELECT current_user"),
        "student_rolcreatedb": q1(c, "SELECT rolcreatedb FROM pg_roles WHERE rolname=%s", (STUDENT,)),
        "vm_rolcreatedb": q1(c, "SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user"),
    }
    if is_member(c):
        return "already a member"
    errors = []

    # 1. Admin API: AlloyDB's control plane does the GRANT.
    uid = urllib.parse.quote(SA_USER, safe="")
    try:
        r = api("GET", f"{CLUSTER_URL}/users/{uid}")
        if not r.ok:
            raise Fatal(f"GET user -> {r.status_code} {r.text[:200]}")
        roles = r.json().get("databaseRoles", [])
        r = api("PATCH", f"{CLUSTER_URL}/users/{uid}", params={"updateMask": "databaseRoles"},
                json={"databaseRoles": sorted(set(roles) | {STUDENT})})
        if not r.ok:
            raise Fatal(f"PATCH user -> {r.status_code} {r.text[:300]}")
        c.close()
        c = connect("postgres", as_student=False)
        if is_member(c):
            return "Admin API users.patch (student's role added to databaseRoles)"
        errors.append("Admin API accepted the patch but the login is still not a member")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Admin API: {e}")

    # 2. SQL GRANT, in case alloydbsuperuser may grant this role.
    try:
        cur = c.cursor()
        cur.execute(f"GRANT {ident(STUDENT)} TO CURRENT_USER")
        cur.close()
        if is_member(c):
            return "SQL GRANT"
        errors.append("SQL GRANT ran but the login is still not a member")
    except Exception as e:  # noqa: BLE001
        errors.append(f"SQL GRANT: {e}")

    HB["checks"]["membership_errors"] = errors
    raise Fatal("could not join the student's role, so nothing can be created as the student: "
                + " | ".join(str(x)[:200] for x in errors))


def ensure_database():
    c = connect("postgres", as_student=False)
    owner = q1(c, "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (DB,))
    if owner == STUDENT:
        return "already there, owned by the student"
    if owner is not None and owner != q1(c, "SELECT current_user"):
        raise Fatal(f"database {DB} exists but is owned by {owner}, not the student")
    if owner is not None:
        cur = c.cursor()
        cur.execute(f"ALTER DATABASE {ident(DB)} OWNER TO {ident(STUDENT)}")
        cur.close()
        return "existed, owned by the VM; ownership given to the student"
    errors = []
    try:
        cur = c.cursor()
        cur.execute(f"SET ROLE {ident(STUDENT)}")
        cur.execute(f"CREATE DATABASE {ident(DB)}")
        cur.close()
        return "SET ROLE to the student, then CREATE DATABASE"
    except Exception as e:  # noqa: BLE001
        errors.append(f"as the student: {e}")
    try:
        cur = c.cursor()
        cur.execute("RESET ROLE")
        cur.execute(f"CREATE DATABASE {ident(DB)} OWNER {ident(STUDENT)}")
        cur.close()
        return "CREATE DATABASE ... OWNER the student, as the VM"
    except Exception as e:  # noqa: BLE001
        errors.append(f"as the VM with OWNER: {e}")
    raise Fatal("could not create the database: " + " | ".join(str(x)[:200] for x in errors))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def first_row(uri):
    p = subprocess.Popen(["gcloud", "storage", "cat", uri], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    try:
        with gzip.GzipFile(fileobj=p.stdout, mode="rb") as gz:
            return next(csv.reader(io.TextIOWrapper(gz, encoding="utf-8")))
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def copy_stream(sess, table, cols, uri, header):
    """Client-side COPY: the gzip streams from Cloud Storage through this VM."""
    conn = sess.conn()
    cur = conn.cursor()
    p = subprocess.Popen(["gcloud", "storage", "cat", uri], stdout=subprocess.PIPE)
    try:
        with gzip.GzipFile(fileobj=p.stdout, mode="rb") as gz:
            cur.execute(f"COPY {table} ({', '.join(cols)}) FROM STDIN "
                        f"WITH (FORMAT csv, HEADER {'true' if header else 'false'})", stream=gz)
    finally:
        cur.close()
        p.stdout.close()
        p.wait()


def import_file(sess, uri, table, cols):
    """AlloyDB server-side import. The service agent reads the file (it has
    objectViewer on the staging bucket); COPY runs as the database user named
    in the request. The student owns the table, so try the student first."""
    errors = []
    for user in (STUDENT, "postgres"):
        if user == "postgres":
            try:
                sess.run(f"GRANT ALL ON TABLE {table} TO postgres")
            except Exception as e:  # noqa: BLE001
                errors.append(f"GRANT to postgres: {str(e)[:150]}")
                continue
        body = {"gcsUri": uri, "database": DB, "user": user,
                "csvImportOptions": {"table": table, "columns": cols}}
        t0 = time.time()
        r = api("POST", f"{CLUSTER_URL}:import", json=body)
        if not r.ok:
            errors.append(f"as {user}: HTTP {r.status_code} {r.text[:250]}")
            continue
        op = wait_op(r.json()["name"])
        if "error" in op:
            errors.append(f"as {user}: {json.dumps(op['error'])[:300]}")
            if sess.one(f"SELECT count(*) FROM {table}"):
                sess.run(f"TRUNCATE {table}")
            continue
        return {"ok": True, "user": user, "seconds": round(time.time() - t0, 1), "errors": errors}
    return {"ok": False, "errors": errors}


def load(sess, table, cols, src_uri, staged_uri):
    n = sess.one(f"SELECT count(*) FROM {table}")
    if n:
        log(f"  {table:22s} already loaded, {n:,} rows")
        HB["loads"].append({"table": table, "path": "skipped (already loaded)", "rows": n})
        return
    first = first_row(src_uri)
    if len(first) != len(cols):
        raise Fatal(f"{src_uri} has {len(first)} fields, the column list has {len(cols)}. Refusing to load.")
    header = [x.strip().lower() for x in first] == [x.strip().lower() for x in cols]
    rec = {"table": table}
    t0 = time.time()
    done = False
    if METHOD == "import" and not header:
        res = import_file(sess, staged_uri, table, cols)
        if res["ok"]:
            rec.update(path=f"import as {'student' if res['user'] == STUDENT else res['user']}")
            done = True
        if res["errors"]:
            rec["import_errors"] = res["errors"]
    if not done:
        if METHOD == "import":
            rec["path"] = "COPY (fallback: " + ("file has a header row" if header else "import failed") + ")"
        else:
            rec["path"] = "COPY"
        copy_stream(sess, table, cols, src_uri, header)
    rec["seconds"] = round(time.time() - t0, 1)
    rec["rows"] = sess.one(f"SELECT count(*) FROM {table}")
    HB["loads"].append(rec)
    log(f"  {table:22s} {rec['rows']:>9,} rows {rec['seconds']:>6.1f}s  {rec['path']}")


def stage_sources(files):
    """Server-side copy from the course bucket into the project's staging bucket,
    which the AlloyDB service agent can read. A few seconds."""
    todo = [f for f in files if subprocess.run(["gcloud", "storage", "ls", f"{STAGE}/{f.rsplit('/', 1)[1]}"],
                                                  capture_output=True).returncode != 0]
    if todo:
        sh("gcloud storage cp --quiet " + " ".join(todo) + f" {STAGE}/", check=True)
    return len(todo)


def core_columns():
    man = json.loads(cat(f"{CORE_SRC}/manifest.json"))
    staged = man.get("staged_files")
    items = staged.items() if isinstance(staged, dict) else [(f.get("name"), f) for f in staged]
    cols = {}
    for key, meta in items:
        if isinstance(meta, dict) and meta.get("column_order"):
            t = str(key).split("/")[-1].replace(".csv.gz", "").replace(".csv", "")
            cols[t] = [c for c in meta["column_order"] if c not in PASS2_COLS]
    missing = [t for t in CORE_TABLES if t not in cols]
    if missing:
        raise Fatal(f"manifest has no column_order for {missing}")
    return cols


# ---------------------------------------------------------------------------
# The datasets
# ---------------------------------------------------------------------------
def load_schema(sess):
    present = [r[0] for r in sess.rows(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
        "AND table_type='BASE TABLE' AND table_name = ANY(%s)", (CORE_TABLES,))]
    if len(present) == len(CORE_TABLES):
        log("  all 8 tables exist; skipping schema.sql")
        return
    for t in present:      # a half-built schema from an interrupted run
        sess.run(f"DROP TABLE IF EXISTS {t} CASCADE")
    if present:
        note(f"dropped a half-built schema ({len(present)} of 8 tables) before re-creating it")
    sess.run(cat(f"{CORE_SRC}/schema.sql"))


def load_core(sess):
    cols = core_columns()
    for t in CORE_TABLES:
        load(sess, t, cols[t], f"{CORE_SRC}/{t}.csv.gz", f"{STAGE}/{t}.csv.gz")
    for t, n in EXPECT.items():
        got = sess.one(f"SELECT count(*) FROM {t}")
        if got != n:
            raise Fatal(f"{t} has {got:,} rows, expected {n:,}")


def load_profiles(sess):
    for t, key in (("players", "player_id"), ("clubs", "club_id")):
        filled = sess.one(f"SELECT count(*) FROM {t} WHERE profile_embedding IS NOT NULL")
        if filled == EXPECT[t]:
            log(f"  {t} profiles already loaded")
            continue
        stg = f"_{t}_profiles"
        sess.run(f"DROP TABLE IF EXISTS {stg}")
        sess.run(f"CREATE TABLE {stg} ({key} INTEGER, profile_text TEXT, profile_embedding VECTOR(3072))")
        load(sess, stg, [key, "profile_text", "profile_embedding"],
             f"{CORE_SRC}/{t}_profiles.csv.gz", f"{STAGE}/{t}_profiles.csv.gz")
        t0 = time.time()
        sess.run(f"UPDATE {t} tgt SET profile_text = src.profile_text, "
                 f"profile_embedding = src.profile_embedding FROM {stg} src WHERE tgt.{key} = src.{key}")
        sess.run(f"DROP TABLE {stg}")   # a twin of the real column confuses every schema-reading agent (mkt014)
        HB["loads"].append({"table": f"{t} profile UPDATE", "seconds": round(time.time() - t0, 1)})
        got = sess.one(f"SELECT count(*) FROM {t} WHERE profile_embedding IS NOT NULL")
        if got != EXPECT[t]:
            raise Fatal(f"{t}: {got:,} profiles, expected {EXPECT[t]:,}")


def load_reports(sess):
    man = json.loads(cat(f"{REPORTS_SRC}/scout_reports_manifest.json"))
    cols = man["column_order"]
    expect = man["files"]["scout_reports.csv.gz"]["rows"]
    if sess.one("SELECT to_regclass('public.scout_reports') IS NULL"):
        sess.run(cat(f"{REPORTS_SRC}/scout_reports.sql"))
    load(sess, "scout_reports", cols, f"{REPORTS_SRC}/scout_reports.csv.gz", f"{STAGE}/scout_reports.csv.gz")
    got = sess.one("SELECT count(*) FROM scout_reports")
    if got != expect:
        raise Fatal(f"scout_reports has {got:,} rows, the manifest says {expect:,}")
    HB["checks"]["scout_reports_prompt_version"] = man.get("prompt_version")


def build_indexes(sess):
    sess.run("SET maintenance_work_mem = '2GB'")
    t0 = time.time()
    if INDEXES == "baseline":
        for ddl in BASELINE_INDEXES:
            sess.run(ddl)
    elif INDEXES == "scann":
        have = sess.one("SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
                        "AND tablename IN ('players','clubs') AND indexdef ILIKE '%scann%'")
        if have >= 2:
            log("  profile indexes already built")
        else:
            sess.run(cat(f"{CORE_SRC}/indexes.sql"))
        if "scout_reports" in DATASETS:
            n = sess.one("SELECT count(*) FROM scout_reports")
            leaves = max(1, int(math.sqrt(n)))
            sess.run("CREATE INDEX IF NOT EXISTS scout_reports_player_idx ON scout_reports (player_id)")
            sess.run("CREATE INDEX IF NOT EXISTS scout_reports_season_idx ON scout_reports (season)")
            sess.run("CREATE INDEX IF NOT EXISTS scout_reports_embedding_scann ON scout_reports "
                     f"USING scann (report_embedding cosine) WITH (num_leaves = {leaves}, quantizer = 'sq8')")
    sess.run("RESET maintenance_work_mem")
    sess.run("ANALYZE")
    HB["checks"]["index_build_seconds"] = round(time.time() - t0, 1)


EXTENSIONS = {"vector", "google_ml_integration"}


def ensure_extensions(sess):
    exts = set(EXTENSIONS)
    if INDEXES == "scann":
        exts |= {"alloydb_scann", "pg_trgm"}   # pg_trgm: QueryData value searches (mkt014)
    for e in sorted(exts):
        sess.run(f"CREATE EXTENSION IF NOT EXISTS {e}")


# ---------------------------------------------------------------------------
# Checks: the ownership proof and what the lab will find
# ---------------------------------------------------------------------------
def checks(sess):
    ck = HB["checks"]
    ck["database_owner"] = sess.one(
        "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
    ck["objects_not_owned_by_student"] = [f"{r[0]} ({r[1]}) owner {r[2]}" for r in sess.rows(
        "SELECT c.relname, c.relkind, pg_catalog.pg_get_userbyid(c.relowner) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
        "AND c.relkind IN ('r','p','i','S','v','m') AND pg_catalog.pg_get_userbyid(c.relowner) <> %s",
        (STUDENT,))]
    ck["extensions"] = {r[0]: f"{r[1]} owner {r[2]}" for r in sess.rows(
        "SELECT extname, extversion, pg_catalog.pg_get_userbyid(extowner) FROM pg_extension ORDER BY 1")}
    ck["tables"] = {r[0]: r[1] for r in sess.rows(
        "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname")}
    ck["indexes"] = [r[0] for r in sess.rows(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY 1")]
    ck["server_version"] = sess.one("SHOW server_version")
    ok = ck["database_owner"] == STUDENT and not ck["objects_not_owned_by_student"]
    ck["student_owns_everything"] = ok
    if not ok:
        raise Fatal(f"ownership check failed: database owner {ck['database_owner']}, "
                    f"{len(ck['objects_not_owned_by_student'])} objects owned by someone else")


# ---------------------------------------------------------------------------
def write_handback(outcome):
    HB["outcome"] = outcome
    HB["finished_utc"] = datetime.now(timezone.utc).isoformat()
    HB["seconds_since_vm_boot"] = round(time.time() - BOOT)
    put(f"gs://{BUCKET}/setup/handback.json", json.dumps(HB, indent=2, default=str))


def main():
    HB["bootstrap_start_utc"] = datetime.now(timezone.utc).isoformat()
    HB["seconds_boot_to_bootstrap"] = round(time.time() - BOOT)
    log(f"module {MODULE}; datasets {DATASETS}; indexes {INDEXES}; method {METHOD}; data_api {DATA_API}")
    log(f"target {INSTANCE_URI} as {SA_USER} -> SET ROLE {STUDENT}")

    plan = []
    if DATA_API:
        plan.append("turn on the Data API")
    if DATASETS:
        plan += ["copy source files"]
        if DATA_API:
            plan.append("wait for the Data API restart")
        plan += ["join the student's role", "create the database as the student", "extensions and schema"]
        plan += [{"core": "core tables", "profiles": "player and club profiles",
                  "scout_reports": "scout reports"}[d] for d in DATASETS]
        if INDEXES != "none":
            plan.append("indexes")
        plan.append("ownership and row checks")
    steps = Steps(plan or ["nothing to load"])

    try:
        op = None
        if DATA_API:
            steps.next("turn on the Data API")
            op = data_api_fire()

        if not DATASETS:
            steps.next("nothing to load")
            if op:
                wait_op(op)
            steps.close()
        else:
            steps.next("copy source files")
            files = []
            if METHOD == "import":
                if "core" in DATASETS:
                    files += [f"{CORE_SRC}/{t}.csv.gz" for t in CORE_TABLES]
                if "profiles" in DATASETS:
                    files += [f"{CORE_SRC}/players_profiles.csv.gz", f"{CORE_SRC}/clubs_profiles.csv.gz"]
                if "scout_reports" in DATASETS:
                    files.append(f"{REPORTS_SRC}/scout_reports.csv.gz")
            HB["checks"]["files_copied"] = stage_sources(files) if files else 0

            if DATA_API:
                steps.next("wait for the Data API restart")
                done = wait_op(op)
                if "error" in done:
                    raise Fatal(f"Data API operation failed: {json.dumps(done['error'])[:300]}")
                wait_instance_ready()

            steps.next("join the student's role")
            HB["checks"]["membership"] = ensure_membership()
            log(f"  membership: {HB['checks']['membership']}")

            steps.next("create the database as the student")
            HB["checks"]["database_created"] = ensure_database()
            log(f"  database: {HB['checks']['database_created']}")

            sess = Session()
            steps.next("extensions and schema")
            ensure_extensions(sess)
            if "core" in DATASETS:
                load_schema(sess)

            for d in DATASETS:
                steps.next({"core": "core tables", "profiles": "player and club profiles",
                            "scout_reports": "scout reports"}[d])
                {"core": load_core, "profiles": load_profiles, "scout_reports": load_reports}[d](sess)

            if INDEXES != "none":
                steps.next("indexes")
                build_indexes(sess)

            steps.next("ownership and row checks")
            checks(sess)
            steps.close()

        if DATA_API:
            HB["checks"]["data_api"] = data_api_state()
            if HB["checks"]["data_api"] != "ENABLED":
                raise Fatal(f"dataApiAccess reads {HB['checks']['data_api']}, not ENABLED")

    except Exception as e:  # noqa: BLE001
        steps.close()
        msg = str(e) if isinstance(e, Fatal) else f"{type(e).__name__}: {e}"
        log(traceback.format_exc())
        HB["error"] = msg
        write_handback("FAILED")
        status(f"FAILED at {steps.i}/{len(steps.names)} {steps.current}: {msg[:300]}. "
               f"Instructor: gs://{BUCKET}/setup/bootstrap.log")
        return 1

    write_handback("ready")
    what = ", ".join(DATASETS) if DATASETS else "nothing to load"
    extra = "; the deadline-day workload is starting" if C.get("load_generator") else ""
    status(f"ready: {MODULE} setup finished in {since_boot()} ({what}){extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
