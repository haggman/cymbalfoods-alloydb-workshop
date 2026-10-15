# Handback: shared provisioning, setup VM, and lab folders

Oct 15, 2026 · Track A build, from the skeleton conversation · answers `state/handoff-skeleton.md`

## Where this stands

Everything in the handoff's build list is written to disk. **Nothing has run against Google Cloud yet**, so there are no measured cold-start times in this file. Four run scripts produce them (see "Runs" below). Until then, here is what has been checked:

- **HCL parses** (python-hcl2), and every resource, local and variable reference resolves. `terraform validate` couldn't run here because this environment can't reach the Terraform registry. Runs 1 and 3 validate for real.
- **The bootstrap ran end to end against a local PostgreSQL 16**, with the Admin API, gcloud and the connector stubbed out. It joined the student's role through the (mocked) Admin API, created the database as the student, imported seven tables as the student, and fell back to COPY for one file with a header row. It built the baseline indexes and passed its ownership check, with zero objects owned by anyone else. The student could then ALTER DATABASE, change table owners, RENAME, REINDEX, and DROP and CREATE indexes. A re-run skipped every step. With membership refused, it stopped with a readable FAILED status line.
- **The repo appears to be public** (an anonymous `git ls-remote` works), which the thin roots need.

Decisions taken with Patrick in this conversation:
- Terraform uploads `bootstrap/` to the staging bucket rather than the VM cloning at a tag.
- The instance is on `google-beta` in every module.
- `class-demo` is anonymous read, so the VM needs no grant.
- The default datasets per module are as in the matrix below.

## Placement

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

## Feature matrix as built

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

## Measured cold-start times

**Pending.** Run 2 measures aldb003 from apply start to "ready", with per-step and per-table times from the VM's `handback.json`. The handoff's target is ready within 20 minutes. Expected shape:
- about 9 minutes for the cluster and instance
- about 1.5 minutes of VM boot and packages
- up to about 2 minutes for the Data API restart, partly overlapped
- the loads: about 3 minutes measured by COPY in pipeline/02; import overhead is unknown

## Task 0 check

```bash
gcloud storage cat gs://$(gcloud config get-value project 2>/dev/null)-cymbalgoal-staging/setup/status 2>/dev/null || echo "starting: the setup VM has not reported yet"
```

It prints exactly one of:
- `starting: …` (the VM hasn't booted or reported yet)
- `loading 5/9: core tables (11m02s since the VM started)`
- `ready: aldb003 setup finished in 16m40s (core, profiles, scout_reports)`
- `FAILED at 4/9 …: <reason>. Instructor: gs://…/setup/bootstrap.log`

The same command is the `task0_check` Terraform output.

## Runs

All four are run-and-paste. Each prints one HANDBACK block. Nothing needs write access to `class-demo`.

**Before run 1:** push the DevWork repo (the scripts run from a clone), and push the `alloydb-workshop` branch of gcp-ce-content (run 4 needs it).

**Runs 1 and 3: plan only, any lab project.**
WHERE: Cloud Shell, signed in as the lab student. WHAT:
```bash
git clone https://github.com/haggman/cymbalgoal-alloydb-workshop.git && cd cymbalgoal-alloydb-workshop
bash build/skeleton/01-plan-parity.sh
bash build/skeleton/03-plan-stubs.sh
```
Run 1 should report `VERDICT: PARITY`, with the only provider changes being the instance moving to `google-beta` and the attributes only the beta schema has.

**Run 2: aldb003 cold start, in a fresh lab project.** About 25 minutes unattended.
WHERE: Cloud Shell, signed in as the lab student, in a project nothing has been applied to. WHAT:
```bash
git clone https://github.com/haggman/cymbalgoal-alloydb-workshop.git && cd cymbalgoal-alloydb-workshop
bash build/skeleton/02-cold-start.sh aldb003
```

**Run 4: Start Lab on aldb002 and aldb003.** These go through the thin roots, with `ref=main` and the module fetched from GitHub.
WHERE: Cloud Shell in each lab's project, once its Start Lab has finished. WHAT:
```bash
curl -fsSL https://raw.githubusercontent.com/haggman/cymbalgoal-alloydb-workshop/main/build/skeleton/04-start-lab-check.sh | bash
```
For aldb002, "the notebook connects" means its student-login line shows `postgres: connected as …`. That's the same connector call a Colab notebook makes.

## Run 2, first attempt (Oct 15, aldb003)

- **Answered: the Admin API accepts the student's role in `databaseRoles`.** `users.patch` made the VM's login a member in 3.9 s, and `SET ROLE` then `CREATE DATABASE` worked, so the database is the student's.
- **Data API:** PATCH accepted on `/v1/`; the restart took 138 s, overlapped with the 16 s source copy.
- **Failed at extensions:** they were created in alphabetical order, and `alloydb_scann` requires `vector`. Fixed: explicit order plus `CASCADE`. The re-run also skips the Data API PATCH when it already reads ENABLED, so it doesn't restart the instance twice.
- Re-run in the same project with `RESUME=1 bash build/skeleton/02-cold-start.sh aldb003` (after a pull).
- **Second attempt (RESUME):** every load finished, 11 of 11 steps in 9m07s from VM boot, and the database owner is the student. It stopped at the ownership check, which counted 51 relations in `public` not owned by the student. Most likely cause: members of a trusted extension belong to the bootstrap superuser whoever ran `CREATE EXTENSION`. The check now leaves extension members out (reporting them by extension and owner), logs anything else by name, and fails only on non-extension objects.

## What run 2 decides

These are the open questions in the design. The bootstrap reports each one, so a failure tells you which fallback to take.

1. **Does the Admin API accept the student's role in `databaseRoles`?** It's the first membership route. If the API refuses, the bootstrap tries a SQL GRANT. If both fail, status is FAILED at "join the student's role", and the fallback is the mkt014/015 pattern (the student runs the load in Task 0). That fallback is a change to the module design, so it comes back to Patrick before anything is rebuilt.
2. **Can the student's IAM role create a database after `SET ROLE`?** The handback records `student_rolcreatedb`. If it can't, the bootstrap tries `CREATE DATABASE … OWNER` as the VM's login.
3. **Does import run as an IAM user?** Recorded per table.
4. **Is import overhead acceptable** across eleven calls? Compare the per-table seconds against COPY's 79–88 s for the core tables.
5. **No drift after the VM powers itself off.** `desired_status` is in `ignore_changes` in case the provider reads the stopped VM as a diff.

## For the lab-writing conversations

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
