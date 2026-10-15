#!/usr/bin/env bash
# =============================================================================
# Module 1 checks, step 4 of 4: what a student does from Cloud Shell   (Cloud Shell)
# =============================================================================
# Runs after step 3 left the DMS job in CDC. Four parts, each recorded:
#
#   A. Assessment: can the Database Migration Assessment collector for SQL Server
#      run from Cloud Shell against Cloud SQL (through the Cloud SQL Auth Proxy)?
#   B. Auth Proxies: Cloud SQL Auth Proxy for SQL Server, AlloyDB Auth Proxy for
#      AlloyDB (password login on 5432, the student's IAM login on 5433), plus
#      connect and query timings through the proxy (Task 5 compares these with the
#      Python connector numbers from step 3).
#   C. DVT: google-pso-data-validator, SQL Server -> AlloyDB, all eight tables:
#      row counts, column aggregates, and row hashes; installs and runtime timed.
#   D. Promote the migration job and time it; then check the identity sequences
#      on the promoted database.   (PROMOTE=0 to skip.)
#
#   cd ~/cymbalgoal-alloydb-workshop && bash build/checks/module1/step4-cloudshell-checks.sh
#
# About 20-40 minutes. The handback prints at the end and is saved to
# ~/module1-step4/step4-handback.txt and to the workshop bucket.
# =============================================================================
set -uo pipefail

PROMOTE="${PROMOTE:-1}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE="cymbalgoal-legacy-sql"
DB_SOURCE="CymbalGoalScouting"
CLUSTER="cymbalgoal-cluster"
PRIMARY="cymbalgoal-primary"
DMS_DB="cymbalgoal_scouting"
DMS_USER="cymbalgoal_scouting"
JOB="cg-scouting-migration"
RESULTS="gs://class-demo/alloydb-labs/cymbalgoal-alloydb-workshop/module1-checks"
TABLES=(competitions clubs players games appearances game_events player_valuations transfers)
declare -A PK=([competitions]=competition_id [clubs]=club_id [players]=player_id [games]=game_id
               [appearances]=appearance_id [game_events]=game_event_id [player_valuations]=valuation_id
               [transfers]=transfer_id)

WORKDIR="$HOME/module1-step4"
mkdir -p "$WORKDIR/bin"
cd "$WORKDIR" || exit 1
export PATH="$WORKDIR/bin:$HOME/.local/bin:$PATH"
HB="$WORKDIR/step4-handback.txt"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
: > "$HB"

PROJECT="$(gcloud config get-value project 2>/dev/null)"
ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
CONN_NAME="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"
PRIMARY_URI="projects/$PROJECT/locations/$REGION/clusters/$CLUSTER/instances/$PRIMARY"
SQL_PW="$(gcloud secrets versions access latest --secret=cymbalgoal-sqlserver-password)"
DMS_PW="$(gcloud secrets versions access latest --secret=cymbalgoal-dms-password)"

hb()      { printf '%s\n' "$*" | tee -a "$HB"; }
section() { printf '\n## %s\n' "$*" | tee -a "$HB"; }
now()     { date +%s; }
secs()    { printf '%ss' $(( $(now) - $1 )); }

printf '%s\n' "==============================================================================" >> "$HB"
hb "MODULE 1 CHECKS: STEP 4 CLOUD SHELL HANDBACK   run $RUN_ID"
hb "project $PROJECT  account $ACCOUNT"
. /etc/os-release
hb "Cloud Shell: $PRETTY_NAME, $(python3 --version 2>&1), $(nproc) vCPU"

latest_tag() {  # GitHub's latest release tag, or the fallback given
  local t
  t="$(curl -fsSL "https://api.github.com/repos/$1/releases/latest" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null)"
  printf '%s' "${t:-$2}"
}

# =============================================================================
section "B. Auth Proxies"
# =============================================================================
T=$(now)
if ! command -v cloud-sql-proxy >/dev/null; then
  TAG="$(latest_tag GoogleCloudPlatform/cloud-sql-proxy v2.14.1)"
  curl -fsSL "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/${TAG}/cloud-sql-proxy.linux.amd64" -o bin/cloud-sql-proxy \
    && chmod +x bin/cloud-sql-proxy
fi
if ! command -v alloydb-auth-proxy >/dev/null; then
  TAG="$(latest_tag GoogleCloudPlatform/alloydb-auth-proxy v1.12.0)"
  curl -fsSL "https://storage.googleapis.com/alloydb-auth-proxy/${TAG}/alloydb-auth-proxy.linux.amd64" -o bin/alloydb-auth-proxy \
    && chmod +x bin/alloydb-auth-proxy
fi
hb "proxies: $(cloud-sql-proxy --version 2>&1 | head -1); $(alloydb-auth-proxy --version 2>&1 | head -1) (install $(secs "$T"))"

pkill -f 'cloud-sql-proxy|alloydb-auth-proxy' 2>/dev/null; sleep 1
cloud-sql-proxy --port 1433 "$CONN_NAME" > proxy-sql.log 2>&1 &
alloydb-auth-proxy --public-ip --port 5432 "$PRIMARY_URI" > proxy-alloydb.log 2>&1 &
alloydb-auth-proxy --public-ip --auto-iam-authn --port 5433 "$PRIMARY_URI" > proxy-alloydb-iam.log 2>&1 &
sleep 6

export PGHOST=127.0.0.1 PGDATABASE="$DMS_DB" PGSSLMODE=disable
T=$(now)
if PGPASSWORD="$DMS_PW" psql -p 5432 -U "$DMS_USER" -Atc 'select 1' >/dev/null 2>psql-err.txt; then
  hb "AlloyDB via Auth Proxy, password login: OK"
else
  hb "AlloyDB via Auth Proxy, password login: FAILED $(tail -c 200 psql-err.txt)"
fi
# The student's IAM login through the proxy; timings for the Task 5 comparison
SCHEMA="$(PGPASSWORD="$DMS_PW" psql -p 5432 -U "$DMS_USER" -Atc "select schemaname from pg_tables where tablename='players' limit 1")"
SCHEMA="${SCHEMA:-dbo}"
hb "migrated schema: $SCHEMA"
TOP_SQL="select p.player_name, sum(a.goals) g from \"$SCHEMA\".appearances a join \"$SCHEMA\".games gm on gm.game_id=a.game_id join \"$SCHEMA\".players p on p.player_id=a.player_id where gm.competition_id='GB1' and gm.season=2023 group by p.player_name order by g desc limit 10"
python3 - "$ACCOUNT" "$TOP_SQL" <<'PY' | tee -a "$HB"
import statistics, subprocess, sys, time
user, sql = sys.argv[1], sys.argv[2]
t0 = time.time()
r = subprocess.run(["psql", "-p", "5433", "-U", user, "-Atc", "select pg_is_in_recovery()"], capture_output=True, text=True)
connect = time.time() - t0
if r.returncode:
    print(f"student IAM login via Auth Proxy (--auto-iam-authn): FAILED {r.stderr.strip()[-200:]}")
    sys.exit()
runs = []
for _ in range(7):
    t = time.time()
    subprocess.run(["psql", "-p", "5433", "-U", user, "-Atc", sql], capture_output=True, text=True)
    runs.append((time.time() - t) * 1000)
print(f"student IAM login via Auth Proxy: OK; psql connect+query {connect:.2f} s; "
      f"top-scorers via proxy median {statistics.median(runs):.0f} ms per psql call (includes psql start-up)")
PY

# =============================================================================
section "A. Assessment collector (Database Migration Assessment) from Cloud Shell"
# =============================================================================
hb "tools present: pwsh=$(command -v pwsh >/dev/null && echo yes || echo no), sqlcmd=$(command -v sqlcmd >/dev/null && echo yes || echo no), bcp=$(command -v bcp >/dev/null && echo yes || echo no)"
curl -fsSL https://api.github.com/repos/GoogleCloudPlatform/database-assessment/releases/latest -o dma-release.json 2>/dev/null
python3 - dma-release.json <<'PY' | tee -a "$HB"
import json, sys
try:
    j = json.load(open(sys.argv[1]))
except Exception:
    j = {}
assets = [a["name"] for a in j.get("assets", [])]
print(f"latest DMA release: {j.get('tag_name', '?')} ({j.get('published_at', '?')}); assets: {', '.join(assets) or 'none listed'}")
PY
ASSET_URL="$(python3 - dma-release.json <<'PY'
import json, sys
try:
    j = json.load(open(sys.argv[1]))
except Exception:
    sys.exit()
for a in j.get("assets", []):
    n = a["name"].lower()
    if "sqlserver" in n or "mssql" in n:
        print(a["browser_download_url"])
        break
PY
)"
if [[ -n "$ASSET_URL" ]]; then
  rm -rf dma && mkdir -p dma && curl -fsSL "$ASSET_URL" -o dma/collector.zip && (cd dma && unzip -oq collector.zip)
  hb "collector: $(basename "$ASSET_URL"), files: $(cd dma && find . -maxdepth 2 -type f | grep -v collector.zip | head -12 | tr '\n' ' ')"
  COLLECTOR="$(find dma -iname '*.ps1' | head -1)"
  if [[ -n "$COLLECTOR" ]]; then
    hb "entry script $COLLECTOR, parameters:"
    grep -iE '^\s*\[?Parameter|^\s*\[string\]|^\s*\[int\]|^\s*\$[a-zA-Z]+\s*=|param\s*\(' "$COLLECTOR" | head -20 | sed 's/^/    /' | tee -a "$HB" >/dev/null
    if command -v pwsh >/dev/null; then
      T=$(now)
      (cd "$(dirname "$COLLECTOR")" && timeout 300 pwsh -NoProfile -File "$(basename "$COLLECTOR")" -serverName 127.0.0.1 -port 1433 \
          -collectionUserName sqlserver -collectionUserPass "$SQL_PW" > "$WORKDIR/dma-run.log" 2>&1)
      RC=$?
      hb "pwsh run against the proxy: exit $RC in $(secs "$T"); last lines:"
      tail -8 "$WORKDIR/dma-run.log" | cut -c1-200 | sed 's/^/    /' | tee -a "$HB" >/dev/null
      hb "output zips: $(find dma -newer dma/collector.zip -name '*.zip' | tr '\n' ' ')"
    else
      hb "pwsh is not installed in Cloud Shell, so the PowerShell collector cannot run here"
    fi
  else
    hb "no .ps1 in the collector archive; README head:"
    find dma -iname 'readme*' | head -1 | xargs -r head -25 | sed 's/^/    /' | tee -a "$HB" >/dev/null
  fi
else
  hb "no SQL Server collector asset found in the latest release"
fi

# =============================================================================
section "C. DVT: SQL Server -> AlloyDB, eight tables"
# =============================================================================
T=$(now)
if ! odbcinst -q -d 2>/dev/null | grep -q 'ODBC Driver 18'; then
  curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor --yes -o /usr/share/keyrings/microsoft-prod.gpg
  curl -fsSL "https://packages.microsoft.com/config/${ID}/${VERSION_ID}/prod.list" | sudo tee /etc/apt/sources.list.d/mssql-release.list >/dev/null
  sudo apt-get update -qq >/dev/null 2>&1
  sudo ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 unixodbc-dev > odbc-install.log 2>&1 || tail -5 odbc-install.log
fi
hb "ODBC Driver 18: $(odbcinst -q -d 2>/dev/null | grep -c 'ODBC Driver 18') installed ($(secs "$T"); from packages.microsoft.com/config/${ID}/${VERSION_ID})"

T=$(now)
python3 -m venv "$WORKDIR/dvt-venv" && . "$WORKDIR/dvt-venv/bin/activate"
pip install -q --upgrade pip > dvt-install.log 2>&1
pip install -q google-pso-data-validator pyodbc >> dvt-install.log 2>&1; RC=$?
hb "pip install google-pso-data-validator pyodbc: exit $RC in $(secs "$T"); DVT $(pip show google-pso-data-validator 2>/dev/null | awk '/^Version/{print $2}')"
[[ $RC -ne 0 ]] && tail -6 dvt-install.log | sed 's/^/    /' | tee -a "$HB" >/dev/null

if command -v data-validation >/dev/null; then
  export PSO_DV_CONFIG_HOME="$WORKDIR/dvt-config"
  data-validation connections add --connection-name cg_sqlserver MSSQL --host 127.0.0.1 --port 1433 \
    --user sqlserver --password "$SQL_PW" --database "$DB_SOURCE" \
    --query '{"driver": "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes", "Encrypt": "no"}' > dvt-conn.log 2>&1
  data-validation connections add --connection-name cg_alloydb Postgres --host 127.0.0.1 --port 5432 \
    --user "$DMS_USER" --password "$DMS_PW" --database "$DMS_DB" >> dvt-conn.log 2>&1
  hb "connections: $(data-validation connections list 2>&1 | grep -ciE 'cg_sqlserver|cg_alloydb')/2 registered"

  T=$(now)
  FIND="$(data-validation find-tables --source-conn cg_sqlserver --target-conn cg_alloydb --allowed-schemas dbo 2>find-err.log)"
  hb "find-tables: $(secs "$T"); $(printf '%s' "$FIND" | python3 -c 'import json,sys
try: print(len(json.load(sys.stdin)), "matches")
except Exception: print("no JSON output")' 2>/dev/null) $(tail -c 160 find-err.log | tr '\n' ' ')"
  LIST=""
  for t in "${TABLES[@]}"; do LIST+="dbo.$t=$SCHEMA.$t,"; done
  LIST="${LIST%,}"

  summarize() {  # $1 csv file -> "N success, M fail" plus failing rows
    python3 - "$1" <<'PY'
import csv, collections, sys
try:
    rows = list(csv.DictReader(open(sys.argv[1])))
except Exception as e:
    print(f"no result file ({e})"); sys.exit()
st = collections.Counter(r.get("validation_status", "?") for r in rows)
print(", ".join(f"{v} {k}" for k, v in st.items()) or "no rows")
for r in rows:
    if r.get("validation_status") not in ("success",):
        keys = [k for k in ("source_table_name", "source_column_name", "validation_name", "source_agg_value",
                            "target_agg_value", "primary_keys", "pct_difference") if r.get(k)]
        print("    FAIL " + "; ".join(f"{k}={r[k][:60]}" for k in keys))
PY
  }

  T=$(now)
  data-validation validate column -sc cg_sqlserver -tc cg_alloydb -tbls "$LIST" --format csv > dvt-count.csv 2> dvt-count.err
  hb "row counts (validate column, count): $(secs "$T"): $(summarize dvt-count.csv | head -1)"
  summarize dvt-count.csv | tail -n +2 | head -10 | tee -a "$HB" >/dev/null
  [[ -s dvt-count.err ]] && tail -3 dvt-count.err | cut -c1-220 | sed 's/^/    /' | tee -a "$HB" >/dev/null

  T=$(now)
  data-validation validate column -sc cg_sqlserver -tc cg_alloydb -tbls "$LIST" --sum '*' --min '*' --max '*' \
    --format csv > dvt-agg.csv 2> dvt-agg.err
  hb "column aggregates (sum/min/max of every numeric column): $(secs "$T"): $(summarize dvt-agg.csv | head -1)"
  summarize dvt-agg.csv | tail -n +2 | head -15 | tee -a "$HB" >/dev/null
  [[ -s dvt-agg.err ]] && tail -3 dvt-agg.err | cut -c1-220 | sed 's/^/    /' | tee -a "$HB" >/dev/null

  hb "row hashes (validate row --hash '*'), per table:"
  TH=$(now)
  for t in "${TABLES[@]}"; do
    T=$(now)
    data-validation validate row -sc cg_sqlserver -tc cg_alloydb -tbls "dbo.$t=$SCHEMA.$t" \
      --primary-keys "${PK[$t]}" --hash '*' --format csv > "dvt-hash-$t.csv" 2> "dvt-hash-$t.err"
    hb "  $t: $(secs "$T"): $(summarize "dvt-hash-$t.csv" | head -1)"
    summarize "dvt-hash-$t.csv" | tail -n +2 | head -3 | tee -a "$HB" >/dev/null
    [[ ! -s "dvt-hash-$t.csv" ]] && tail -2 "dvt-hash-$t.err" | cut -c1-220 | sed 's/^/      /' | tee -a "$HB" >/dev/null
  done
  hb "row hashes, all tables: $(secs "$TH")"
  deactivate
else
  hb "data-validation is not available, so the DVT checks were skipped"
fi

# =============================================================================
section "D. Promote the migration job"
# =============================================================================
if [[ "$PROMOTE" == "1" ]]; then
  hb "before: $(gcloud database-migration migration-jobs describe "$JOB" --region="$REGION" --format='value(state,phase)' 2>&1 | tr '\t' ' ')"
  T=$(now)
  gcloud database-migration migration-jobs promote "$JOB" --region="$REGION" --quiet > promote.log 2>&1
  hb "promote call: exit $? $(tail -c 160 promote.log | tr '\n' ' ')"
  LAST=""
  for _ in $(seq 1 180); do
    S="$(gcloud database-migration migration-jobs describe "$JOB" --region="$REGION" --format='value(state,phase)' 2>/dev/null | tr '\t' ' ')"
    [[ "$S" != "$LAST" ]] && hb "  +$(secs "$T") $S" && LAST="$S"
    [[ "$S" == COMPLETED* || "$S" == FAILED* ]] && break
    sleep 10
  done
  hb "promotion: $(secs "$T") to '$LAST'"
  # Identity sequences after promotion: do new rows collide with migrated ones?
  for pair in transfers:transfer_id player_valuations:valuation_id; do
    tb="${pair%%:*}"; col="${pair##*:}"
    OUT="$(PGPASSWORD="$DMS_PW" psql -p 5432 -U "$DMS_USER" -At <<SQL 2>&1
\set ON_ERROR_STOP off
select coalesce(pg_get_serial_sequence('"$SCHEMA".$tb', '$col'), 'none');
select max($col) from "$SCHEMA".$tb;
begin;
insert into "$SCHEMA".$tb (player_id, $( [[ $tb == transfers ]] && printf 'transfer_date, to_club_name' || printf 'valuation_date')) values (28003, '2099-08-01'$( [[ $tb == transfers ]] && printf ", 'probe'")) returning $col;
rollback;
SQL
)"
    hb "  $tb.$col after promotion: $(printf '%s' "$OUT" | tr '\n' ' ' | cut -c1-260)"
  done
else
  hb "skipped (PROMOTE=0)"
fi

pkill -f 'cloud-sql-proxy|alloydb-auth-proxy' 2>/dev/null
printf '%s\n' "==============================================================================" >> "$HB"
printf '\n\n'; cat "$HB"
if gcloud storage cp "$HB" "$RESULTS/step4-handback-$RUN_ID.txt" >/dev/null 2>&1; then
  printf '(saved to %s/)\n' "$RESULTS"
else
  printf '(not saved to the bucket; local copy %s)\n' "$HB"
fi
