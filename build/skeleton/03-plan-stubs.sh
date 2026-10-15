#!/usr/bin/env bash
# =============================================================================
# Skeleton run 3: validate and plan aldb001, aldb004, aldb005. PLAN ONLY.
# =============================================================================
# WHERE: Cloud Shell in any lab project, from your clone of this repo.
# RUN:   bash build/skeleton/03-plan-stubs.sh            (or name modules: ... aldb005)
#
# Each module is planned from empty state, so every resource shows as a create.
# A plan that errors is the finding. Paste back the HANDBACK block.
# =============================================================================
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
MODULES=("$@"); [ ${#MODULES[@]} -eq 0 ] && MODULES=(aldb001 aldb004 aldb005)
OUT="$HOME/cg-stubs/handback.txt"; mkdir -p "$HOME/cg-stubs"
{
  echo "================================================================"
  echo "SKELETON RUN 3: STUB MODULES VALIDATE + PLAN"
  echo "project ${PROJECT}"
  echo "================================================================"
} > "${OUT}"
for m in "${MODULES[@]}"; do
  W="$HOME/cg-stubs/$m"
  stage_module "$W" "$m"
  echo "== $m"
  ( cd "$W"
    terraform init -input=false -no-color > init.log 2>&1 || { echo "$m: init FAILED"; tail -15 init.log; exit 0; }
    if terraform validate -no-color > validate.log 2>&1; then v=ok; else v=FAILED; fi
    terraform plan -input=false -no-color -detailed-exitcode -out=plan.bin > plan.log 2>&1; rc=$?
    if [ $rc -eq 2 ]; then
      terraform show -json plan.bin > plan.json
      summary=$(python3 -c '
import json, collections
p = json.load(open("plan.json"))
c = collections.Counter(r["type"] for r in p["resource_changes"] if "create" in r["change"]["actions"])
print(f"{sum(c.values())} to create: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))')
      echo "$m: validate ${v}; plan OK; ${summary}"
    else
      echo "$m: validate ${v}; plan exit ${rc}"
      grep -E "Error|error" -A6 plan.log validate.log | head -40
    fi
  ) | tee -a "${OUT}"
done
echo "================================================================" >> "${OUT}"
echo; cat "${OUT}"
