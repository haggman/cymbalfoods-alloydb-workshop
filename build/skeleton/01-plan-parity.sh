#!/usr/bin/env bash
# =============================================================================
# Skeleton run 1: aldb002 plan parity. PLAN ONLY, nothing is created.
# =============================================================================
# WHERE: Cloud Shell in any lab project, from your clone of this repo.
# RUN:   bash build/skeleton/01-plan-parity.sh
#
# Plans the module as it was before the switches (commit 7557e1d) and as it is
# now with lab_module = aldb002, then compares every planned resource: address,
# action and every known attribute. The one expected difference is the AlloyDB
# instance's provider (google -> google-beta). Paste back the HANDBACK block.
# =============================================================================
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
BASE_REF="${1:-7557e1d}"
W="$HOME/cg-parity"
rm -rf "$W"; mkdir -p "$W/old" "$W/new"

git -C "${REPO}" archive "${BASE_REF}" terraform | tar -x -C "$W/old"
stage_module "$W/new/terraform" aldb002
printf 'gcp_project_id = "%s"\ngcp_region     = "%s"\ngcp_zone       = "%s"\nusername       = "%s"\n' \
  "${PROJECT}" "${REGION}" "${ZONE}" "${ACCOUNT}" > "$W/old/terraform/terraform.tfvars"

for side in old new; do
  echo "== planning ${side}"
  ( cd "$W/${side}/terraform" \
    && terraform init -input=false -no-color > init.log 2>&1 \
    && terraform plan -input=false -no-color -out=plan.bin > plan.log 2>&1 \
    && terraform show -json plan.bin > plan.json ) || { echo "  ${side} FAILED, see $W/${side}/terraform/*.log"; tail -20 "$W/${side}/terraform/plan.log" "$W/${side}/terraform/init.log" 2>/dev/null; }
done

python3 - "$W" "${BASE_REF}" <<'PYEOF'
import json, sys, os
w, ref = sys.argv[1], sys.argv[2]
def load(side):
    p = f"{w}/{side}/terraform/plan.json"
    if not os.path.exists(p):
        return None
    out = {}
    for rc in json.load(open(p)).get("resource_changes", []):
        ch = rc["change"]
        out[rc["address"]] = {"actions": ch["actions"], "provider": rc["provider_name"].split("/")[-1],
                              "after": ch.get("after") or {}, "unknown": sorted((ch.get("after_unknown") or {}).keys())}
    return out
old, new = load("old"), load("new")
lines = ["=" * 64, "SKELETON RUN 1: aldb002 PLAN PARITY", f"baseline {ref}; project {os.environ.get('PROJECT','')}", "=" * 64]
if old is None or new is None:
    lines.append(f"plan failed: old={'ok' if old else 'FAILED'} new={'ok' if new else 'FAILED'} (logs under {w})")
else:
    only_old, only_new = sorted(set(old) - set(new)), sorted(set(new) - set(old))
    diffs, prov = [], []
    for a in sorted(set(old) & set(new)):
        o, n = old[a], new[a]
        if o["provider"] != n["provider"]:
            prov.append(f"{a}: {o['provider']} -> {n['provider']}")
        if o["actions"] != n["actions"]:
            diffs.append(f"{a}: actions {o['actions']} -> {n['actions']}")
        o_keys = set(o["after"]) | set(o["unknown"])
        n_keys = set(n["after"]) | set(n["unknown"])
        # An attribute only one provider's schema has (google-beta has more on
        # the instance) is reported apart: it is not a change in what we asked for.
        schema_only = sorted(o_keys ^ n_keys)
        if schema_only:
            prov.append(f"{a}: attributes in only one provider's schema: {schema_only}")
        for k in sorted(o_keys & n_keys):
            if o["after"].get(k) != n["after"].get(k) or ((k in o["unknown"]) != (k in n["unknown"])):
                diffs.append(f"{a}.{k}: {json.dumps(o['after'].get(k))[:150]} -> {json.dumps(n['after'].get(k))[:150]}")
    lines += [f"resources: old {len(old)}, new {len(new)}",
              f"only in old: {only_old or 'none'}", f"only in new: {only_new or 'none'}",
              "provider changes:"] + ["  " + x for x in (prov or ["none"])] + [f"attribute or action differences: {len(diffs)}"]
    lines += ["  " + d for d in diffs[:60]]
    verdict = "PARITY" if not (only_old or only_new or diffs) else "DIFFERENT"
    lines.append(f"VERDICT: {verdict}")
lines.append("=" * 64)
print("\n".join(lines))
PYEOF
