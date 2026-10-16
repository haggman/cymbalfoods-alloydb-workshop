# Handoff: shared provisioning, setup VM, and lab folders

Oct 15, 2026 · Track A build, written by the planning conversation

> **Status, Oct 16:** built and partly tested on CymbalGoal, then paused when Google moved the workshop to the Cymbal Foods dataset. What was proven, what changes, and the shorter version 2 plan are in `state/skeleton-handback.md`.

## Context

Track A of Google's AlloyDB workshop is five standalone Qwiklabs (aldb001–aldb005) on the CymbalGoal football data. Every lab cold-starts: the student clicks Start Lab, waits 15–20 minutes, and proceeds. Labs are built in order, Module 1 first, and are due November 16. This conversation builds the provisioning every module shares. It writes no lab prose.

| Lab | Module | Provisioning beyond the common base |
| :-- | :-- | :-- |
| aldb001 | Foundations: SQL Server to AlloyDB with DMS, validate, connect, read pool | Read pool (1 node); Cloud SQL for SQL Server with the legacy `.bak` restored and CDC on; DMS APIs. **Stub this**: a parallel conversation is checking DMS and building the SQL Server package now (`state/handoff-module1-checks.md`), and its results set this switch |
| aldb002 | Search and AI (Colab Enterprise notebook) | **No setup VM.** The student's notebook creates and loads the database in Task 1 (~3 min measured), as the current Terraform already assumes |
| aldb003 | Agents (QueryData, managed MCP, ADK, secure views) | `dataApiAccess` patched on; QueryData, MCP, and Model Armor APIs and roles |
| aldb004 | Data cloud (federation, Datastream, Knowledge Catalog, Gemini Enterprise) | BigQuery history datasets; Datastream prerequisites; Knowledge Catalog and Gemini Enterprise APIs. **Stub** until its checks run |
| aldb005 | Deadline day (ops under load) | REGIONAL (HA) instance; observability config (`google-beta`); `enable_columnar_engine = true`; load-generator VM running at arrival; `pgbench` |

Common base: what `terraform/` provisions today.

## Read first

- `state/track-a-index-findings-and-next-steps.md`: where the build stands.
- `terraform/` and its `README.md`: current and proven. **Extend it; don't replace it.**
- Earlier CymbalGoal repos in `DevWork`, header comments included:
  - `cymbalgoal-data-agent/terraform/main.tf`: why `dataApiAccess` is a REST PATCH with no Terraform or gcloud equivalent.
  - `cymbalgoal-database-intelligence/terraform/` (observability via `google-beta`) and `workload/` (the deadline-day load generator to bring into this repo).
- Setup-VM patterns in `gcp-ce-content/labs/`: `ce997-basic-lab-template-startup-script/` (the template) and `ce398-london-summit-track-3/` (VM plus `remote-exec`).
- `gcp-ce-content/labs/mkt015-agentic-operations-database-intelligence/`: `qwiklabs.yaml` and `QL_OWNER` to model on.

## Build

1. **Module switches.** Add `lab_module` (`aldb001`–`aldb005`) and `track` (`cloud`; `omni` reserved for Track B, nothing else yet) to `terraform/`, driving a feature map in `locals`. With `lab_module = "aldb002"` the plan must match what applies today.
2. **Setup VM for aldb001, 003, 004, and 005.** It lives in our VPC, runs as a service account with the AlloyDB roles it needs, and is created after the instance. Its startup script clones this repo at a pinned tag and runs a bootstrap that:
    - creates the `cymbalgoal` database, extensions, and schema, and bulk-loads with `gcloud alloydb clusters import` from the staging bucket the Terraform already creates;
    - leaves the **student's IAM user owning the database** (the ownership trap: `alloydbsuperuser` isn't a real superuser, so a database owned by `postgres` can't be fully managed by the student; prove this works);
    - patches `dataApiAccess` for aldb003;
    - writes a status marker, and gives Task 0 a one-line check that prints "ready" or what's still loading;
    - stops the VM, except in aldb005, where it stays up as the load generator.

    Start Lab won't wait for a startup script; that's what the Task 0 check is for. Fallback if the VM can't be made reliable: the student starts the setup script in the background in Task 0 (the mkt014/015 pattern).
3. **Thin roots per lab.** Each lab folder's `terraform/` is a short root that sets `lab_module` and calls `source = "github.com/haggman/cymbalgoal-alloydb-workshop//terraform?ref=<tag>"`. Dozens of gcp-ce-content labs pull CloudVLab modules from GitHub this way. The repo must be public. Fallback: a vendored copy of the module.
4. **Lab folders.** Ask Patrick to create the `alloydb-track-a` branch in gcp-ce-content before you write there (it's currently on someone else's branch). Then create:

| Folder | Title |
| :-- | :-- |
| `aldb001-alloydb-foundations-migrate-connect-scale` | AlloyDB Foundations: Migrate, Connect, Scale |
| `aldb002-alloydb-ai-keywords-to-graphrag` | AlloyDB AI: From Keywords to GraphRAG |
| `aldb003-alloydb-agents-three-ways-to-ask` | AlloyDB Agents: Three Ways to Ask the Database |
| `aldb004-alloydb-data-cloud-federate-stream-sync` | AlloyDB and the Data Cloud: Federate, Stream, Sync |
| `aldb005-alloydb-deadline-day-operations` | AlloyDB Deadline Day: Operations Under Load |

Each lab folder gets `QL_OWNER` (copy mkt015's), a clean `qwiklabs.yaml` (one-line comments at most; `variant: gcp_medium_extra`; `allowed_locations: [us-central1]`; `duration: 180`; `max_duration: 360`; lab id first in tags; `custom_properties` for `username`), `instructions/en.md` with only the Title Case H1 and the four fragments (`startqwiklab`, `gcpconsole`, `endqwiklab`, `copyright`), and the thin `terraform/` root. Titles are provisional; the title lives in `qwiklabs.yaml`, the `en.md` H1, and the Terraform header, and they move together.

## Conventions

- Patrick handles all git. Write files to disk only.
- Never `gsutil` (use `gcloud storage`); `printf`, not bare `echo >>`; Terraform 1.12.1; apply twice and require `terraform plan -detailed-exitcode` to exit 0.
- The runtime injects only `gcp_project_id`, `gcp_region`, and `gcp_zone`; everything else comes through `custom_properties`.
- Runs: Patrick runs a script or notebook end to end and pastes back one handback block. No per-step checklists. Ask him to look at something specific only if the handback needs it.
- If something forces a design change to a module, stop and ask.
- Propose your plan before writing files.

## Done when

- aldb002 cold-starts through a thin root at Start Lab, and its notebook connects.
- One setup-VM module (aldb003 is simplest) cold-starts end to end with the bootstrap done in 20 minutes or less, measured.
- aldb001, 004, and 005 `validate` and `plan` cleanly with their stubs.
- The five lab folders exist.

## Hand back

Write `state/skeleton-handback.md`: a placement table (file → path → one-line note), the feature matrix as built with what's stubbed, measured cold-start times, the Task 0 check command, and anything the lab-writing conversations need to know.
