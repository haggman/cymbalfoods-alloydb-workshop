# Handback: shared provisioning, setup VM, and lab folders

Oct 16, 2026 · Track A build, from the skeleton conversation · answers `state/handoff-skeleton.md`

## Status: paused for the dataset change

Google replaced the CymbalGoal football dataset with **Cymbal Foods** (`state/handoff-dataset.md`). The skeleton stops here, partway through its first test pass, and resumes as a shorter **version 2** once the new dataset is staged. The provisioning machinery doesn't depend on the dataset and carries over. The loader, the aldb005 workload, the resource names and the lab-folder story text are CymbalGoal-specific and change.

| Item | State |
| :-- | :-- |
| Module switches (`terraform/modules.tf`), google-beta instance, read pool, HA, columnar, observability | Built. aldb003's configuration applied cleanly on real Google Cloud. aldb001/004/005 plans: no result reported back |
| Setup VM and bootstrap | Built. Ran end to end on real AlloyDB twice (aldb003); two bugs found and fixed, the second fix not yet re-run |
| Ownership trap | **Solved** on real AlloyDB: the student owns the database with no student action |
| aldb002 plan parity (run 1) | Script written; no result reported back |
| Start Lab through the thin roots (run 4) | Not run |
| Five lab folders | Built and committed on gcp-ce-content `alloydb-workshop` (a383f1867d) |
| Stubs | aldb001 SQL Server/DMS, aldb004 BigQuery/Iceberg/Datastream: unchanged, still `false` |

## What we learned on real AlloyDB

From two runs of `02-cold-start.sh aldb003` on Oct 15, project `qwiklabs-gcp-04-7fee42316409`.

**Proven:**
- **The ownership trap has a clean answer.** The setup VM's IAM login can't join the student's role on its own, but the AlloyDB Admin API can do it: `users.patch` with the student's role added to the VM user's `databaseRoles` worked in 3.9 s. The VM then runs `SET ROLE "<student>"` and `CREATE DATABASE`, so the student owns the database and everything created in it. Nothing in the lab asks the student to load or fix ownership.
- **Data API:** the `/v1/` PATCH to `ENABLED` was accepted in 2.5 s; the instance restart it triggers took **138 s**. The bootstrap fires it first and waits before any SQL, so the restart happens before the student arrives.
- **Course data is reachable from the VM:** the VM's service account copied 11 files from anonymous-read `class-demo` into the project's staging bucket in **16 s**, with no grant.
- **VM start-up:** boot to bootstrap start was about **51 s** (apt and pip on Debian 12).
- **The whole aldb003 Terraform configuration applies:** the google-beta instance, the setup service account and its database user, the subnet, the IAP rule, the code upload and the VM.
- **The status line works as the Task 0 check:** `starting` → `loading N/11: …` → `FAILED at …` were all seen, each with the time since the VM started.

**Bugs found and fixed:**
1. **Extension order.** Extensions were created alphabetically, so `alloydb_scann` came before `vector`, which it requires. They're now in an explicit order (`vector` first) with `CASCADE`. On a re-run the bootstrap also skips the Data API PATCH if it already reads `ENABLED`, so the instance doesn't restart twice.
2. **Ownership check too strict.** The second run loaded everything (11 of 11 steps, **9m07s** after VM boot), and the database owner was the student. Then the check counted **51 relations in `public`** not owned by the student. The likely cause: objects inside a trusted extension belong to the internal superuser, whoever ran `CREATE EXTENSION`. The check now leaves extension members out, reports them by extension and owner, names anything else, and still fails on any non-extension object. **This fix hasn't been re-run.**

**Load time (derived, so treat as rough):** the first run reached the loads at 3m34s, which included the 138 s Data API wait. The second run skipped that wait and finished at 9m07s, so loads plus index builds took roughly **7 to 8 minutes** for 1.66M relational rows, profile embeddings and 38,445 report vectors. Client COPY did the same data in about 3 minutes in `pipeline/02`, so each server-side import call seems to carry real overhead. The per-table split was in that run's `setup/handback.json`, which didn't come back to this conversation. **This matters for Cymbal Foods**, whose lab slice is about 5M rows and 100,000 vectors against an 11-minute budget.

**Still unverified** (carry into version 2):
- The ownership-check fix, and the student-side owner tests in `02-cold-start.sh` (ALTER DATABASE, change owner, RENAME, REINDEX, CREATE/DROP INDEX, CREATE/DROP DATABASE).
- `terraform plan -detailed-exitcode` after two applies, and after the VM powers itself off (`desired_status` is in `ignore_changes` against that drift).
- Whether import runs as the student's IAM user, or falls back to `postgres`/COPY, table by table.
- aldb002 plan parity; aldb001/004/005 validate and plan; any Start Lab through a thin root (`github.com/...//terraform?ref=main`).
- Apply time for the cluster and instance (about 9 minutes measured in earlier labs).

## What the dataset change touches

**Carries over unchanged:**
- The module switches, feature map shape, google-beta instance, read pool, HA, columnar and observability settings.
- The setup VM: service account and roles, subnet, IAP SSH, code upload, startup script, the status line and Task 0 check, power-off, failure handling, resume.
- The ownership mechanism (Admin API membership, then `SET ROLE`), the Data API PATCH, the import path through the staging bucket, and the per-file COPY fallback.
- The thin-root pattern and the gcp-ce-content folder structure, `QL_OWNER`, `qwiklabs.yaml` mechanics.
- The run scripts' structure and the check-sheet pattern.

**CymbalGoal-specific, must change:**

| What | Where | Change |
| :-- | :-- | :-- |
| Table list, load order, expected row counts | `bootstrap.py`: `CORE_TABLES`, `EXPECT`, `load_core` / `load_profiles` / `load_reports` | Read from the dataset's `manifest.json` instead of code (see the loader contract below) |
| Profile "pass 2" (load then UPDATE embeddings) | `bootstrap.py`: `load_profiles` | Drop it: ask the generator for one file per final table, embeddings inline |
| Baseline indexes (mkt015's six football B-trees) and `indexes.sql` | `bootstrap.py`: `BASELINE_INDEXES`, `build_indexes` | One index SQL file per module set, staged with the data |
| Source prefixes | `setup_vm.tf`: `core_src`, `reports_src` | `gs://class-demo/alloydb-labs/cymbalfoods-alloydb-workshop/…` |
| Datasets per module | `modules.tf`: `datasets`, `indexes` | Cymbal Foods table groups per module |
| aldb005 workload | `bootstrap/workload/deadline_day.py` | A new holiday-rush generator on the Cymbal Foods schema; only the systemd and `make_connect()` plumbing carries over |
| Resource and object names | `cymbalgoal-cluster`, `cymbalgoal-primary`, `cymbalgoal-network`, `<project>-cymbalgoal-staging`, `cymbalgoal-setup`, database `cymbalgoal`, the repo name | Decision below |
| Lab story text | the five `qwiklabs.yaml` descriptions and `en.md` overviews and objectives | Rewrite for Cymbal Foods |
| aldb005 title and folder | "AlloyDB Deadline Day: Operations Under Load", `aldb005-alloydb-deadline-day-operations` | "Deadline day" is football transfer-window language; retitle, and rename the folder to match |
| Task 0 command | lab `en.md` Task 1, `setup_vm.tf` `task0_check`, run scripts | Follows the bucket name |

## Loader contract for the new dataset

What the bootstrap needs from the dataset build so it can be data-driven (also summarized in `state/handoff-dataset.md`):

- **Files:** one gzip CSV per table under the staging root, **headerless** (AlloyDB import can't skip a header; a file with a header falls back to slower client COPY), NULL as an unquoted empty field, vectors as pgvector literals. One file per final table, with any embedding column inline.
- **`manifest.json`:** per table, the file name, `column_order` matching the DDL, and the expected row count; the load order (parents before children, if the schema has foreign keys); and named table groups, so `modules.tf` can say "aldb003 loads groups X and Y".
- **DDL:** `schema.sql` with `CREATE TABLE` only, no indexes (indexes built before a bulk load make every row an index insert). Constraints are fine if the load order respects them.
- **Indexes:** one SQL file per module set (for example `indexes-search.sql`, `indexes-baseline.sql`), every statement `IF NOT EXISTS`, so a re-run is safe.
- **Extensions** each group needs, listed in the manifest in dependency order (`vector` before `alloydb_scann`).
- **Budget:** the lab-scale slice for a setup-VM module must load and index in about 11 minutes after the cluster exists. Given the import overhead above, the dataset's load test (its stage 4) should time **both** server-side import and parallel client COPY. The bootstrap's `load_method` switch can use either.
- Spanner-shaped UUID keys are fine for the loader.

## Version 2: the shorter pass

**Starts when** the Cymbal Foods lab slice is staged with its manifest and has passed the dataset's own load test.

**Decisions for Patrick first:**
1. **Names.** Keep the `cymbalgoal-*` resource names, the `cymbalgoal` database name and the repo name, or move to Cymbal Foods names (for example `cymbalfoods-cluster`, database `cymbalfoods`, bucket suffix `-cymbalfoods-staging`)? The new staging root is already `cymbalfoods-alloydb-workshop`. Since the same dataset serves Track B and Spanner, neutral names may age better.
2. **aldb005's new title**, so the folder is renamed once.
3. **The aldb005 workload:** build the holiday-rush generator in version 2, or stub it there (VM stays up, no workload) and build it with Module 5.

**Code pass (this conversation):**
- Manifest-driven loader: tables, order, counts, groups, extensions and index files all from `manifest.json`; no table names in code.
- `modules.tf` dataset groups and index sets per module; new source prefix.
- The renames from decision 1, everywhere including the run scripts and the Task 0 command.
- Lab folders: new story text, the aldb005 rename.

**Runs (Patrick), three instead of six:**
1. `03-plan-stubs.sh` for **all five** modules: validate and plan. This replaces the separate parity run, since the module's shape won't change again.
2. `02-cold-start.sh` on the heaviest setup-VM module (aldb003 or whichever loads the most): apply time, every bootstrap step and table timed, both plans exit 0, the student-side owner tests. This also clears every "still unverified" item above.
3. One Start Lab through a thin root (the same module), then `04-start-lab-check.sh`, which proves the GitHub module fetch and the student's view.

Nothing in version 2 needs write access to `class-demo`.

## Reference: what was built

As of Oct 15, on the CymbalGoal data. Rows marked with table names change in version 2.

### Placement

| File | Path | Note |
| :-- | :-- | :-- |
| modules.tf | `terraform/modules.tf` | **New.** The feature map: one column per lab, `local.f` is the active one |
| variables.tf | `terraform/variables.tf` | Added `lab_module` (default `aldb002`), `track` (`cloud` only), `setup_machine_type` |
| main.tf | `terraform/main.tf` | Header rewritten; APIs + `extra_apis`; PSA range waits for the setup subnet; instance on google-beta with HA, columnar and observability switches; read pool; module student roles |
| setup_vm.tf | `terraform/setup_vm.tf` | **New.** SA and roles, SA database user, subnet, IAP SSH rule, code upload, the VM |
| versions.tf, provider.tf | `terraform/` | google-beta 7.35.0 added |
| outputs.tf | `terraform/outputs.tf` | Added `lab_module`, `instance_path`, `read_pool_instance`, `setup_vm`, `task0_check` |
| README.md, terraform.tfvars.example | `terraform/` | Feature matrix, setup VM, testing; `lab_module` example |
| startup.sh | `terraform/bootstrap/startup.sh` | **New.** VM boot: packages, code, bootstrap, power off (or start the workload) |
| bootstrap.py | `terraform/bootstrap/bootstrap.py` | **New.** Membership, database as the student, loads, indexes, Data API, checks, status |
| deadline_day.py | `terraform/bootstrap/workload/deadline_day.py` | mkt015's generator; only `make_connect()` changed (takes `CG_*` env, private IP, SET ROLE) |
| cymbalgoal-workload.service | `terraform/bootstrap/workload/` | **New.** systemd unit for aldb005 |
| _common.sh, 01–04 | `build/skeleton/` | **New.** Run scripts, each ending in one handback block |
| 5 lab folders | `gcp-ce-content/labs/aldb00N-…/` | On branch `alloydb-workshop`: `QL_OWNER` (mkt015's), `qwiklabs.yaml`, `instructions/en.md`, `terraform/` thin root |

The workload lives under `terraform/bootstrap/` rather than a top-level `workload/` so it's inside the module directory Terraform uploads from.

### Feature matrix as built

| | aldb001 | aldb002 | aldb003 | aldb004 | aldb005 |
| :-- | :-- | :-- | :-- | :-- | :-- |
| Setup VM | yes; nothing to load yet | **no** | yes | yes | yes, stays up |
| Data at arrival | none (DMS fills it) | none | core, profiles, scout reports | core | core, profiles |
| Indexes | none | none | `indexes.sql` + scout-report B-trees + ScaNN (√rows leaves, sq8) | mkt015 baseline six | mkt015 baseline six |
| Extensions | none | none | vector, google_ml_integration, alloydb_scann, pg_trgm | vector, google_ml_integration | vector, google_ml_integration |
| Read pool | 1 node, public IP | | | | |
| Availability | ZONAL | ZONAL | ZONAL | ZONAL | **REGIONAL** |
| Columnar | off | off | off | off | on, auto-columnarization off |
| Enhanced query insights | | | | | yes |
| Data API | | | ENABLED by the VM, before loading | | |
| Extra APIs | iap, datamigration, sqladmin | | iap, geminidataanalytics, cloudaicompanion, dataplex, modelarmor | iap, bigquery, bigqueryconnection, datastream, dataplex | iap |
| Student roles | | | databaseUser, queryDataUser, serviceUsageConsumer | | |
| Workload | | | | | systemd service on the VM |
| **Stubbed** | `sqlserver`: Cloud SQL for SQL Server, `.bak`, CDC, DMS resources | | | `bq_history`: BigQuery datasets, Iceberg, Datastream prerequisites | |

Each stub is a `false` in `modules.tf` with nothing behind it yet. The Module 1 checks handback sets the aldb001 switch; Module 4's checks set aldb004's.

**Load method.** Every module defaults to `load_method = "import"`: AlloyDB's server-side import, one call per file, run as the student. If a call fails, it retries as `postgres` (after a GRANT), and then falls back to client COPY for that file. A file with a header row always goes through COPY. The handback records which path each table took. If eleven import calls turn out slower than client COPY, change that module's `load_method` to `"copy"`. That's a one-word edit.

## Task 0 check (current names)

```bash
gcloud storage cat gs://$(gcloud config get-value project 2>/dev/null)-cymbalgoal-staging/setup/status 2>/dev/null || echo "starting: the setup VM has not reported yet"
```

It prints exactly one of `starting: …`, `loading 5/11: core tables (4m02s since the VM started)`, `ready: … setup finished in …`, or `FAILED at …: <reason>. Instructor: gs://…/setup/bootstrap.log`. The same command is the `task0_check` Terraform output. A failed run leaves the VM up; `RESUME=1 bash build/skeleton/02-cold-start.sh <module>` re-runs it in the same project after a fix.

## For the lab-writing conversations

Written for CymbalGoal; the mechanics hold for Cymbal Foods, but table names and the aldb005 workload change.

- **The database and everything in it belong to the student.** Labs can tell students to `ALTER`, `DROP`, `REINDEX` and change owners freely. The setup VM's login (`cymbalgoal-setup@<project>.iam`) stays a member of the student's role and shows up in `pg_roles`. If aldb003's secure-views task lists roles, say what it is.
- **No `provisioning_status` table.** mkt014 had one. Readiness is the bucket status line, so the schema an agent inspects holds only real tables. The profile staging tables are dropped too, for the reason mkt014 found.
- **aldb003:**
  - The Data API is on before the data loads, so its instance restart happens before arrival and never mid-lab.
  - Whether the managed MCP server also needs a per-project MCP enablement step isn't verified here. Module 3's checks should settle it.
  - `pg_trgm` is created up front (QueryData value searches).
- **aldb005:**
  - The workload runs on the VM as a service: `sudo systemctl status|stop cymbalgoal-workload` over IAP SSH. Its report runs as `sudo /opt/cymbalgoal/venv/bin/python /opt/cymbalgoal/code/workload/deadline_day.py --report`.
  - It runs as the VM's login with `SET ROLE` to the student. Whether Query Insights then attributes queries to the student or to the VM's login is unverified.
  - `pgbench` and `psql` are on the VM. How the student connects them (IAM token as the password, or the Auth Proxy) is the lab's choice and isn't tested yet.
  - REGIONAL doubles the capacity each student needs.
  - mkt015 saw `track_client_address` not come back from the API. If the aldb005 re-apply plan shows a diff there, that's the cause.
- **aldb004:** Datastream from AlloyDB will need logical decoding on the instance. Verify that flag name against `supportedDatabaseFlags` before adding it to `instance_flags`.
- **aldb001:**
  - Cold start includes creating the read pool, and the setup VM waits for it.
  - The student's database user may be created while the read pool is still building. If aldb001's first cold start shows a conflicting-operation error there, make that user wait for the read pool.
- **Before any event:** change `ref=main` to a release tag in all five thin roots. Until then, a push to main changes what the next Start Lab builds. Titles live in three places that move together: `qwiklabs.yaml`, the `en.md` H1, and the `terraform/main.tf` header.
- **Lab metadata:** `qwiklabs.yaml` takes `username` from `user_0.local_username`, as mkt015 does. Levels are introductory (001), advanced (002–004) and intermediate (005), and `credits: 1`. Change them if Google wants something else.
