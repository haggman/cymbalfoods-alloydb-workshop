# Handoff: the Cymbal Foods dataset

Oct 16, 2026 · Track A build, written by the planning conversation

## Context

Google's AlloyDB workshop (Track A, AlloyDB Cloud: five standalone Qwiklabs, aldb001–aldb005) has dropped its football dataset. Google approved **Cymbal Foods** to replace it. The same dataset will serve Track B (AlloyDB Omni) and a future Spanner workshop, so it is effectively Google's reference dataset for these trainings. This conversation designs and builds it. It writes no lab prose.

**The company (approved story).** Cymbal Foods began more than a century ago as a single spice counter whose house blends were sold to local cafés and hotels. Four generations later it runs three channels: about 40 retail stores, an online store, and wholesale supply to about 4,000 restaurants and caterers on contract pricing. Spices, herbs, chiles, and house blends are the signature category, alongside oils and vinegars, sauces and condiments, teas and coffee, and baking ingredients. Shelf-stable only: no produce, meat, dairy, or cold chain. About 150 suppliers in about 25 countries ship lots into 4 distribution centers. The original order system still runs on SQL Server, and the online store's Google Analytics 4 clickstream lands in BigQuery.

**What each module needs from the data** (plant these deliberately, and document where each lives):

| Module | Needs |
| :-- | :-- |
| 1 Foundations | A legacy SQL Server order database with 2014-era T-SQL: stored procedures for unit-of-measure conversion (oz, ½ lb, lb, 5 lb, 25 lb bulk) and tiered pricing by channel, a view, a scalar function, a trigger, and at least one construct a DMS/Gemini conversion is likely to get subtly wrong (rounding, MONEY math, implicit conversion) |
| 2 Search and AI | Exact identifiers (SKUs, lot numbers) for full-text search; descriptive text for semantic queries ("something smoky for brisket that isn't too hot"); reviews that mention things `ai.if` can filter (stale, clumping, too hot, great value); a graph of supplier → lot → blend component → blend → order → customer, so a failed lot can be traced to every restaurant that received it |
| 3 Agents | Ambiguous names ("cinnamon" = Ceylon, cassia, Saigon, Korintje; "chili powder" vs "chile powder"); per-restaurant contract prices that must stay private; store and region ownership for secure views |
| 4 Data cloud | Order history and clickstream big enough to live in BigQuery and Iceberg; clickstream joinable to live inventory ("viewed but can't ship"); a seasonal demand signal (fall baking, holiday gift sets) a BigQuery forecast can model and sync back as reorder points |
| 5 Day-2 ops | Skewed, seasonal activity with a holiday-rush peak the load generator can replay; analytical tables worth columnarizing; realistic hot spots for Query Insights and the Index Advisor |

Business-translation callouts will map these patterns to financial services, healthcare, telecom, and ad tech, so a light online-order fraud thread (orders flagged as suspicious) is welcome.

**Sizing (approved in the one-pager).**

| Data | Lab scale | Demo scale | Spanner scale |
| :-- | :-- | :-- | :-- |
| Products / SKUs | ~2,000 / ~12,000 | same | same |
| Customers | 250,000 consumers + 4,000 restaurant accounts | 10× | 100× |
| Orders / order lines (3 years) | 1M / 3M | 10M / 30M | 100M / 300M+ |
| Suppliers / POs / inbound lots | 150 / 60,000 / 120,000 | 10× POs and lots | 100× |
| Inventory positions (SKU × location × lot) | ~500,000 | 10× | 100× |
| Text documents with 3,072-dim embeddings | ~100,000 | ~500,000 | ~500,000 |
| GA4 clickstream events (BigQuery only) | ~20M | ~200M | 1B+ |
| Total in AlloyDB | ~5M rows + 100,000 vectors | ~50M rows + 500,000 vectors | n/a |

The lab-scale load must fit the cold-start budget: about 11 minutes after the cluster exists. Earlier measurements on the 8-vCPU lab cluster: 38,000 documents with 3,072-dim embeddings loaded in 26–52 s; a 1.6M-row relational set plus its vector index in about 3 minutes.

## Read first

- This repo: `state/track-a-index-findings-and-next-steps.md` (index findings: ScaNN tuning, the HNSW planner trap, columnar off by default), `state/skeleton-handback.md` (how data gets loaded at Start Lab: a setup VM running `terraform/bootstrap/bootstrap.py`, AlloyDB server-side import from GCS), and `pipeline/00`–`04` (the generation, embedding, load-test, and benchmark patterns that worked: bucket guards, checkpoints to GCS, sample-then-full, region-sharded embeddings, manifests, one handback block per run).
- `DevWork/cymbal-voyages/data/`: the seeded, deterministic generator from another lab (`generate_warehouse.py`, `cvgen/config.py` knobs, hand-curated `content/`, BigQuery schemas with column descriptions, a data dictionary, a Makefile). Use its structure as the model.

## Shape of the build

- **`datagen/`: a Python package** (the cymbal-voyages pattern): one config with every knob and a single scale factor, seeded and byte-for-byte reproducible, writing Parquet plus a manifest. It's code because it must be versioned, testable, and reused by the Omni and Spanner tracks.
- **`datagen/content/`: hand-curated reference inputs** Patrick can read and edit: the spice and product list (names, forms, origins, package sizes, price bands), supplier countries, store cities, category and seasonality curves. Where each came from is documented. Patrick wants a say in what's included and where the reference data comes from, so these are decision points, not afterthoughts.
- **`pipeline/` notebooks** that narrate and run each stage by calling `datagen/`. Students will read them to see how the data was made, so they explain as well as run.
- **Text corpus** (descriptions, reviews, recipes, supplier quality reports, restaurant account notes) in its own Colab Enterprise notebook: `gemini-3.8-flash` from the `global` endpoint, batch where possible, written from structured facts the generator produced (the model never invents numbers), embedded with `gemini-embedding-001` at 3,072 dims (regional endpoint only).
- **Spanner-ready schema:** no monotonically increasing keys (UUIDs or similar), and parent-child tables (orders → order lines, POs → PO lines) shaped so Spanner can interleave them.

## Stages (stop for Patrick's review after each)

0. **Design doc** (`datagen/DESIGN.md`): every table and column, keys, relationships, the distributions (popularity skew, seasonality, reorder cycles), where each module's teaching moment is planted, reference-data sources, the text corpus plan with counts per type, the SQL Server package plan, staging layout, and a size-and-load-time estimate per scale. **Nothing gets generated before Patrick approves this.**
1. **Relational and clickstream generator at lab scale**, with validation (row counts, referential integrity, planted moments findable by the queries the labs will use) and staging.
2. **Text corpus:** 25-document sample per type, stop for review, calibrate cost from the sample, then the full run and embeddings.
3. **Legacy SQL Server package:** the same schema rendered as 2014-era T-SQL plus the legacy procedures, exported as a `.bak`. A Module 1 checks conversation already built a CymbalGoal version (`pipeline/05-sqlserver-package.ipynb`, `pipeline/sqlserver/`) and is testing DMS with it. Reuse its build and restore mechanics; replace the schema and procedures.
4. **Load test** on a lab cluster (adapt `pipeline/02-load-test.ipynb`) to confirm the lab slice fits the cold-start budget, and trim the slice if it doesn't.

## Conventions

- Staging root: `gs://class-demo/alloydb-labs/cymbalfoods-alloydb-workshop/` with a `manifest.json` the bootstrap reads. Never write under `gs://class-demo/alloydb-labs/cymbalgoal/` (another team's labs depend on it).
- No real brands, people, restaurants, or businesses. Fake names come from a seeded generator, and a product's origin country is fine but its producer is fictional. No health or medical claims in generated text.
- Patrick handles all git; write files to disk only. Never `gsutil` (use `gcloud storage`); `printf`, not bare `echo >>`. Python 3.11+.
- Runs: Patrick runs a notebook or script end to end and pastes back one handback block. No per-step checklists. Notebook cells are named by section heading, never by number.
- Propose your plan before writing files. If something would change a module's design, stop and ask.

## What the loader needs (from the skeleton conversation, Oct 16)

The setup VM's bootstrap loads each lab's data at Start Lab and is moving to read everything from your manifest. The full contract and the reasons are in `state/skeleton-handback.md` ("Loader contract for the new dataset"). In short:

- One **headerless** gzip CSV per final table (embeddings inline, no second "UPDATE the embeddings" pass), NULL as an unquoted empty field, vectors as pgvector literals.
- `manifest.json` with each table's file, `column_order` and row count, the load order, named **table groups** (so each module can pick its groups), and the extensions each group needs, in dependency order.
- `schema.sql` with tables only; indexes in separate per-module SQL files, every statement `IF NOT EXISTS`.
- **Measured on real AlloyDB with CymbalGoal:** server-side import of 11 files plus index builds took roughly 7 to 8 minutes, against about 3 minutes for client COPY of the same data. Stage 4's load test should time both paths on the lab slice before the slice is fixed.

## Planning notes (from the planning conversation, Oct 16)

These refine the stages above after reading the skeleton handback.

- **Size the lab slice per module, not once for the whole dataset.** The ~5M-row lab scale is the full dataset; each setup-VM module loads only the table groups it needs. Each module's groups, plus their indexes, have to load inside that module's own budget. A 20-minute cold start leaves roughly:

| Module | Already spent before loading | Load + index budget |
| :-- | :-- | :-- |
| aldb001 | cluster (~9 min) + read pool + VM boot (~1 min) | small: DMS fills AlloyDB in the lab, so little or nothing preloads |
| aldb002 | none: the student's notebook loads in Task 1 | a few minutes of notebook time; keep the notebook load under ~5 min |
| aldb003 | cluster (~9 min) + VM boot (~1 min) + Data API restart (~2.3 min) | **~7 min, the tightest** |
| aldb004 | cluster + VM boot | ~9 min |
| aldb005 | REGIONAL cluster (likely slower to create than ~9 min) + VM boot | ~8 min, to be confirmed |

  For scale: the CymbalGoal set (1.66M rows plus 38,445 vectors) took 7–8 minutes through server-side import, and about 3 minutes by client COPY. So the design doc should propose the groups and an estimated per-module load, and stage 4 should time the heaviest setup-VM module's groups both ways.
- **Add an "application query catalog" to the design doc:** the queries the Cymbal Foods application runs (product search, store and online order lookups, inventory checks, restaurant reorders, order history), each tagged by feature. Module 5's holiday-rush load generator and its Query Insights hot spots will be built from it later. Only the list is needed now, not the generator.

## Names (decided by Patrick, Oct 16)

- `cymbalfoods` everywhere: database `cymbalfoods`, cluster `cymbalfoods-cluster`, staging bucket suffix `-cymbalfoods-staging`, repo `cymbalfoods-alloydb-workshop`. Use these in `schema.sql`, the manifest, and the notebooks.
- Module 5 is "AlloyDB Holiday Rush: Operations Under Load". The peak day in the data is the holiday rush.
- The Module 5 load generator is not built now; only the application query catalog is.

## Hand back

After each stage, update `state/dataset-handback.md`: what was built (placement table: file → path → one-line note), row counts and sizes per table, where each planted teaching moment lives and the query that finds it, cost and wall time actuals, and anything the skeleton (loader) or lab-writing conversations need to know.
