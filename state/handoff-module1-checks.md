# Handoff: Module 1 feature checks and the SQL Server package

Oct 15, 2026 · Track A build, written by the planning conversation

> **Status, Oct 16:** the dataset is changing to Cymbal Foods (`state/handoff-dataset.md`). The CymbalGoal SQL Server package and the probes in `build/checks/module1/` still answer the DMS, CDC, DVT and read-pool questions; the dataset build's stage 3 replaces the package's schema and procedures and reuses its build and restore mechanics. The aldb001 Terraform switch in `terraform/modules.tf` (`sqlserver`) is still a stub.

## Context

Track A of Google's AlloyDB workshop is five standalone Qwiklabs (aldb001–aldb005) on the CymbalGoal football data, built in order and due November 16. Module 1 is built next, so its risky features are checked now, in a fresh Qwiklabs project. This conversation does two things: builds the real legacy SQL Server package, and checks that the Module 1 lab can run on it. It writes no lab prose.

**Module 1 (L100–L200, lab ~75 min).** CymbalGoal's scouting data lives on a legacy SQL Server. Students meet the provisioned AlloyDB cluster, assess the SQL Server source, migrate it with Database Migration Service (DMS) using a conversion workspace where Gemini translates the T-SQL, keep it in sync with continuous replication, validate with the Data Validation Tool (DVT), connect a Python app with the AlloyDB Python Connector and IAM auth (compared with the Auth Proxy), and add a read-pool node. Start Lab provisions everything: AlloyDB, the read pool, and Cloud SQL for SQL Server with the package restored and CDC on.

## Read first

- `state/track-a-index-findings-and-next-steps.md`: where the build stands.
- `terraform/` and its `README.md`: the AlloyDB provisioning Module 1 will extend. A parallel conversation (`state/handoff-skeleton.md`) is adding module switches; leave `terraform/` to it and put your provisioning probes in `build/checks/module1/`.
- `DevWork/cymbalgoal-intelligent-search/notebooks/cymbalgoal_de_pipeline_part1.ipynb`: how the eight-table relational core was built, including its dirty edges.
- `pipeline/00-explore-and-size.ipynb`: scope and load conventions for this repo.
- Source data: `gs://class-demo/alloydb-labs/cymbalgoal/`. **Read only**: another team's labs depend on it. Write only under `gs://class-demo/alloydb-labs/cymbalgoal-alloydb-workshop/`.

## Part A: the SQL Server package

`pipeline/05-sqlserver-package.ipynb` (or scripts beside it), producing:

- Database `CymbalGoalScouting`: the eight relational-core tables in `dbo`, with T-SQL types a 2014 system would use (`NVARCHAR`, `DATETIME2`, `BIT`, `IDENTITY`, `MONEY` for fees), and the real data's dirty edges kept.
- Five to eight stored procedures written the legacy way: cursors, temp tables, `TOP`, `ISNULL`, `+` string concatenation, `MERGE`, `TRY/CATCH`, an output parameter or two. Plus one view, one scalar function, and one trigger. Include at least one construct the conversion should get wrong, so students have something real to catch in review.
- A `.bak` under `gs://class-demo/alloydb-labs/cymbalgoal-alloydb-workshop/sqlserver/`, along with the scripts that built it.

## Part B: the checks

Run in us-central1 unless the checks show a reason not to. For each check, do the desk research first (docs, release notes, preview or GA, region), then probe.

| Check | What we need to know |
| :-- | :-- |
| Cloud SQL for SQL Server | Which edition supports CDC for a DMS continuous job; cost per lab-hour; create time plus `.bak` import time |
| Assessment | Is Database Migration Assessment (or its current replacement) for SQL Server runnable by a student from Cloud Shell or the console? What does it report on our procedures? |
| DMS conversion | Is SQL Server → AlloyDB available (preview or GA)? Conversion workspace with Gemini: what converts cleanly, what it flags, what it gets wrong |
| DMS continuous job | Full load plus CDC end to end; a row changed on SQL Server appears in AlloyDB, and how long it takes; networking a startup script can build |
| DVT | `google-pso-data-validator` from Cloud Shell: row counts and column hashes across all eight tables; runtime |
| Read pool | Add-node time for Task 6; routing reads to it from the Python connector |
| Cold start | Cloud SQL for SQL Server, AlloyDB, and a read pool provisioned in parallel: total time against the 20-minute budget |

**Stop and ask Patrick** if DMS can't migrate SQL Server to AlloyDB at all. The fallback (a PostgreSQL source with homogeneous DMS, with SQL Server assessment moved to slides) changes the module, and that's his call.

## Conventions

- Patrick handles all git. Write files to disk only.
- Never `gsutil` (use `gcloud storage`); `printf`, not bare `echo >>`. If anything calls a generation model, use `gemini-3.8-flash`.
- Runs: Patrick runs a notebook or script end to end and pastes back one handback block. No per-step checklists.
- Propose your plan before writing files.

## Hand back

Write `state/module1-checks-handback.md`:

1. One row per check: `| Check | PASS / PASS with caveat / FAIL → fallback | Evidence (doc link or measured number) | Design impact on Module 1's tasks |`
2. The package: placement table, object list, and the deliberate conversion trap.
3. Measured times: provisioning per resource, `.bak` import, DMS full load and replication lag, DVT runtime, read-pool add.
4. What Module 1's Terraform needs (for the skeleton's aldb001 switch) and what the lab-writing conversation needs to know.
