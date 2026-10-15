#!/usr/bin/env bash
# =============================================================================
# Skeleton run 2: one setup-VM module, cold start to "ready", measured.
# =============================================================================
# WHERE: Cloud Shell, signed in as the lab student, in a FRESH lab project
#        (nothing provisioned yet), from your clone of this repo.
# RUN:   bash build/skeleton/02-cold-start.sh aldb003
#
# What it does, end to end (~20-25 min, unattended):
#   1. applies the module (timed; this stands in for Start Lab), then applies
#      again and runs `plan -detailed-exitcode` (must be 0)
#   2. polls the setup VM's status line, the same one Task 0 prints, logging
#      every change against the clock started at step 1
#   3. waits for the VM to power itself off, then plans again (must be 0)
#   4. connects as YOU (the student) and tries the things only an owner can do
#   5. prints one HANDBACK block. Paste that back.
#
# Leaves everything running. The Terraform state is in ~/cg-cold-<module>.
#
# RE-RUN after a bootstrap fix, in the SAME project (git pull first):
#   RESUME=1 bash build/skeleton/02-cold-start.sh aldb003
# Copies the fixed module over the existing work folder (state kept), applies
# (which uploads the new bootstrap code), resets the setup VM so it runs again,
# and does everything from step 2 on. The clock starts at the reset, so read the
# VM's per-step times, not the cluster time; that comes from the first run.
# =============================================================================
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
MODULE="${1:-aldb003}"
W="$HOME/cg-cold-${MODULE}"
if [ "${RESUME:-}" = "1" ]; then
  [ -f "$W/terraform.tfstate" ] || { echo "RESUME=1 needs the earlier run's state in $W"; exit 1; }
  cp -r "${REPO}/terraform/." "$W/"
  rm -rf "$W"/bootstrap/workload/out
elif [ -f "$W/terraform.tfstate" ] && [ "${FORCE:-}" != "1" ]; then
  echo "$W already has state, so this would not be a cold start. Use a fresh project,"
  echo "or FORCE=1 to re-run against what is there (timings then mean nothing)."
  exit 1
fi
[ -f "$W/terraform.tfstate" ] || stage_module "$W" "${MODULE}"
cd "$W"
LOG="$W/run.log"
if [ "${RESUME:-}" = "1" ]; then printf '\n---- RESUME %s ----\n' "$(date -u +%H:%M:%S)" >> "${LOG}"; else : > "${LOG}"; fi
say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$1" | tee -a "${LOG}"; }
el() { local s=$(( $(date +%s) - T0 )); printf '%dm%02ds' $((s / 60)) $((s % 60)); }

python3 -m pip install -q --user "google-cloud-alloydb-connector[pg8000]" >/dev/null 2>&1 &
PIP=$!

terraform init -input=false -no-color > init.log 2>&1 || { tail -20 init.log; exit 1; }
T0=$(date +%s)
if [ "${RESUME:-}" = "1" ]; then
  say "resume: apply to upload the fixed bootstrap code"
  terraform apply -auto-approve -input=false -no-color > apply1.log 2>&1; A1=$?
  say "resume apply exit ${A1}; changed: $(grep -cE 'Modifications complete|Creation complete|Destruction complete' apply1.log) resources"
  if [ ${A1} -eq 0 ]; then
    gcloud storage rm --quiet "gs://${PROJECT}-cymbalgoal-staging/setup/status" >/dev/null 2>&1
    gcloud compute instances reset cymbalgoal-setup --zone="${ZONE}" --quiet >/dev/null 2>&1 \
      || gcloud compute instances start cymbalgoal-setup --zone="${ZONE}" --quiet >/dev/null 2>&1
    T0=$(date +%s)
    say "setup VM reset; clock restarted"
  fi
else
  say "apply 1 started (this is the Start Lab clock)"
  terraform apply -auto-approve -input=false -no-color > apply1.log 2>&1; A1=$?
fi
A1_T=$(el); say "apply 1 exit ${A1} at ${A1_T}"
if [ ${A1} -ne 0 ]; then grep -E "Error" -A8 apply1.log | head -60; fi
terraform apply -auto-approve -input=false -no-color > apply2.log 2>&1; A2=$?
say "apply 2 exit ${A2} at $(el)"
terraform plan -detailed-exitcode -input=false -no-color > plan1.log 2>&1; P1=$?
say "plan after two applies: exit ${P1}"
[ ${P1} -eq 0 ] || grep -E "^  [#~+-]|will be|must be" plan1.log | head -30 | tee -a "${LOG}"

BUCKET="gs://${PROJECT}-cymbalgoal-staging"
CHECK="$(terraform output -raw task0_check 2>/dev/null)"
HAS_VM="$(terraform output -raw setup_vm 2>/dev/null)"
LAST=""; FINAL=""
if [ ${A1} -ne 0 ]; then
  say "apply 1 failed, so not polling the setup VM"
elif [ -n "${HAS_VM}" ]; then
  say "polling ${BUCKET}/setup/status"
  for i in $(seq 1 160); do
    S="$(gcloud storage cat "${BUCKET}/setup/status" 2>/dev/null | head -1)"
    if [ -n "$S" ] && [ "$S" != "$LAST" ]; then say "[$(el)] $S"; LAST="$S"; fi
    case "$S" in ready:*|FAILED*) FINAL="$S"; break ;; esac
    sleep 15
  done
  READY_T="$(el)"
  say "status settled at ${READY_T}: ${FINAL:-timed out after 40 min}"
  T0_CHECK="$(bash -c "${CHECK}")"
  if [ "${MODULE}" != "aldb005" ] && [[ "${FINAL}" == ready:* ]]; then
    for i in $(seq 1 24); do
      VS="$(gcloud compute instances describe cymbalgoal-setup --zone="${ZONE}" --format='value(status)' 2>/dev/null)"
      [ "$VS" = "TERMINATED" ] && break; sleep 10
    done
    say "VM status: ${VS}"
  fi
  terraform plan -detailed-exitcode -input=false -no-color > plan2.log 2>&1; P2=$?
  say "plan after the VM finished: exit ${P2}"
  [ ${P2} -eq 0 ] || grep -E "^  [#~+-]|will be|must be" plan2.log | head -30 | tee -a "${LOG}"
  gcloud storage cat "${BUCKET}/setup/handback.json" > vm-handback.json 2>/dev/null
  gcloud storage cat "${BUCKET}/setup/bootstrap.log" > vm-bootstrap.log 2>/dev/null
fi

# ---- Ownership, from the student's side -------------------------------------
wait ${PIP}
INSTANCE_PATH="$(terraform output -raw instance_path)"
python3 - "${INSTANCE_PATH}" "${ACCOUNT}" > ownership.txt 2>&1 <<'PYEOF'
import sys
from google.cloud.alloydb.connector import Connector, IPTypes
uri, user = sys.argv[1], sys.argv[2]
conn = Connector()
def connect(db):
    c = conn.connect(uri, "pg8000", user=user, db=db, enable_iam_auth=True, ip_type=IPTypes.PUBLIC)
    c.autocommit = True
    return c
def one(c, sql):
    cur = c.cursor(); cur.execute(sql); r = cur.fetchall() if cur.description else []; cur.close()
    return r[0][0] if r else None
try:
    c = connect("cymbalgoal")
except Exception as e:
    print(f"connect to cymbalgoal as {user}: FAIL {type(e).__name__}: {str(e)[:200]}")
    sys.exit(0)
print(f"current_user: {one(c, 'SELECT current_user')}")
print(f"database owner: {one(c, 'SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()')}")
has_reports = one(c, "SELECT to_regclass('public.scout_reports') IS NOT NULL")
tests = [
    ("ALTER DATABASE ... SET, then RESET", ["ALTER DATABASE cymbalgoal SET work_mem = '8MB'", "ALTER DATABASE cymbalgoal RESET work_mem"]),
    ("COMMENT ON DATABASE", ["COMMENT ON DATABASE cymbalgoal IS 'ownership probe'", "COMMENT ON DATABASE cymbalgoal IS NULL"]),
    ("ALTER TABLE players OWNER TO current_user", ["ALTER TABLE players OWNER TO CURRENT_USER"]),
    ("ALTER TABLE ... RENAME and back", ["ALTER TABLE competitions RENAME TO competitions_probe", "ALTER TABLE competitions_probe RENAME TO competitions"]),
    ("CREATE INDEX and DROP INDEX on clubs", ["CREATE INDEX probe_idx ON clubs (club_id)", "DROP INDEX probe_idx"]),
    ("REINDEX TABLE competitions", ["REINDEX TABLE competitions"]),
    ("VACUUM ANALYZE competitions", ["VACUUM ANALYZE competitions"]),
    ("GRANT and REVOKE on players", ["GRANT SELECT ON players TO PUBLIC", "REVOKE SELECT ON players FROM PUBLIC"]),
    ("CREATE TABLE and DROP TABLE", ["CREATE TABLE ownership_probe (x int)", "DROP TABLE ownership_probe"]),
    ("CREATE DATABASE and DROP DATABASE (probe db)", ["CREATE DATABASE ownership_probe_db", "DROP DATABASE ownership_probe_db"]),
]
if has_reports:
    tests.append(("ALTER TABLE scout_reports SET (fillfactor), RESET", ["ALTER TABLE scout_reports SET (fillfactor = 90)", "ALTER TABLE scout_reports RESET (fillfactor)"]))
for name, stmts in tests:
    try:
        for s in stmts:
            cur = c.cursor(); cur.execute(s); cur.close()
        print(f"PASS {name}")
    except Exception as e:
        print(f"FAIL {name}: {str(e)[:200]}")
cur = c.cursor()
cur.execute("SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname")
print("rows: " + ", ".join(f"{r[0]} {r[1]:,}" for r in cur.fetchall()))
PYEOF

# ---- Handback ---------------------------------------------------------------
{
  echo "================================================================"
  echo "SKELETON RUN 2: ${MODULE} COLD START"
  echo "project ${PROJECT}; region ${REGION}; zone ${ZONE}; $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "================================================================"
  echo "## Clock (from apply 1 start)"
  cat "${LOG}"
  echo
  echo "## Apply 1 errors"
  if [ ${A1} -ne 0 ]; then grep -E "Error" -A8 apply1.log | head -40; else echo "none"; fi
  echo "## Resource create times (apply 1)"
  grep -E "Creation complete after" apply1.log | sed -E 's/^(.*): Creation complete after ([^ ]*).*$/\2  \1/' 
  echo
  echo "## Task 0 check output"
  echo "${T0_CHECK:-<no setup VM>}"
  echo
  echo "## VM handback.json"
  if [ -s vm-handback.json ]; then python3 -c 'import json;print(json.dumps(json.load(open("vm-handback.json")),indent=1)[:9000])'; else echo "none"; fi
  echo
  echo "## Ownership from the student side"
  cat ownership.txt
  if [[ "${FINAL:-}" == FAILED* ]]; then
    echo; echo "## Bootstrap log tail"; tail -60 vm-bootstrap.log 2>/dev/null
  fi
  echo "================================================================"
} > handback.txt
echo; cat handback.txt
