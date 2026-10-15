#!/bin/bash
# =============================================================================
# CymbalGoal setup VM: startup script. Runs as root on EVERY boot.
# =============================================================================
# Settings come from the `cg-config` metadata attribute (setup_vm.tf). The code
# comes from the staging bucket, where Terraform uploaded this module's
# bootstrap/ folder. Progress goes to gs://<bucket>/setup/status, one line,
# which is what the student's Task 0 check prints.
#
#   first boot, success  -> marker written; power off (aldb005: start workload)
#   first boot, failure  -> FAILED status, log uploaded, VM LEFT RUNNING
#   any later boot       -> marker present: power off (aldb005: workload resumes)
# =============================================================================
set -uo pipefail

MD="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
CFG="$(curl -sf -H 'Metadata-Flavor: Google' "${MD}/cg-config")"
cfg() {
  python3 -c 'import json,sys; v=json.loads(sys.argv[1]).get(sys.argv[2], ""); print(v if isinstance(v, str) else json.dumps(v))' "${CFG}" "$1"
}
BUCKET="$(cfg bucket)"
MODULE="$(cfg module)"
LOADGEN="$(cfg load_generator)"

BASE=/opt/cymbalgoal
DONE="${BASE}/bootstrap.done"
LOG=/var/log/cymbalgoal-bootstrap.log
STATUS_URI="gs://${BUCKET}/setup/status"
mkdir -p "${BASE}"

status() {
  printf '%s\n' "$1" > "${BASE}/status.txt"
  gcloud storage cp --quiet "${BASE}/status.txt" "${STATUS_URI}" >/dev/null 2>&1 || true
}
upload_log() {
  gcloud storage cp --quiet "${LOG}" "gs://${BUCKET}/setup/bootstrap.log" >/dev/null 2>&1 || true
}
note() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$1" >> "${LOG}"; }

# ---- Later boots ------------------------------------------------------------
if [ -f "${DONE}" ]; then
  if [ "${LOADGEN}" = "true" ]; then
    systemctl start cymbalgoal-workload.service   # enabled, so it is already starting
    exit 0
  fi
  poweroff
  exit 0
fi

note "boot: ${MODULE}, uptime $(cut -d' ' -f1 /proc/uptime)s"
status "starting: setup VM booted, installing packages (${MODULE})"

# ---- Packages ---------------------------------------------------------------
# A fresh Debian image may still hold the apt lock (unattended upgrades), so
# retry rather than fail. postgresql-client brings pgbench and psql for aldb005.
PKGS="python3-venv"
[ "${LOADGEN}" = "true" ] && PKGS="${PKGS} postgresql-client"
ok=0
for i in 1 2 3 4 5 6; do
  if DEBIAN_FRONTEND=noninteractive apt-get update -qq >> "${LOG}" 2>&1 \
     && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ${PKGS} >> "${LOG}" 2>&1; then
    ok=1; break
  fi
  note "apt attempt ${i} failed; retrying in 15s"
  sleep 15
done
if [ "${ok}" != "1" ]; then
  status "FAILED at packages: apt-get could not install ${PKGS}. Log: gs://${BUCKET}/setup/bootstrap.log"
  upload_log; exit 1
fi

if [ ! -x "${BASE}/venv/bin/python" ]; then
  python3 -m venv "${BASE}/venv" >> "${LOG}" 2>&1
fi
if ! "${BASE}/venv/bin/pip" install -q --upgrade \
      "google-cloud-alloydb-connector[pg8000]" google-auth requests >> "${LOG}" 2>&1; then
  status "FAILED at packages: pip install failed. Log: gs://${BUCKET}/setup/bootstrap.log"
  upload_log; exit 1
fi
note "packages installed"

# ---- Code -------------------------------------------------------------------
rm -rf "${BASE}/code"
if ! gcloud storage cp -r "gs://${BUCKET}/setup/code" "${BASE}/" >> "${LOG}" 2>&1; then
  status "FAILED at code: could not copy gs://${BUCKET}/setup/code. Log: gs://${BUCKET}/setup/bootstrap.log"
  upload_log; exit 1
fi

# ---- Bootstrap --------------------------------------------------------------
# bootstrap.py writes the status line itself from here on, including FAILED.
"${BASE}/venv/bin/python" -u "${BASE}/code/bootstrap.py" >> "${LOG}" 2>&1
rc=$?
upload_log
if [ "${rc}" != "0" ]; then
  note "bootstrap exited ${rc}; leaving the VM running for inspection"
  upload_log
  exit "${rc}"
fi
touch "${DONE}"

# ---- aldb005: the deadline-day workload stays up ----------------------------
if [ "${LOADGEN}" = "true" ]; then
  install -m 0644 "${BASE}/code/workload/cymbalgoal-workload.service" /etc/systemd/system/
  python3 - "${CFG}" > /etc/default/cymbalgoal-workload <<'PYEOF'
import json, sys
c = json.loads(sys.argv[1])
print(f"CG_INSTANCE_URI=projects/{c['project']}/locations/{c['region']}/clusters/{c['cluster']}/instances/{c['instance']}")
print(f"CG_DB_USER={c['setup_db_user']}")
print(f"CG_SET_ROLE={c['student']}")
print(f"CG_DB={c['database']}")
print("CG_IP_TYPE=PRIVATE")
PYEOF
  systemctl daemon-reload
  systemctl enable --now cymbalgoal-workload.service >> "${LOG}" 2>&1
  note "workload service started"
  upload_log
  exit 0
fi

note "powering off"
upload_log
poweroff
