# Handback: the Cymbal Foods dataset

Answers `state/handoff-dataset.md` · written by the dataset conversation · updated after each stage · last update Oct 16, 2026 (Stage 1)

## Status

| Stage | State |
| :-- | :-- |
| 0 · Design doc | **Approved Oct 16, 2026** |
| 1 · Relational and clickstream generator | **Built and validated at full lab scale in the build environment** (0 problems, byte-for-byte reproducible). **Waiting on Patrick's run of notebook 10**, which stages it to the bucket |
| 2 · Text corpus | Next: notebook 11, 25 documents per type for review first. The fact tables it writes from already exist |
| 3 · Legacy SQL Server package | Not started; waits on the Module 1 checks (DMS SQL Server → AlloyDB) |
| 4 · Load test | Not started. A local PostgreSQL 16 check is done (below) |

## Patrick's run: notebook 10

One run in a temp Qwiklabs project. About 15–20 minutes; the default Colab Enterprise runtime is enough (the run peaks near 4 GB of memory).

1. Push the repo (new: `datagen/` and `pipeline/10-generate-dataset.ipynb`).
2. **Where:** Cloud Shell, as your own account (the `class-demo` owner). **What:** let the lab account write to the workshop prefix. Replace the first line with the lab's username:

```bash
LAB_USER=student-XX-XXXXXXXXXXXX@qwiklabs.net
gcloud storage buckets add-iam-policy-binding gs://class-demo \
  --member="user:${LAB_USER}" --role=roles/storage.objectAdmin \
  --condition='expression=resource.name.startsWith("projects/_/buckets/class-demo/objects/alloydb-labs/cymbalfoods-alloydb-workshop/"),title=cymbalfoods-workshop-prefix'
```

3. **Where:** Colab Enterprise in the lab project. **What:** upload `pipeline/10-generate-dataset.ipynb`, connect a runtime, **Run all**, and paste back the box the last cell (section 10 · Handback) prints.

If section 3 · The bucket guard reports the write probe FAILED, the grant didn't take; the cell prints the same command with the account filled in.

## Stage 1: what was built

| File | Path | Note |
| :-- | :-- | :-- |
| generate_dataset.py | `datagen/` | Command line: generate → validate → cut slices; `--cut-only` re-cuts from existing Parquet |
| config.py | `datagen/cfgen/` | Every knob: seed 20261016, scale, calendar, volumes, prices, demand shape, planted moments |
| util.py, content.py | `datagen/cfgen/` | Named seeded streams and deterministic UUIDs; content loader and checker (`python3 -m cfgen.content`) |
| catalog.py | `datagen/cfgen/` | Ingredients, products, blend recipes, gift sets, SKUs, prices and price history |
| people.py | `datagen/cfgen/` | Locations, staff, consumers, restaurant accounts (with look-alike names), usual baskets, contract prices |
| demand.py | `datagen/cfgen/` | Product demand by day and channel: popularity × season × dated events |
| orders.py | `datagen/cfgen/` | Orders and lines for stores, online and wholesale, with prices, totals, payment and status |
| supply.py | `datagen/cfgen/` | Suppliers; demand worked back to batches, lots and POs; FIFO lot draws; the stockout plant |
| inventory.py | `datagen/cfgen/` | Dec 31 inventory from what batches have left; manual reorder points |
| fraud.py | `datagen/cfgen/` | Risk scores and the planted fraud ring |
| facts.py | `datagen/cfgen/` | Fact tables for Stage 2's text: reviews (with planted themes), quality reports, account notes, recipes, product descriptions |
| clickstream.py | `datagen/cfgen/` | GA4-shaped events for 2026, one Parquet file per day |
| schemas.py | `datagen/cfgen/` | Single source for all 31 tables: DDL with COMMENT ON, constraints, index files, BigQuery and Spanner schemas |
| writer.py, slicer.py | `datagen/cfgen/` | Deterministic Parquet and PostgreSQL-CSV writers; slice cutting with closure and per-slice manifests |
| validate.py | `datagen/cfgen/` | DuckDB checks: keys, foreign keys, consistency, every planted moment |
| slices.yaml | `datagen/` | The five named slices |
| pg_load_check.py | `datagen/tools/` | Loads a slice into a local PostgreSQL the way the setup VM will, and times it |
| README.md, requirements.txt, Makefile, .gitignore | `datagen/` | How to run; libraries; `make` targets; ignores `out/` |
| us_cities.csv, names/*.csv | `datagen/content/` | **New:** 82 cities with ZIP prefixes and area codes; 1990 Census name lists |
| 10-generate-dataset.ipynb | `pipeline/` | Student-readable: generate, look at the data, validate, slice, stage, one handback. Tested end to end here against a stand-in bucket, both `REGENERATE = True` and the re-cut path |
| DESIGN.md | `datagen/` | **Updated:** section 0.1 lists what the build changed; P8, P13, P15 and the slice table carry measured numbers |
| README.md | `datagen/content/` | **Updated:** sources for names, origins (trade-data cross-check done), cities |

## Row counts and sizes (lab scale, full run Oct 16)

| Table | Rows | | Table | Rows |
| :-- | --: | :-- | :-- | --: |
| regions | 4 | | customers | 250,014 |
| locations | 44 | | restaurant_accounts | 4,000 |
| staff | 205 | | contract_prices | 212,544 |
| categories | 15 | | orders | 998,646 |
| uom_conversions | 20 | | order_lines | 3,073,267 |
| ingredients | 427 | | purchase_orders | 51,348 |
| suppliers | 150 | | po_lines | 90,678 |
| supplier_ingredients | 924 | | inbound_lots | 120,578 |
| products | 835 | | production_batches | 137,957 |
| blend_components | 1,288 | | batch_inputs | 353,934 |
| gift_set_items | 145 | | inventory_positions | 66,689 |
| skus | 3,379 | | reorder_points | 13,224 |
| price_history | 19,419 | | **total in AlloyDB (relational)** | **5,399,734** |

Stage 2 facts: 70,000 reviews, 15,000 quality reports, 10,000 account notes, 3,000 recipes (10,414 recipe-product links), 835 product descriptions: **98,835 documents**. GA4: **22,104,628 events**, 3.6M sessions, staged as 365 daily files `ga4/events_YYYYMMDD.parquet`, each loading as its own BigQuery table `ga4_export.events_YYYYMMDD` (date-sharded, as GA4 exports; decision D9).

Sizes: Parquet master 157 MB (+7.6 MB facts), GA4 356 MB, staged slices 670 MB (`full` 252 MB).

| Slice | Rows | CSV.gz | Files |
| :-- | --: | --: | --: |
| full | 5,399,734 | 252 MB | 29 |
| aldb002_search (Jul–Dec 2026, with supply) | 1,386,193 | 64 MB | 23 |
| aldb003_agents (Oct–Dec 2026) | 954,678 | 42 MB | 20 |
| aldb004_datacloud (2025–2026) | 3,412,037 | 161 MB | 22 |
| aldb005_ops (2025–2026, with supply) | 3,926,264 | 183 MB | 27 |

## Planted moments: where they live and what finds them

All verified by `cfgen/validate.py`; the answer key with queries is `validation/plants.json`. P1–P3 (the T-SQL traps) are Stage 3.

| ID | Measured | Query that finds it |
| :-- | :-- | :-- |
| P5 smoky, not hot | Smoked Paprika (Sweet) heat 0, Smokehouse Brisket Rub 1, Chipotle Meco 3, Smoked Paprika (Hot) 3 | vector search, then `heat_level <= 1` (Stage 2 adds the text) |
| P6 review themes | great value 4,811 · clumping 1,763 · too hot 1,752 · stale 968 · leaking 679 · wrong cinnamon 307 · musty smell 109 (of 70,000) | `ai.if` per theme against the ground truth in the facts |
| P7 humid DC | clumping 8.05% for DC-CE batches packed Jun–Sep vs 1.96% elsewhere | reviews → order_lines.batch_id → production_batches.dc_id |
| P8 failed lot | **L-ES-2608-0117**: 14 products, 26 batches on hold, **158 restaurants, 293 consumers, 12 stores, 716 orders** | recursive CTE from the lot code (works in PostgreSQL, tested) |
| P9 ambiguity | 22 groups; cinnamon has 4 ingredients and a Four Cinnamons Flight gift set | `ambiguity_group`, `aliases` |
| P10 private prices | 212,544 contract rows over 4,000 accounts, 60 reps | secure views by `account_rep_id` |
| P11 look-alikes | 20 business names shared by 47 accounts in different cities | `GROUP BY business_name HAVING count(*) > 1` |
| P12 seasonality | baking and pumpkin units peak Oct–Dec in all three years | monthly units by `seasonality_profile` |
| P13 can't ship | DC-CE Saigon on hand 0 (1,996 elsewhere); 203 substituted, 214 backordered from Dec 7; PO-26-014618 in transit, due Jan 7, 2027; Holiday Baking Collection 2026 last sold Dec 14, 3,272 GA4 views after | inventory join to GA4 `view_item` |
| P14 GA4 gap | 10,299 of 130,412 online 2026 orders (7.9%) have no purchase event | anti-join on `transaction_id = order_number` |
| P15 rush | Cyber Monday Nov 30, 2026: 1,626 online orders (3.9× the November mean of 414), peak 21:00 ET; stores peak Black Friday | orders per day and hour |
| P16 hot spots | indexes for Q07, Q11, Q13, Q18, Q20 absent from `indexes-baseline.sql`; answers in `indexes-ops-answers.sql` | Query Insights, Index Advisor |
| F1 fraud ring | 14 guest accounts (C19900000–C19900013), 2 ship-to addresses, 3 cards, 23 orders; 14 cancelled, 9 cleared by mistake | `GROUP BY ship_address_hash HAVING count(DISTINCT customer_id) >= 5` |

## Wall time and cost actuals

| Step | Time (2 vCPU, 8 GB) |
| :-- | --: |
| catalog and people | 12 s |
| orders | 52 s |
| supply | 93 s |
| inventory, facts, Parquet | 33 s |
| GA4 clickstream | 519 s |
| validation | 10 s |
| five slices | 292 s |
| **total** | **1,009 s (17 min)** |

Cloud cost so far: $0 (everything ran in the build environment). Local PostgreSQL 16 load of `aldb005_ops` (3.9M rows): COPY 25.5 s, constraints 5.0 s, indexes and ANALYZE 4.9 s, 808 MB; `aldb003_agents`: 12 s in all.

## For the skeleton (loader) conversation

- **Read the slice manifest:** `gs://class-demo/alloydb-labs/cymbalfoods-alloydb-workshop/dataset/v1/slices/<slice>/manifest.json`. Paths inside are relative to the slice folder (`../../sql/...`).
- **`schema_files` is a list:** `schema.sql` (relational) and, once Stage 2 adds corpus tables, `schema-corpus.sql`, which needs the `vector` extension first. Every `CREATE TABLE` is `IF NOT EXISTS`, with primary keys and NOT NULL only.
- **`load_order`** lists tables in dependency order. Each table has `column_order`, `rows`, and `files`: a list, because orders, order lines (and later reviews) come one file per year.
- **Files:** headerless CSV, gzip; NULL is an unquoted empty field; booleans `t`/`f`; timestamps `YYYY-MM-DD HH:MM:SS+00`; arrays as `{"a","b"}` literals. Tested with `\copy ... WITH (FORMAT csv)` on PostgreSQL 16.
- **`constraints`:** each foreign key carries the tables it `requires`, and the manifest lists only those the slice supports. Each statement adds the key `NOT VALID` inside a guard, then validates, so a re-run is safe (tested).
- **`index_files`:** per slice; every statement `IF NOT EXISTS` (re-run tested). `indexes-ops-answers.sql` is never loaded.
- **`extensions`:** in dependency order. The Stage 1 slices need only `pg_trgm`; Stage 2 adds `vector`, `alloydb_scann` and `google_ml_integration` to slices with corpus tables.
- **Load method:** the data is smaller than feared; locally the heaviest slice loads in 35 s. The open question for Stage 4 is the per-file overhead of server-side import, since a slice has 20–29 files.
- **aldb005 workload:** build it from DESIGN section 10's query catalog.

## For the lab-writing conversations

- The concrete facts are in the planted-moments table above; ids and the finding queries are in `validation/plants.json` and DESIGN section 5.
- `schema.sql` carries `COMMENT ON` for every table and column, written for agents and NL2SQL (units, allowed values, which joins mean what, what's confidential).
- The fraud ring's ship-to addresses (fictional) are 418 Quarry Ct, Houston TX 77041 and 2290 Depot Way, Newark NJ 07105; the data stores only their hashes.
- Names in data: database `cymbalfoods`; staff `@cymbalfoods.example`; consumer emails on example.com/.net/.org; phones NXX-555-01XX; UPCs in the in-store "2" range.

## Known limits and follow-ups

- **Generation is in memory** (about 4 GB at lab scale). Demo scale (10×) needs a ~40 GB runtime; Spanner scale (100×) needs the chunked rewrite the design called for. Not needed for Track A.
- **Slicing takes about 5 minutes**, mostly CSV formatting in pandas; fine for now.
- **The manifests' import estimates are pessimistic** (30 s charged per file). Stage 4 replaces them with measurements.
- **Patrick:** update the one-pager's sizing table for Google (about 850 products and 3,400 SKUs).
