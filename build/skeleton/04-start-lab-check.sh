#!/usr/bin/env bash
# =============================================================================
# Skeleton run 4: after clicking Start Lab on a thin-root lab, is it right?
# =============================================================================
# WHERE: Cloud Shell in the lab's own project, signed in as the lab student.
# RUN:   curl -fsSL https://raw.githubusercontent.com/haggman/cymbalgoal-alloydb-workshop/main/build/skeleton/04-start-lab-check.sh | bash
#        (or from a clone: bash build/skeleton/04-start-lab-check.sh)
#
# Reports what Start Lab built, the Task 0 status line, and a student login
# through the Python Connector (the same call a Colab notebook makes).
# Paste back the HANDBACK block.
# =============================================================================
set -uo pipefail
PROJECT="$(gcloud config get-value project 2>/dev/null)"
ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
python3 -m pip install -q --user "google-cloud-alloydb-connector[pg8000]" >/dev/null 2>&1
{
  echo "================================================================"
  echo "SKELETON RUN 4: START LAB CHECK"
  echo "project ${PROJECT}; $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "================================================================"
  echo "## AlloyDB"
  gcloud alloydb clusters list --format="table(name.basename(),state,databaseVersion,createTime)" 2>&1
  gcloud alloydb instances list --cluster=cymbalgoal-cluster --region=us-central1 \
    --format="table(name.basename(),instanceType,availabilityType,state,machineConfig.cpuCount,readPoolConfig.nodeCount,createTime)" 2>&1
  echo "## Setup VM"
  gcloud compute instances list --filter="name=cymbalgoal-setup" --format="table(name,status,creationTimestamp)" 2>&1
  echo "## Task 0 check"
  gcloud storage cat "gs://${PROJECT}-cymbalgoal-staging/setup/status" 2>/dev/null || echo "starting: the setup VM has not reported yet (or this lab has no setup VM)"
  echo "## Student login"
  python3 - "${PROJECT}" "${ACCOUNT}" <<'PYEOF'
import sys
from google.cloud.alloydb.connector import Connector, IPTypes
project, user = sys.argv[1], sys.argv[2]
uri = f"projects/{project}/locations/us-central1/clusters/cymbalgoal-cluster/instances/cymbalgoal-primary"
conn = Connector()
for db in ("postgres", "cymbalgoal"):
    try:
        c = conn.connect(uri, "pg8000", user=user, db=db, enable_iam_auth=True, ip_type=IPTypes.PUBLIC)
        cur = c.cursor()
        cur.execute("SELECT current_user, version(), pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
        u, v, o = cur.fetchone()
        print(f"{db}: connected as {u}; owner {o}; {v.split(' on ')[0]}")
        c.close()
    except Exception as e:
        print(f"{db}: {type(e).__name__}: {str(e)[:160]}")
PYEOF
  echo "================================================================"
} 2>&1
