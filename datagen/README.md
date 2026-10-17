# Cymbal Foods dataset generator

The reference dataset for Google's AlloyDB workshop (Track A, aldb001–aldb005), and later AlloyDB Omni and Spanner. Cymbal Foods is a fictional, century-old specialty food retailer and foodservice distributor: 40 stores, an online store, and about 4,000 restaurant accounts on contract pricing. `DESIGN.md` explains every table, behavior and planted teaching moment.

```
make check          # validate content/ and project product and SKU counts
make                # generate, validate and cut every slice into out/  (~17 min on 2 vCPU)
make slices         # re-cut slices.yaml from the existing Parquet (no regeneration)
make pgcheck SLICE=aldb005_ops   # load a slice into a local PostgreSQL the way the setup VM will
```

`pipeline/10-generate-dataset.ipynb` runs the same steps in Colab Enterprise and stages the result to `gs://class-demo/alloydb-labs/cymbalfoods-alloydb-workshop/dataset/v1/`. `pipeline/11-text-corpus.ipynb` then writes the text corpus with Gemini (a 25-per-type sample first, then the full run), embeds it, adds the six corpus tables to `parquet/` and re-cuts the slices.

| Path | What |
| :-- | :-- |
| `DESIGN.md` | The design: tables, behavior, planted moments, corpus, SQL Server package, slices, sizes |
| `content/` | Hand-curated reference data (spices, blends, pantry, packs, stores, seasonality, events…). `content/README.md` says where each came from |
| `cfgen/config.py` | Every knob: seed, scale, calendar, volumes, prices, demand shape, planted moments |
| `cfgen/` | The generator: `catalog`, `people`, `demand`, `orders`, `supply`, `inventory`, `fraud`, `facts`, `clickstream`, `schemas`, `writer`, `slicer`, `validate` |
| `cfgen/corpus.py` | Stage 2: the fact block, prompt, checks and final table for each of the five document types |
| `cfgen/gemini.py` | Stage 2: the throttled, retrying, checkpointing Gemini pool and embeddings (CymbalGoal notebook 01's machinery) |
| `cfgen/fake_gemini.py` | An offline stand-in for Gemini (`CF_FAKE_GEMINI=1`), only for testing notebook 11 |
| `slices.yaml` | The named slices each lab module loads |
| `generate_dataset.py` | The command line: generate → validate → cut slices |
| `tools/pg_load_check.py` | Loads a slice into a local PostgreSQL exactly as the setup VM does, and times it |

## What a run writes (`out/`)

| Path | What |
| :-- | :-- |
| `parquet/<table>/part-*.parquet` | The master copy, one folder per table (orders and order lines split by year) |
| `parquet/_facts/*.parquet` | Fact blocks for the Stage 2 text corpus (reviews, quality reports, account notes, recipes, product descriptions) |
| `ga4/events_YYYYMMDD.parquet` | GA4 clickstream for 2026, one file per day; each loads into BigQuery as its own `events_YYYYMMDD` table, as a GA4 export writes them |
| `sql/` | `schema.sql`, `schema-corpus.sql`, `constraints.sql`, `indexes-*.sql`, `spanner-schema.sql` |
| `bigquery/schemas/*.json` | BigQuery schemas with column descriptions |
| `slices/<slice>/` | Headerless `*.csv.gz` per table, the `manifest.json` the setup VM's loader reads, and `indexes-vector.sql` (ScaNN, num_leaves ≈ √rows) once the slice has corpus tables |
| `validation/report.json`, `validation/plants.json` | Counts, keys, foreign keys, consistency checks and every planted moment with the query that finds it |
| `manifest.json` | Seed, scale, library versions, row counts, sizes, timings |

## Determinism

One master seed in `cfgen/config.py`; every module draws from named streams (`cfgen/util.rng`). Two runs with the same seed, content and library versions produce identical Parquet, CSV and GA4 files. Tested Oct 16, 2026: two runs, 521 Parquet and CSV files, zero differences.

## Scale

`--scale 1` is lab scale (~5.4M rows in AlloyDB, ~22M GA4 events). `--scale 10` (demo) and `--scale 100` (Spanner) multiply customers, orders, lots and clickstream. The current implementation holds a run in memory (about 4 GB at lab scale), so demo scale needs a high-memory runtime and Spanner scale needs the chunked rewrite noted in `state/dataset-handback.md`.
