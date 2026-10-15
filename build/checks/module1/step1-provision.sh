#!/usr/bin/env bash
# =============================================================================
# Module 1 checks, step 1 of 4: cold-start provisioning probe   (Cloud Shell)
# =============================================================================
# Applies build/checks/module1/terraform (AlloyDB primary + read pool, Cloud SQL
# for SQL Server, DMS allowlists, users, secrets) and times every resource from
# a timestamped copy of Terraform's own output. Then applies a second time and
# requires `terraform plan -detailed-exitcode` to exit 0.
#
#   cd ~/cymbalgoal-alloydb-workshop && bash build/checks/module1/step1-provision.sh
#
# Runs about 20-25 minutes. The last thing it prints is the handback block; it
# is also saved to build/checks/module1/terraform/step1-handback.txt and, when
# the write grant is in place, to the workshop bucket.
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$HERE/terraform"
TF_VERSION="1.12.1"
REGION="${REGION:-us-central1}"
RESULTS="gs://class-demo/alloydb-labs/cymbalgoal-alloydb-workshop/module1-checks"

PROJECT="$(gcloud config get-value project 2>/dev/null)"
ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
if [[ -z "$PROJECT" || -z "$ACCOUNT" ]]; then
  printf 'No project or account in gcloud config. Run: gcloud config set project <lab project>\n'
  exit 1
fi

# ---- Terraform, the version the lab platform runs ----------------------------
export PATH="$HOME/bin:$PATH"
if ! terraform version 2>/dev/null | head -1 | grep -q "v${TF_VERSION}$"; then
  printf 'Installing Terraform %s ...\n' "$TF_VERSION"
  mkdir -p "$HOME/bin"
  curl -fsSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" -o /tmp/tf.zip \
    && unzip -oq /tmp/tf.zip -d "$HOME/bin" && rm -f /tmp/tf.zip
fi
terraform version | head -1

cd "$TF_DIR" || exit 1
printf 'gcp_project_id = "%s"\ngcp_region     = "%s"\ngcp_zone       = "%s-a"\nusername       = "%s"\n' \
  "$PROJECT" "$REGION" "$REGION" "$ACCOUNT" > terraform.tfvars

# Prefix every line with epoch seconds so each resource's start and finish are known.
stamp() { while IFS= read -r line; do printf '%s %s\n' "$(date +%s)" "$line"; done; }

printf '\n== init / validate ==\n'
terraform init -input=false -no-color > init.log 2>&1;  RC_INIT=$?
[[ $RC_INIT -ne 0 ]] && tail -30 init.log
terraform validate -no-color > validate.log 2>&1;        RC_VALIDATE=$?
cat validate.log

RC_APPLY1=99; RC_APPLY2=99; RC_PLAN=99
T0=$(date +%s)
if [[ $RC_INIT -eq 0 && $RC_VALIDATE -eq 0 ]]; then
  printf '\n== apply 1 (the cold start; ~20 min) ==\n'
  terraform apply -auto-approve -input=false -no-color 2>&1 | stamp | tee apply1.log
  RC_APPLY1=${PIPESTATUS[0]}
fi
T1=$(date +%s)

if [[ $RC_APPLY1 -eq 0 ]]; then
  printf '\n== apply 2 (anything it changes is a second-apply trap) ==\n'
  terraform apply -auto-approve -input=false -no-color > apply2.log 2>&1; RC_APPLY2=$?
  tail -3 apply2.log
  terraform plan -detailed-exitcode -input=false -no-color > plan.log 2>&1; RC_PLAN=$?
  printf 'plan -detailed-exitcode: %s\n' "$RC_PLAN"
fi

# ---- Handback -----------------------------------------------------------------
HB="$TF_DIR/step1-handback.txt"
{
  printf '%s\n' "=============================================================================="
  printf 'MODULE 1 CHECKS: STEP 1 PROVISIONING HANDBACK   %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'project %s   region %s   terraform %s\n' "$PROJECT" "$REGION" "$(terraform version | head -1)"
  printf '%s\n' "=============================================================================="
  printf 'exit codes: init %s  validate %s  apply1 %s  apply2 %s  plan %s (0 = no drift)\n' \
    "$RC_INIT" "$RC_VALIDATE" "$RC_APPLY1" "$RC_APPLY2" "$RC_PLAN"
  printf 'apply 1 wall clock: %dm%02ds\n' $(( (T1 - T0) / 60 )) $(( (T1 - T0) % 60 ))

  if [[ -f apply1.log ]]; then
    printf '\n## Per-resource timeline (seconds from apply start)\n'
    python3 - "$T0" apply1.log <<'PY'
import re, sys
t0 = int(sys.argv[1]); start, end, took = {}, {}, {}
for line in open(sys.argv[2], encoding="utf-8", errors="replace"):
    ts, _, msg = line.rstrip("\n").partition(" ")
    if not ts.isdigit():
        continue
    m = re.match(r"^(\S+): Creating\.\.\.", msg)
    if m:
        start.setdefault(m.group(1), int(ts))
    m = re.match(r"^(\S+): Creation complete after (\S+)", msg)
    if m:
        end[m.group(1)] = int(ts); took[m.group(1)] = m.group(2)
rows = sorted(end, key=lambda r: end[r])
print(f"  {'resource':<62}{'start':>7}{'done':>7}  took")
for r in rows:
    if took[r] in ("0s", "1s"):
        continue
    print(f"  {r:<62}{start.get(r, end[r]) - t0:>7}{end[r] - t0:>7}  {took[r]}")
still = [r for r in start if r not in end]
if still:
    print("  NOT COMPLETED: " + ", ".join(still))
PY
    printf '\n## Errors in apply 1\n'
    grep -n -A8 'Error:' apply1.log | cut -c1-260 | head -80 || true
  fi

  if [[ $RC_PLAN -eq 2 ]]; then
    printf '\n## Drift after the second apply (plan)\n'
    grep -E '^\s+# |^\s+[~+-] ' plan.log | head -40
  fi

  if [[ $RC_APPLY1 -eq 0 ]]; then
    printf '\n## Outputs\n'
    terraform output -no-color | grep -v -i password
    SQL_INSTANCE="$(terraform output -raw sqlserver_instance 2>/dev/null)"
    printf '\n## Cloud SQL instance as created\n'
    gcloud sql instances describe "$SQL_INSTANCE" \
      --format='value(databaseVersion,settings.tier,settings.edition,state,createTime,settings.ipConfiguration.authorizedNetworks.len())' 2>&1 | head -3
    printf '\n## AlloyDB instances as created\n'
    gcloud alloydb instances list --cluster=cymbalgoal-cluster --region="$REGION" \
      --format='table(name.basename(),instanceType,machineConfig.cpuCount,readPoolConfig.nodeCount,state,createTime)' 2>&1
  fi
  printf '%s\n' "=============================================================================="
} > "$HB" 2>&1

printf '\n\n'
cat "$HB"
if gcloud storage cp "$HB" "$RESULTS/step1-handback-$(date -u +%Y%m%dT%H%M%SZ).txt" >/dev/null 2>&1; then
  printf '(saved to %s/)\n' "$RESULTS"
else
  printf '(could not save to %s: check the class-demo write grant. Local copy: %s)\n' "$RESULTS" "$HB"
fi
