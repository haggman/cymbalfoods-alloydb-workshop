#!/usr/bin/env python3
"""
CymbalGoal deadline-day workload generator. Track A carries mkt015's generator
unchanged except for make_connect(), which also takes its target from the
environment so the aldb005 setup VM can run it as a service (see
cymbalgoal-workload.service). With none of the CG_* connection variables set it
behaves exactly as it did in Cloud Shell for mkt015.

  CG_INSTANCE_URI  full instance path (default: discovered with gcloud)
  CG_DB_USER       database login (default: the gcloud account)
  CG_SET_ROLE      role to SET after connecting (the VM uses the student's)
  CG_IP_TYPE       PUBLIC (default) or PRIVATE

Original header follows.

CymbalGoal Lab 3 (mkt015) — the deadline-day workload generator.

This catalogue has been measured extensively against live AlloyDB clusters
during lab development — the weights, worker counts and queries below are the
ones the shipped lab's figures were taken from. If you point it at your own
instance, expect the same shape of load and your own numbers.

WHAT IT IS FOR. Lab 3's story is an incident: transfer deadline day, load up,
response times creeping. That story needs a database that is actually under
load, and it needs the load to be legible from four different console surfaces —
Database Center, Query Insights, the Index Advisor, and Active Queries. Each of
those has to have something DIFFERENT to say, or the lab is one finding told
four times.

THE THREE DESIGN CONSTRAINTS, and they pull against each other:

  1. IT HAS TO RUN FROM CLOUD SHELL. There is no VM in this series. P-55
     measured the COPY path as client-CPU-bound, and Cloud Shell is the weak
     end of it. So this generator makes the SERVER work hard while the CLIENT
     does almost nothing: every statement returns an AGGREGATE, never rows.
     Reading 832,000 rows to return one number costs the client a single int.

  2. IT HAS TO BE ATTRIBUTABLE. Every statement carries a sqlcommenter comment
     naming the part of the app that issued it. That is what turns Task 2 from
     "this query is slow" into "the transfer ticker is slow", which is the
     sentence an on-call developer actually needs to say. It depends on
     preserve_comments and record_application_tags in the instance's
     observability config — see the Terraform.

  3. IT HAS TO BE HONEST. Every slow query here is slow because of a real gap
     in the shipped schema, not because it was written to be slow. The six
     baseline indexes serve the SEARCH application CymbalGoal used to be.
     Deadline day introduces access patterns nobody indexed for. That is a
     thing that happens to real systems, and it is why the Index Advisor is
     being asked a genuine question.

USAGE
    python3 deadline_day.py                 # run until stopped
    python3 deadline_day.py --seconds 600   # run for ten minutes
    python3 deadline_day.py --report        # percentiles from the last run

    touch workload/.stop                    # graceful stop
"""

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, ".stop")
OUT = os.path.join(HERE, "out")
SAMPLES = os.path.join(OUT, "samples.jsonl")

DB_NAME = os.environ.get("CG_DB", "cymbalgoal")

# 🔴 THE CORPUS RUNS TO 2026-05-24, NOT 2025.
#
# Measured 2026-08-21: appearances.appearance_date spans 2012-08-10 to
# 2026-05-24 over 832,193 rows, and 105,453 of them fall in the last 30 days.
#
# The first cut anchored every date predicate on '2025-01-01', which is sixteen
# months back from the end of the data. "The last three days" was therefore a
# slice out of the MIDDLE of the corpus with another year and a half of rows
# sitting after it — which is not what deadline day means, and it quietly made
# every window predicate match far more than intended.
#
# Anchor on the real end of the data instead. main() refreshes this from the
# database at startup so it cannot go stale if the corpus is ever regenerated.
ANCHOR = "2026-05-24"

# ---------------------------------------------------------------------------
# THE QUERY CATALOGUE
# ---------------------------------------------------------------------------
# Each entry is (tag, weight, sql-builder). `tag` becomes the sqlcommenter
# controller value, so it is what shows up in Query Insights' TAGS view — keep
# the names in the language of the app, not the database.
#
# ⚠️ THE COLUMN IN EACH WHERE CLAUSE IS THE POINT. Annotated per query. If any
# of these columns ever gets an index in setup/lab3-setup.py's BASELINE_INDEXES,
# the corresponding query stops being interesting and Task 3 loses a finding.

def q_transfer_ticker(rnd):
    """Deadline-day ticker: what has moved in the last N days.

    UNINDEXED: transfers.transfer_date. The schema indexes transfers by
    player_id, because the search app looked up one player's history. Nobody
    ever asked "what happened today" until today.
    """
    days = rnd.choice([1, 3, 7, 14, 30])
    return f"""
        SELECT count(*), sum(transfer_fee), max(transfer_fee)
          FROM transfers
         WHERE transfer_date >= DATE '{ANCHOR}' - INTERVAL '{days} days'
           AND transfer_fee IS NOT NULL"""


def q_contract_watch(rnd):
    """Who is out of contract and therefore a free-agent target.

    UNINDEXED: players.contract_expiration_date.
    """
    m = rnd.choice([3, 6, 12, 18])
    return f"""
        SELECT main_position, count(*), avg(market_value_in_eur)::BIGINT
          FROM players
         WHERE contract_expiration_date IS NOT NULL
           AND contract_expiration_date < DATE '{ANCHOR}' + INTERVAL '{m} months'
         GROUP BY main_position"""


def q_squad_view(rnd):
    """One club's current squad.

    UNINDEXED: players.current_club_id. It is a foreign key, and PostgreSQL
    does not index foreign keys automatically — the single most common index
    gap in any schema, and it is sitting in this one honestly.
    """
    club = rnd.randint(1, 60000)
    return f"""
        SELECT count(*), avg(height_in_cm), sum(market_value_in_eur)
          FROM players
         WHERE current_club_id = {club}"""


def q_form_window(rnd):
    """Form over a date window — the heaviest of the routine queries.

    UNINDEXED: appearances.appearance_date, over 832,193 rows.
    """
    days = rnd.choice([7, 14, 30, 60])
    return f"""
        SELECT count(*), sum(goals), sum(assists), sum(minutes_played)
          FROM appearances
         WHERE appearance_date >= DATE '{ANCHOR}' - INTERVAL '{days} days'"""


def q_player_timeline(rnd):
    """Every event for one player.

    UNINDEXED: game_events.player_id, over 417,617 rows. The schema indexes
    game_events by game_id — the search app rendered a match, not a career.
    """
    pid = rnd.randint(1, 1200000)
    return f"""
        SELECT event_type, count(*)
          FROM game_events
         WHERE player_id = {pid}
         GROUP BY event_type"""


def q_scout_search(rnd):
    """Free-text scan of the scouting profiles.

    NO TEXT INDEX AT ALL in Lab 3 — no BM25, no trigram. This is a sequential
    scan across ~13,400 profiles of roughly 250 words each, which is a lot more
    bytes than the row count suggests. Included because it is the query most
    likely to make the instance's CPU visible in System Insights.
    """
    term = rnd.choice(["left-footed", "pressing", "set-piece", "loan",
                       "academy", "injury", "captain", "counter-attack"])
    return f"""
        SELECT count(*)
          FROM players
         WHERE profile_text ILIKE '%{term}%'"""


def q_league_table(rnd):
    """The dashboard aggregate. Big join, real work, no gimmick."""
    season = rnd.choice([2021, 2022, 2023, 2024])
    return f"""
        SELECT a.player_club_id, sum(a.goals) AS goals, count(*) AS apps
          FROM appearances a
          JOIN games g ON g.game_id = a.game_id
         WHERE g.season = {season}
         GROUP BY a.player_club_id
         ORDER BY goals DESC
         LIMIT 20"""


def q_heavy_rollup(rnd):
    """The long-runner. Its job is to still be running when someone looks.

    Task 4 reads the Active Queries view, and a view of actively running
    queries is empty unless something is actively running. Everything else in
    this catalogue finishes too fast to be caught. This one does not.
    """
    return """
        SELECT p.country_of_citizenship,
               count(DISTINCT a.player_id)  AS players,
               sum(a.goals)                 AS goals,
               sum(a.minutes_played)        AS minutes
          FROM appearances a
          JOIN players p ON p.player_id = a.player_id
          JOIN games   g ON g.game_id   = a.game_id
         WHERE a.appearance_date IS NOT NULL
         GROUP BY p.country_of_citizenship
         ORDER BY goals DESC NULLS LAST"""


# (tag, weight, builder).
#
# 🔴 REBALANCED 2026-08-21 against the first live run. The first cut's weights
# were guesses and two of them were badly wrong. Measured average execution
# time, from Query Insights over a ~20 minute run:
#
#     deadline-rollup  2,982.54 ms   (heavy lane)
#     scout-search       108.12 ms
#     league-table       106.57 ms
#     form-window         96.91 ms   <- seq scan on appearances, 13,525 calls
#     player-timeline      40.85 ms
#     transfer-ticker      7.53 ms   <- was weight 30. THIRTY.
#
# transfer-ticker was the single heaviest-weighted query in the mix and it
# costs 7.5 ms, because `transfers` is 11 MB and 65,494 rows — too small to
# hurt no matter how badly it is indexed. Nearly a third of the load was being
# spent on the cheapest statement in the catalogue.
#
# The weight now follows the cost. appearances (111 MB, 832,193 rows) is the
# only table in this corpus big enough to make a seq scan hurt, so the two
# queries that scan it carry the mix.
#
# ⚠️ transfer-ticker is NOT removed and its weight must not go to zero. It is
# still the query whose NAME tells the deadline-day story, and Task 2's lesson
# is partly that the query you assume is the problem is not always the one the
# data blames. A cheap statement that appears in the list is useful evidence.
CATALOGUE = [
    ("form-window",     32, q_form_window),
    ("squad-view",      20, q_squad_view),
    ("player-timeline", 20, q_player_timeline),
    ("contract-watch",  12, q_contract_watch),
    ("transfer-ticker",  8, q_transfer_ticker),
    ("scout-search",     5, q_scout_search),
    ("league-table",     3, q_league_table),
]

HEAVY = ("deadline-rollup", q_heavy_rollup)


def q_transfer_desk(rnd):
    """A transfer completing. THE ONLY WRITE IN THIS WORKLOAD.

    🔴 ADDED 2026-08-21, and it exists to fix a measured gap rather than to add
    realism for its own sake.

    The first live run's wait-event breakdown was: CPU 2.36 s, IPC 0.21 s,
    IO 0 s, LWLock 0 s, Client 0, Internal 0. Every drop of it CPU and the
    parallel-query plumbing behind Gather. That is because the workload was
    ENTIRELY READ-ONLY, and a read-only workload against a fully cached corpus
    can never produce a lock wait or an I/O wait.

    Task 4's teaching payload is the difference between "this query was slow"
    and "this query was slow BECAUSE it was waiting on something". With only
    CPU and IPC in the picture there is no second half of that sentence, and
    Task 4 collapses into Task 2.

    So: deadline day gets its actual defining event. A player changes clubs.

    WHY AN UPDATE ON `players` SPECIFICALLY. It is the widest table in the
    corpus — ~17 KB a row, because of the 3072-dimension embedding — so every
    row update writes a large new tuple and real WAL. That is the cheapest
    honest way to put I/O and lock waits into a database that otherwise fits
    entirely in RAM.

    It is also true to the story, which matters more than it sounds: a lab that
    manufactures contention with pg_sleep or an advisory lock is teaching the
    student about the workload generator. This one is teaching them about
    deadline day.

    ⚠️ IT MUTATES THE CORPUS, deliberately and reversibly. Only
    `current_club_id` moves, and only ever to a club that already exists, so no
    foreign key is violated and no row count changes. A fresh load restores the
    original state. Nothing in the lab asserts a specific player's club.
    """
    return """
        UPDATE players
           SET current_club_id = (
                   SELECT club_id FROM clubs
                    ORDER BY random() LIMIT 1)
         WHERE player_id = (
                   SELECT player_id FROM players
                    WHERE last_season >= 2024
                    ORDER BY random() LIMIT 1)"""


WRITES = ("transfer-desk", q_transfer_desk)

APP = "cymbalgoal-deadline-day"


def tagged(tag, sql):
    """sqlcommenter prefix.

    This is the shape sqlcommenter emits and the shape AlloyDB's TAGS view
    reads — verified end to end: these tags are what Query Insights' Tags tab
    and the Database Insights MCP server hand back as query labels. If the
    TAGS view ever comes up empty on another instance, check this prefix
    before the observability config.
    """
    return f"/*application='{APP}',controller='{tag}',framework='psycopg-lite'*/ {sql}"


# ---------------------------------------------------------------------------
def make_connect():
    """One Connector, one connection per worker.

    Cloud Shell (mkt015): public IP + IAM as the gcloud account, discovered with
    gcloud. The aldb005 setup VM: everything from CG_* variables, private IP, as
    the VM's service account with SET ROLE to the student, so every table it
    touches is the student's and Query Insights shows the student's role.
    Do not "simplify" to psql: Cloud Shell's egress IP is dynamic, which would
    force 0.0.0.0/0 on the instance's authorized networks.
    """
    import subprocess
    from google.cloud.alloydb.connector import Connector, IPTypes

    def sh(c):
        return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()

    uri = os.environ.get("CG_INSTANCE_URI", "")
    user = os.environ.get("CG_DB_USER") or sh("gcloud config get-value account")
    set_role = os.environ.get("CG_SET_ROLE", "")
    ip_type = IPTypes.PRIVATE if os.environ.get("CG_IP_TYPE", "PUBLIC").upper() == "PRIVATE" else IPTypes.PUBLIC

    if not uri:
        project = sh("gcloud config get-value project")
        clusters = json.loads(sh("gcloud alloydb clusters list --format=json") or "[]")
        if not clusters:
            sys.exit("FATAL: no AlloyDB cluster in this project.")
        name = clusters[0]["name"]
        cluster = name.split("/")[-1]
        region = name.split("/locations/")[1].split("/")[0]
        instances = json.loads(sh(
            f"gcloud alloydb instances list --cluster={cluster} --region={region} --format=json") or "[]")
        primary = [i for i in instances if i.get("instanceType") == "PRIMARY"][0]
        instance = primary["name"].split("/")[-1]
        uri = f"projects/{project}/locations/{region}/clusters/{cluster}/instances/{instance}"

    connector = Connector()

    def connect():
        c = connector.connect(uri, "pg8000", user=user, db=DB_NAME,
                              enable_iam_auth=True, ip_type=ip_type)
        c.autocommit = True
        if set_role:
            cur = c.cursor()
            cur.execute('SET ROLE "' + set_role.replace('"', '""') + '"')
            cur.close()
        return c

    return uri, (set_role or user), connect


def worker(wid, connect, catalogue, stop_at, results, lock, think_ms):
    rnd = random.Random(wid * 7919)
    tags = [t for t, w, _ in catalogue for _ in range(w)]
    conn = connect()
    while not os.path.exists(STOP) and time.time() < stop_at:
        tag = rnd.choice(tags)
        build = dict((t, f) for t, _, f in catalogue)[tag]
        sql = tagged(tag, build(rnd))
        t0 = time.time()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cur.fetchall()          # aggregates only — a handful of rows at most
            cur.close()
            ok = True
        except Exception as e:                                     # noqa: BLE001
            ok = False
            # A dropped connection mid-run is expected over a long workload.
            try:
                conn = connect()
            except Exception:                                      # noqa: BLE001
                time.sleep(1.0)
            sys.stderr.write(f"[w{wid}] {tag}: {type(e).__name__}: {e}\n")
        ms = (time.time() - t0) * 1000.0
        with lock:
            results.append({"t": time.time(), "tag": tag, "ms": ms, "ok": ok})
        if think_ms:
            time.sleep(rnd.uniform(0, think_ms) / 1000.0)


def heavy_worker(wid, connect, stop_at, results, lock, gap_s):
    rnd = random.Random(9999 + wid)
    tag, build = HEAVY
    conn = connect()
    while not os.path.exists(STOP) and time.time() < stop_at:
        sql = tagged(tag, build(rnd))
        t0 = time.time()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cur.fetchall()
            cur.close()
            ok = True
        except Exception as e:                                     # noqa: BLE001
            ok = False
            try:
                conn = connect()
            except Exception:                                      # noqa: BLE001
                time.sleep(1.0)
            sys.stderr.write(f"[h{wid}] {tag}: {type(e).__name__}: {e}\n")
        with lock:
            results.append({"t": time.time(), "tag": tag,
                            "ms": (time.time() - t0) * 1000.0, "ok": ok})
        time.sleep(gap_s)


def write_worker(wid, connect, stop_at, results, lock, gap_s):
    """The transfer desk. One connection, deliberately slow-handed.

    Kept to a low rate on purpose. The goal is lock and I/O waits appearing in
    the wait-event breakdown, not a write-saturated instance — a lab that
    hammers writes teaches nothing except that writes are expensive.
    """
    rnd = random.Random(4242 + wid)
    tag, build = WRITES
    conn = connect()
    while not os.path.exists(STOP) and time.time() < stop_at:
        sql = tagged(tag, build(rnd))
        t0 = time.time()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cur.close()
            ok = True
        except Exception as e:                                     # noqa: BLE001
            ok = False
            try:
                conn = connect()
            except Exception:                                      # noqa: BLE001
                time.sleep(1.0)
            sys.stderr.write(f"[wr{wid}] {tag}: {type(e).__name__}: {e}\n")
        with lock:
            results.append({"t": time.time(), "tag": tag,
                            "ms": (time.time() - t0) * 1000.0, "ok": ok})
        time.sleep(gap_s)


def flusher(results, lock, stop_at):
    os.makedirs(OUT, exist_ok=True)
    seen = 0
    while not os.path.exists(STOP) and time.time() < stop_at:
        time.sleep(10)
        with lock:
            batch = results[seen:]
            seen = len(results)
        if batch:
            with open(SAMPLES, "a") as fh:
                for r in batch:
                    fh.write(json.dumps(r) + "\n")
            summarize(batch, prefix="  last 10s ")


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def split_ok(batch):
    """Separate successful statements from failed ones, per tag.

    🔴 FIXED 2026-08-22. Every sample has carried an `ok` flag since the first
    cut, and NOTHING read it — so a statement that died on a dropped connection
    after 31 seconds went into the percentiles as though it were a 31-second
    QUERY. One brief connection blip produced `transfer-desk p95=31549` in a
    workload whose writes run in about 74 ms, which is not a slow write, it is
    a stopwatch left running on a broken socket.

    That mattered beyond looking alarming: a before/after `report` straddling a
    blip would show a fix making things worse. Latency stats now come from
    successful statements only, and failures are surfaced as their own count so
    they stay visible instead of being quietly dropped.
    """
    by = {}
    for r in batch:
        d = by.setdefault(r["tag"], {"ok": [], "err": 0})
        if r.get("ok", True):
            d["ok"].append(r["ms"])
        else:
            d["err"] += 1
    return by


def summarize(batch, prefix=""):
    by = split_ok(batch)
    line = []
    for tag in sorted(by):
        v, e = by[tag]["ok"], by[tag]["err"]
        part = (f"{tag} n={len(v)} p50={statistics.median(v):.0f} p95={pct(v,95):.0f}"
                if v else f"{tag} n=0")
        if e:
            part += f" err={e}"
        line.append(part)
    print(prefix + " | ".join(line), flush=True)


def _load_samples():
    """Read samples.jsonl, tolerating a torn or empty line.

    🔴 FIXED 2026-08-23, after `report --since 3` died with
    json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    in front of a student.

    The flusher appends a batch to this file every ten seconds while `report`
    or `status` may be reading it, so a read can catch a partially written
    line, or a trailing empty one. json.loads then raises, and a Python
    traceback in the middle of Task 5 reads as a broken lab rather than as a
    race on a log file.

    Skip anything that does not parse. One dropped sample out of hundreds of
    thousands moves no percentile, and a report that prints is worth vastly
    more than a report that is exactly right.
    """
    rows = []
    if not os.path.exists(SAMPLES):
        return rows
    with open(SAMPLES) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def status_report(window_s=60.0):
    """Is traffic flowing RIGHT NOW? Reads the samples file, not the log.

    ⚠️ The log at ~/cymbalgoal-workload.log is only written by the backgrounded
    `start` path. In the foreground `run` path — which is what the lab uses —
    output goes to the terminal and that log stays empty. Tailing it reported
    nothing and looked like a broken workload. The samples file is written by
    the flusher either way, so it is the honest source for this check.
    """
    rows = _load_samples()
    if not rows:
        print("  no samples yet — give it about ten seconds and re-run.")
        return
    now = time.time()
    age = now - rows[-1]["t"]
    print(f"  {len(rows):,} statements sampled so far, most recent {age:.0f}s ago")
    recent = [r for r in rows if now - r["t"] <= window_s]
    if not recent:
        print(f"  ⚠️  nothing in the last {window_s:.0f}s. The process is alive but"
              " not issuing queries — check the tab it is running in.")
        return
    summarize(recent, prefix=f"  last {window_s:.0f}s ")


def report(since_min=0.0):
    batch = _load_samples()
    if not batch:
        sys.exit(f"no samples yet at {SAMPLES} — is the generator running?")
    # ⚠️ samples.jsonl is APPEND-ONLY and survives restarts, so a bare report is
    # cumulative over everything ever run. That dilutes a before/after
    # comparison to the point of uselessness: apply an index at minute 40 and
    # the "after" report still carries forty minutes of "before" in it.
    # --since is what makes the before/after demonstration honest.
    if since_min > 0:
        cutoff = time.time() - since_min * 60.0
        batch = [r for r in batch if r["t"] >= cutoff]
        if not batch:
            sys.exit(f"no samples in the last {since_min:g} minutes")
        print(f"--- last {since_min:g} minutes only ---")
    print(f"{len(batch):,} samples over "
          f"{(batch[-1]['t'] - batch[0]['t'])/60:.1f} minutes\n")
    by = split_ok(batch)
    errs = sum(d["err"] for d in by.values())
    if errs:
        print(f"⚠️  {errs:,} statements failed (dropped connections). They are "
              f"counted below but excluded from the latency columns.\n")
    print(f"{'tag':20s} {'n':>7s} {'p50 ms':>9s} {'p95 ms':>9s} "
          f"{'p99 ms':>9s} {'max ms':>9s} {'err':>6s}")
    ranked = sorted((t for t in by if by[t]["ok"]),
                    key=lambda t: -statistics.median(by[t]["ok"]))
    for tag in ranked:
        v, e = by[tag]["ok"], by[tag]["err"]
        print(f"{tag:20s} {len(v):>7,} {statistics.median(v):>9.0f} "
              f"{pct(v,95):>9.0f} {pct(v,99):>9.0f} {max(v):>9.0f} {e:>6,}")
    for tag in sorted(t for t in by if not by[t]["ok"]):
        print(f"{tag:20s} {0:>7,} {'-':>9s} {'-':>9s} {'-':>9s} {'-':>9s} "
              f"{by[tag]['err']:>6,}")


def main():
    ap = argparse.ArgumentParser()
    # 🔴 DEFAULTS RAISED 2026-08-21, from a measured starting point rather than
    # a guess. The first cut ran 12 workers at a 250 ms think time and the
    # instance sat at roughly a third of capacity at peak — 45% P99 CPU on the
    # spike, and the corpus is fully cached so there is no I/O to wait on.
    # A database at one third of capacity is not an incident.
    #
    # Roughly 3x the concurrency and a quarter of the think time. Both are
    # overridable from the environment so the ceiling can be found without
    # editing this file: CG_WORKERS, CG_THINK_MS, CG_WRITERS.
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("CG_WORKERS", 32)),
                    help="routine-query connections.")
    # 🔴 RAISED 2026-08-21. Task 4's Active Queries view only shows what is
    # running AT THAT INSTANT. deadline-rollup takes ~3.7 s; at 2 workers on a
    # 15 s gap that is roughly a 40% duty cycle, and the view was measured
    # EMPTY of it on a live look — the student refreshes, sees nothing longer
    # than 0.38 s, and concludes the lab is broken.
    #
    # 3 workers on a 2 s gap puts the duty cycle above 95%, so a long-runner is
    # essentially always there to be caught in the act. It is still an honest
    # workload — a real deadline-day rollup would run continuously, not once a
    # quarter-minute.
    ap.add_argument("--heavy", type=int,
                    default=int(os.environ.get("CG_HEAVY", 3)),
                    help="long-runner connections. Task 4 needs one visible at all times.")
    ap.add_argument("--heavy-gap", type=float, default=2.0,
                    help="seconds between long-runner starts")
    ap.add_argument("--writers", type=int,
                    default=int(os.environ.get("CG_WRITERS", 2)),
                    help="transfer-desk write connections. 0 disables writes entirely.")
    ap.add_argument("--write-gap", type=float, default=2.0,
                    help="seconds between writes, per writer")
    ap.add_argument("--think-ms", type=float,
                    default=float(os.environ.get("CG_THINK_MS", 60)),
                    help="upper bound on per-worker pause. 0 is a stress test, not a workload.")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = until stopped")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--status", action="store_true",
                    help="is traffic flowing right now? reads the samples file.")
    ap.add_argument("--since", type=float, default=0.0,
                    help="with --report, only samples from the last N minutes. "
                         "Use this for before/after comparisons — a bare "
                         "--report is cumulative over every run.")
    args = ap.parse_args()

    if args.status:
        return status_report()

    if args.report:
        return report(args.since)

    if os.path.exists(STOP):
        os.remove(STOP)
    uri, user, connect = make_connect()

    # Re-anchor the date predicates on the real end of the corpus. If the data
    # is ever regenerated the constant above goes stale silently, and a stale
    # anchor does not error — it just quietly changes what every window query
    # matches, which is the worst kind of wrong in a workload generator.
    global ANCHOR
    try:
        c = connect()
        cur = c.cursor()
        cur.execute("SELECT max(appearance_date)::date::text FROM appearances")
        found = cur.fetchone()[0]
        cur.close()
        c.close()
        if found:
            if found != ANCHOR:
                print(f"anchor   {found}  (constant said {ANCHOR} — using the database)")
            ANCHOR = found
    except Exception as e:                                         # noqa: BLE001
        print(f"anchor   {ANCHOR}  (could not read from database: {e})")
    stop_at = time.time() + args.seconds if args.seconds else time.time() + 86400

    print(f"target   {uri}")
    print(f"as       {user}")
    print(f"workers  {args.workers} routine + {args.heavy} heavy + {args.writers} write")
    print(f"stop     touch {STOP}")
    print()

    results, lock, threads = [], threading.Lock(), []
    for i in range(args.workers):
        t = threading.Thread(target=worker, daemon=True,
                             args=(i, connect, CATALOGUE, stop_at, results, lock, args.think_ms))
        t.start()
        threads.append(t)
        time.sleep(0.15)          # stagger, so connection setup is not a thundering herd
    for i in range(args.heavy):
        t = threading.Thread(target=heavy_worker, daemon=True,
                             args=(i, connect, stop_at, results, lock, args.heavy_gap))
        t.start()
        threads.append(t)
    for i in range(args.writers):
        t = threading.Thread(target=write_worker, daemon=True,
                             args=(i, connect, stop_at, results, lock, args.write_gap))
        t.start()
        threads.append(t)
    f = threading.Thread(target=flusher, daemon=True, args=(results, lock, stop_at))
    f.start()

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        open(STOP, "w").close()
    print("\nstopped.")
    summarize(results, prefix="session  ")


if __name__ == "__main__":
    main()
