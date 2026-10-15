# Module 1 (aldb001) feature checks

Probes that every Module 1 feature works in a Qwiklabs lab project: Cloud SQL for SQL Server with CDC,
assessment, DMS SQL Server → AlloyDB with the Gemini conversion workspace, a continuous job, DVT, and the
read pool. Written for `state/handoff-module1-checks.md`; results go to `state/module1-checks-handback.md`.

All four steps run in **one** lab project, in order. Each ends with one handback block to paste back.

| Step | Where | File | Time |
| :-- | :-- | :-- | :-- |
| 1 | Cloud Shell | `step1-provision.sh` (applies `terraform/`) | ~20–25 min |
| 2 | Colab Enterprise | `../../../pipeline/05-sqlserver-package.ipynb` | ~20–30 min |
| 3 | Colab Enterprise | `step3-dms-checks.ipynb` | ~45–75 min |
| 4 | Cloud Shell | `step4-cloudshell-checks.sh` | ~20–40 min |

## Before step 1

1. Push the repo: step 2 reads `pipeline/sqlserver/*.sql` from GitHub (`main`), and Cloud Shell clones it.
2. Grant the lab account write access to the workshop prefix, from your own account:

```bash
LAB_USER=student-XX-XXXXXXXXXXXX@qwiklabs.net   # the lab project's username
gcloud storage buckets add-iam-policy-binding gs://class-demo \
  --member="user:${LAB_USER}" --role=roles/storage.objectAdmin \
  --condition='expression=resource.name.startsWith("projects/_/buckets/class-demo/objects/alloydb-labs/cymbalgoal-alloydb-workshop/"),title=cymbalgoal-workshop-prefix'
```

`class-demo` is already readable anonymously, so no read grant is needed. The condition keeps the lab account's writes inside `cymbalgoal-alloydb-workshop/`; `cymbalgoal/` stays
read-only for it (the notebooks also refuse to write there).

## Step 1: provision (Cloud Shell, in the lab project)

```bash
git clone https://github.com/haggman/cymbalgoal-alloydb-workshop.git ~/cymbalgoal-alloydb-workshop
cd ~/cymbalgoal-alloydb-workshop && bash build/checks/module1/step1-provision.sh
```

## Step 2: build the SQL Server package (Colab Enterprise)

Upload `pipeline/05-sqlserver-package.ipynb`, connect a runtime, **Run all**, paste back the last cell's output.

## Step 3: DMS end to end (Colab Enterprise)

Upload `build/checks/module1/step3-dms-checks.ipynb`, **Run all**, paste back the last cell's output. It
leaves the migration job running in CDC for step 4.

## Step 4: assessment, DVT, promote (Cloud Shell)

```bash
cd ~/cymbalgoal-alloydb-workshop && git pull && bash build/checks/module1/step4-cloudshell-checks.sh
```

`PROMOTE=0 bash ...` skips the promotion at the end.

## Re-running

- Step 1 is idempotent (`terraform apply`).
- Step 2 drops and rebuilds both databases (`REBUILD_FINAL = True`). If step 3 already created a migration
  job, delete it first: `gcloud database-migration migration-jobs delete cg-scouting-migration --region=us-central1`.
- Step 3 reuses profiles, the workspace and the job when they already exist.
