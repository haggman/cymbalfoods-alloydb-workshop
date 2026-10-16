# Cymbal Foods dataset: design

Oct 16, 2026 · Stage 0 of `state/handoff-dataset.md` · **for Patrick's review; nothing is generated until this is approved**

This is the reference dataset for Track A (AlloyDB, aldb001–aldb005), and later Track B (AlloyDB Omni) and the Spanner workshop. It covers every table and column, how the data behaves, where each module's teaching moment is planted, the text corpus, the legacy SQL Server package, how the files are staged and sliced, and what it should cost in size and load time.

## 0 · Decisions so far, and what's open

**Decided (Oct 16):**

| # | Decision |
| :-- | :-- |
| D1 | Stage 0 = this document plus draft reference lists in `datagen/content/` |
| D2 | Calendar: **Jan 1, 2024 – Dec 31, 2026**, three full years. "Now" is the end of Dec 31, 2026. The holiday rush peak is **Cyber Monday, Nov 30, 2026**. GA4 clickstream covers calendar 2026 |
| D3 | US only, USD. Four DCs: Edison NJ (Northeast), McDonough GA (Southeast), Houston TX (Central, the humid Gulf DC), Ontario CA (West). 40 stores in 37 cities. No headquarters city |
| D4 | The legacy SQL Server uses `UNIQUEIDENTIFIER` keys (merge-replication backstory), so keys are identical in SQL Server, AlloyDB and Spanner. The `.bak`'s year range waits on the Module 1 checks |
| D5 | New notebooks are `pipeline/10`–`13`; Patrick moves the CymbalGoal `00`–`05` to `archive/cymbalgoal/` when the new ones land |
| D6 | Generate the full dataset once. Modules load **named slices** (groups × date window × optional cap) cut from the same Parquet, and a slice can be re-cut without regenerating (section 9) |
| D7 | Catalog: keep the curated lists (~835 products, ~3,460 SKUs). Patrick updates the sizing table Google saw. Vector and load timings are sized by the corpus and the order history, which don't depend on catalog size (section 1.3) |
| D8 | The answer key (`validation/plants.json`) lives in the anonymous-read bucket with the rest of the dataset |
| D9 | GA4 clickstream is **date-sharded daily tables** `ga4_export.events_YYYYMMDD`, exactly as a GA4 export writes them (queried with `events_*` and `_TABLE_SUFFIX`). Corrected Oct 16: Patrick agreed with the original sharded recommendation; an earlier version of this row said partitioned |

**Open:** none. Stage 0 approved Oct 16, 2026.

## 0.1 · Stage 1: what changed while building (Oct 16)

Stage 1 is built, run at full lab scale and validated. Where the build differs from the design below, this list wins.

| Topic | Design said | Built and measured |
| :-- | :-- | :-- |
| `ingredients` | 163 + 56 pantry base items | **427**: 163 spices plus one supply item per pantry product (an Arbequina lot is not a Picual lot) |
| `staff` | ~320 | **205** (3 per store, 15 reps and 6 DC staff per region, 5 head office) |
| `contract_prices` | ~160,000 | **212,544** (8–25 contract SKUs per account, one row per contract year) |
| Supply | 60,000 POs, ~110,000 batches | **51,348 POs**, 90,678 PO lines, **120,578 lots**, **137,957 batches**, 353,934 batch inputs |
| `inventory_positions` | ~270,000 | **66,689**: DCs hold the recent batches of every SKU; stores hold what they sold in the last 120 days |
| `reorder_points` | ~14,000 | **13,224** |
| Failed lot (P8) | used in 3 blends | drawn by every DC-NE batch needing sweet smoked paprika in the window: 14 products, 158 restaurants, 293 consumers, 12 stores |
| Stockout (P13) | Dec 10 | **Dec 7** (Monday, so batch coverage and substitutions agree); the in-transit PO is due Jan 7, 2027 |
| Peak (P15) | ~6× a normal day | **3.9×** (1,626 online orders on Cyber Monday 2026); stores peak on Black Friday |
| GA4 | ~20M events, one partitioned table | **22,104,628 events** in **365 daily files → `events_YYYYMMDD` tables** (D9), 3.6M sessions, 3.3% conversion, 7.9% of online orders without a purchase event |
| Customer `created_at` | — | 3,065 customers' records were created at checkout, just before their first order (realistic, and no order predates its customer) |
| `schema.sql` | one file | **split**: `schema.sql` (relational) and `schema-corpus.sql` (vector tables, needs the `vector` extension); manifests list `schema_files` |
| Names | Census 2010 and SSA | **1990 Census name lists** (public domain): the Census and SSA sites are not reachable from the build environment, so the lists come from the `names` 0.3.0 package's data files |
| Scale | chunked by month, bounded memory | **in memory** (peak ~3.7 GB at lab scale). Demo scale needs a ~40 GB runtime; Spanner scale needs the chunked rewrite (later) |
| Determinism | byte-for-byte | **verified**: two runs, 521 data files, zero differences |
| Load | estimates only | a local PostgreSQL 16 loads the heaviest setup-VM slice (`aldb005_ops`, 3.9M rows) in **35 s** (COPY 25.5 s, constraints 5 s, indexes 4.9 s; 808 MB). AlloyDB through a setup VM will be slower; Stage 4 measures it |

## 1 · The company in data

### 1.1 Story facts the data must honor

Cymbal Foods began more than a century ago as a single spice counter whose house blends went to local cafés and hotels. It runs three channels: about 40 retail stores, an online store, and wholesale supply to about 4,000 restaurants and caterers on contract pricing. Spices, herbs, chiles and house blends are the signature category, alongside oils and vinegars, sauces and condiments, teas and coffee, and baking ingredients. Everything is shelf-stable: no produce, meat, dairy or cold chain. About 150 suppliers in about 25 countries ship lots into 4 distribution centers. The original order system still runs on SQL Server, and the online store's GA4 clickstream lands in BigQuery.

Details the data adds (all fictional): store ST-001 in Greenwich Village is the descendant of the original counter. Two stores open inside the window (Austin Mueller in Mar 2025, Sacramento Midtown in Aug 2026), so "same-store sales" has a real edge case. Growth is about +20% a year online, +3% in stores and +8% in wholesale.

### 1.2 Scales

One knob, `SCALE`, multiplies customers, orders, POs, lots and clickstream. The catalog, stores, DCs and suppliers stay fixed at every scale, and the planted moments carry the same codes at every scale.

| | Lab (`SCALE=1`) | Demo (`SCALE=10`) | Spanner (`SCALE=100`) |
| :-- | --: | --: | --: |
| Consumers / restaurant accounts | 250,000 / 4,000 | 2.5M / 40,000 | 25M / 400,000 |
| Orders / order lines | 1.0M / 3.0M | 10M / 30M | 100M / 300M |
| POs / inbound lots | 60,000 / 120,000 | 600,000 / 1.2M | 6M / 12M |
| Text documents with embeddings | ~100,000 | ~500,000 (capped) | ~500,000 (capped) |
| GA4 events (BigQuery only) | ~20M | ~200M | ~2B |

Demo and Spanner scale are separate runs of the same generator. They are not supersets of the lab data.

### 1.3 Catalog size (decided Oct 16: A, the curated catalog)

The approved one-pager says ~2,000 products and ~12,000 SKUs. The reference lists as curated give:

| Source | Products | SKUs |
| :-- | --: | --: |
| `spices.csv` (163 ingredients, forms × origin variants + organic) | 349 | 1,577 |
| `blends.csv` (143 blends + variants) | 186 | 954 |
| `pantry.csv` (oils 32, vinegars 29, sauces 33, condiments 42, tea 54, coffee 16, baking 58) | 264 | 859 |
| `gift_sets.csv` (24 sets, annual editions) | 36 | 72 |
| **Total** | **835** | **3,462** |

That is a believable catalog for a four-generation specialty merchant. Reaching 2,000 / 12,000 needs one of two things:

- **A. Keep the honest catalog** (recommended). Update the sizing table to ~850 products / ~3,500 SKUs. Inventory positions drop from ~500,000 to ~270,000, and the total AlloyDB rows from ~5.6M to ~5.4M. No module's teaching moment changes.
- **B. Grow to ~2,000 / ~12,000 inside the approved five categories.** Add ~1,100 curated items: specialty flours, baking chocolate by origin and cacao percentage, more teas, more sauces, marinades and dressings, annual limited-edition blends (discontinued after their season), and foodservice grinds (16-mesh pepper, restaurant-grind garlic). Add wholesale case packs and a 2 lb size to reach ~6 SKUs per product. This costs Patrick another review pass over the new lists.

**Decided Oct 16: A.** Every count below assumes it. The vector timings Track A needs come from the corpus (~99,000 documents, 70,000 of them reviews, which scale with orders, not products) and the relational volume comes from orders, lines and supply, so neither shrinks with the catalog. If a benchmark needs more vectors, the review count is the knob.

## 2 · Principles

1. **Seeded and byte-for-byte reproducible.** One master seed. Each generator module and each chunk (table × month) draws from its own stream (`SeedSequence.spawn` keyed by name), so the output doesn't depend on chunking or parallelism. UUIDs come from those streams too. The only wall-clock value is `generated_at` in the manifest. Dependencies are pinned, and gzip writes with `mtime=0`.
2. **Parquet is the master copy.** Staged CSVs, BigQuery loads, the SQL Server package and every slice are cut from it. Re-cutting never regenerates.
3. **Facts first, text second.** The generator writes every number, date and relationship. Gemini only turns structured fact blocks into prose, and QA rejects text that adds facts (section 6).
4. **Spanner-ready.** Transactional tables have random UUID keys (never monotonic). Children carry the parent's key first (`(order_id, line_no)`), so Spanner can interleave them. Types map one to one onto Spanner (section 11).
5. **Readable codes alongside keys.** SKUs (`CF-10427-B08`), lot codes (`L-ES-2608-0117`), batch codes, order numbers (`O-26-0412873`) and account numbers exist for exact-match search, for agents, and so people can read the lab SQL.
6. **No real brands, people or businesses.** No health claims. No sanctioned origins (see `content/README.md`).

## 3 · Tables

PostgreSQL types as `schema.sql` will declare them. Database `cymbalfoods`, schema `public`. "PK" = primary key. Every timestamp is `timestamptz` in UTC; local time comes from the location's `time_zone`.

### 3.1 Reference and places

**`regions`** (4): `region_id text PK` (NE, SE, CE, WE) · `name text` · `dc_location_id text`

**`locations`** (44): `location_id text PK` (DC-NE, ST-001…) · `location_type text` (dc | store) · `name text` · `region_id text` · `street_address text` (generated) · `city text` · `state text` · `postal_code text` · `latitude numeric(8,5)` · `longitude numeric(8,5)` · `time_zone text` · `opened_on date` · `sq_ft integer`

**`staff`** (~320): `staff_id text PK` (E10001…) · `full_name text` · `email text` (`@cymbalfoods.example`) · `role text` (store_manager | assistant_manager | regional_manager | account_rep | dc_manager | buyer | quality_analyst | merchandiser) · `location_id text` · `region_id text` · `hired_on date`. Secure views and the account notes' authors use this.

**`categories`** (~15): `category_id text PK` (SPICE, HERB, CHILE, PEPPER, SALT, AROMATIC, BLEND, OIL, VINEGAR, SAUCE, CONDIMENT, TEA, COFFEE, BAKING, GIFT) · `name text` · `department text`

**`uom_conversions`** (~20): `from_uom text` · `to_uom text` · `factor numeric(12,6)`, PK (from_uom, to_uom). oz, half_lb, lb, 5lb, 25lb, g, kg, fl_oz, ml, gal.

### 3.2 Catalog and supply base

**`ingredients`** (163 + 56 pantry base items): what lots are bought as. `ingredient_id text PK` (the content slug) · `name text` · `ingredient_type text` · `botanical_name text` · `ambiguity_group text` · `aliases text[]` · `heat_level smallint` (0–5) · `scoville_min integer` · `scoville_max integer` · `flavor_notes text[]` · `primary_origins text[]`

**`suppliers`** (150): `supplier_id uuid PK` · `supplier_code text` (SUP-0001) · `name text` (generated) · `country_code text` · `supplier_kind text` (grower_exporter | processor | trading_house | co_packer | importer) · `quality_tier text` (A | B | C) · `onboarded_on date` · `is_active boolean`

**`supplier_ingredients`** (~900): `supplier_id uuid` · `ingredient_id text` · `origin_country text` · `is_primary boolean` · `lead_time_days smallint`, PK (supplier_id, ingredient_id, origin_country)

**`products`** (~835): `product_id text PK` (P10001…) · `name text` · `category_id text` · `product_type text` (single_origin | blend | pantry | gift_set) · `ingredient_id text` (single-origin only) · `form text` · `origin_country text` · `is_organic boolean` · `is_salt_free boolean` · `heat_level smallint` · `flavor_tags text[]` · `cuisine_tags text[]` · `allergens text[]` (sesame, mustard, wheat, soy, fish, tree nut, from the components) · `seasonality_profile text` · `ambiguity_group text` · `launched_on date` · `discontinued_on date` (annual gift editions, a few retired items)

**`blend_components`** (~1,100): `product_id text` · `ingredient_id text` · `pct numeric(5,2)`, PK (product_id, ingredient_id). The recipe, and the edge from ingredient to blend in the trace graph.

**`gift_set_items`** (~110): `gift_product_id text` · `product_id text` · `quantity smallint`, PK (gift_product_id, product_id)

**`skus`** (~3,460): `sku_id text PK` (CF-10427-B08: product number and pack code; the hyphens make it a useful exact-identifier search test) · `product_id text` · `pack_code text` · `pack_description text` · `container text` · `net_qty numeric(8,2)` · `net_uom text` · `net_oz numeric(8,3)` · `upc text` (12 digits, "2" prefix: the in-store range, so no collision with real products) · `sold_in_stores boolean` · `sold_online boolean` · `sold_wholesale boolean` · `list_price numeric(10,2)` (current retail) · `wholesale_price numeric(10,2)` (current tier list) · `unit_cost numeric(10,4)` · `status text` · `introduced_on date`

**`price_history`** (~25,000): `sku_id text` · `channel text` (retail | wholesale) · `effective_from date` · `price numeric(10,2)` · `change_reason text`, PK (sku_id, channel, effective_from). Annual increases every January, plus supply-driven moves (vanilla +18% Apr 2025, saffron +9% Sep 2025).

### 3.3 Customers

**`customers`** (250,000 consumers): `customer_id uuid PK` · `customer_number text` (C########) · `first_name text` · `last_name text` · `email text` (example.com, example.net, example.org) · `phone text` (NXX-555-01XX, the range reserved for fiction) · `street_address text` · `city text` · `state text` · `postal_code text` · `home_store_id text` (nearest store, may be null) · `serving_dc_id text` · `created_at timestamptz` · `account_type text` (registered | guest) · `loyalty_tier text` (none | member | plus) · `marketing_segment text` · `email_opt_in boolean`

**`restaurant_accounts`** (4,000): `account_id uuid PK` · `account_number text` (W######) · `business_name text` · `business_type text` · `cuisine text` · `contact_name text` · `contact_email text` · `phone text` · `street_address text` · `city text` · `state text` · `postal_code text` · `region_id text` · `serving_dc_id text` · `account_rep_id text` (→ staff) · `price_tier text` (T1 | T2 | T3: volume tiers) · `credit_terms text` · `contract_start date` · `contract_end date` · `status text` · `created_at timestamptz`

**`contract_prices`** (~160,000): `account_id uuid` · `sku_id text` · `effective_from date` · `effective_to date` · `contract_price numeric(10,2)`, PK (account_id, sku_id, effective_from). Each account's "usual" SKUs at 3–12% below its tier price, renewed when its contract renews. **Private by design**: Module 3's secure views expose an account's prices only to that account's rep.

### 3.4 Orders

**`orders`** (1,000,000): `order_id uuid PK` · `order_number text` (S-/O-/W- + yy + 7 digits) · `channel text` (store | online | wholesale) · `customer_id uuid` (null for anonymous store sales and wholesale) · `account_id uuid` (wholesale only) · `location_id text` (the store, or the fulfilling DC) · `ordered_at timestamptz` · `status text` (completed | shipped | delivered | cancelled | returned) · `ship_city`, `ship_state`, `ship_postal_code text` · `ship_address_hash text` (for fraud matching without exposing an address) · `subtotal`, `discount_total`, `shipping_total`, `tax_total`, `order_total numeric(12,2)` · `promo_code text` · `payment_method text` (card | cash | invoice | gift_card) · `payment_fingerprint text` (a hash; never a card number) · `device text` (online: web | app) · `risk_score numeric(4,3)` (online only) · `risk_flags text[]` · `risk_review text` (none | cleared | cancelled_fraud | pending)

**`order_lines`** (3,000,000): `order_id uuid` · `line_no smallint` · `sku_id text` · `quantity integer` · `unit_price numeric(10,2)` · `price_source text` (list | tier | contract | promo) · `extended_price numeric(12,2)` · `batch_id uuid` (the finished lot shipped or sold; the trace edge) · `line_status text` (fulfilled | substituted | backordered | returned) · `substituted_from_sku text` · `return_reason text`, PK (order_id, line_no)

Channel mix (lab scale, three years):

| Channel | Orders | Lines per order | Lines | Identified customer |
| :-- | --: | --: | --: | :-- |
| Store | ~470,000 | 1.3 | ~610,000 | ~45% (loyalty scan) |
| Online | ~330,000 | 2.8 | ~920,000 | 100% (guest checkout makes a guest customer) |
| Wholesale | ~200,000 | 7.4 | ~1,470,000 | 100% (account) |

Every year's orders are a separate staged file, so a slice can take any year range.

### 3.5 Supply and traceability

**`purchase_orders`** (60,000): `po_id uuid PK` · `po_number text` (PO-26-012345) · `supplier_id uuid` · `destination_dc_id text` · `ordered_on date` · `expected_on date` · `received_on date` · `status text` (open | in_transit | received | cancelled) · `freight text` · `total_cost numeric(14,2)`

**`po_lines`** (~90,000): `po_id uuid` · `line_no smallint` · `ingredient_id text` · `origin_country text` · `quantity_kg numeric(10,2)` · `unit_cost_per_kg numeric(10,4)`, PK (po_id, line_no)

**`inbound_lots`** (120,000): `lot_id uuid PK` · `lot_code text` (L-<origin ISO>-<yymm>-<seq>) · `po_id uuid` · `po_line_no smallint` · `supplier_id uuid` · `ingredient_id text` · `origin_country text` · `harvest_year smallint` · `dc_id text` · `received_at timestamptz` · `quantity_kg numeric(10,2)` · `moisture_pct numeric(4,2)` · `volatile_oil_pct numeric(4,2)` · `qc_status text` (released | hold | rejected) · `qc_released_at timestamptz` · `best_by date`. A PO line often arrives as several supplier lots, which is how 90,000 lines become 120,000 lots.

**`production_batches`** (~110,000): `batch_id uuid PK` · `batch_code text` (B-<dc>-<yymmdd>-<seq>) · `batch_type text` (pack | blend | receive_finished | kit) · `product_id text` · `dc_id text` · `produced_at timestamptz` · `quantity_kg numeric(10,2)` · `best_by date` · `qc_status text`

**`batch_inputs`** (~330,000): `batch_id uuid` · `input_no smallint` · `lot_id uuid` · `quantity_kg numeric(10,2)`, PK (batch_id, input_no). A pack batch has one or two lots; a blend batch has one lot per component.

The trace graph is: **supplier → inbound_lot → batch_input → production_batch (blend or pack) → order_line → order → customer or restaurant**. The same tables serve plain SQL, a PostgreSQL 19 property graph, and later Spanner Graph.

### 3.6 Inventory

**`inventory_positions`** (~270,000, as of Dec 31, 2026): `location_id text` · `sku_id text` · `batch_id uuid` · `on_hand integer` (units) · `reserved integer` · `best_by date` · `last_counted_at timestamptz`, PK (location_id, sku_id, batch_id). DCs hold every active SKU, a few open batches each; stores hold their retail range, one or two batches each.

**`reorder_points`** (~14,000): `location_id text` (DCs) · `sku_id text` · `reorder_point integer` · `reorder_qty integer` · `source text` (manual | forecast) · `updated_at timestamptz`, PK (location_id, sku_id). All `manual` at the start; Module 4's forecast writes `forecast` rows back.

### 3.7 Text corpus tables (each with a `vector(3072)` column)

| Table | Rows | Key columns | Embedded text |
| :-- | --: | :-- | :-- |
| `product_descriptions` | ~835 | `product_id text PK`, `description text`, `description_embedding` | the description |
| `reviews` | 70,000 | `review_id uuid PK`, `product_id`, `sku_id`, `customer_id`, `order_id`, `line_no`, `rating smallint`, `title`, `body`, `submitted_at`, `verified_purchase`, `helpful_votes`, `body_embedding` | title + body |
| `recipes` | 3,000 | `recipe_id uuid PK`, `title`, `cuisine`, `course`, `servings`, `total_minutes`, `ingredients_text`, `instructions`, `recipe_embedding`; plus `recipe_products (recipe_id, product_id)` | title + ingredients + instructions |
| `supplier_quality_reports` | 15,000 | `report_id uuid PK`, `lot_id`, `supplier_id`, `report_type` (receiving_inspection, coa_review, retest, supplier_audit), `inspected_at`, `result` (pass, conditional, fail), `report_text`, `report_embedding` | report_text |
| `account_notes` | 10,000 | `note_id uuid PK`, `account_id`, `staff_id`, `noted_at`, `note_type` (call, visit, email, complaint), `note_text`, `note_embedding` | note_text |

Reviews carry `order_id` and `line_no`, so a review joins to the batch it came from. That is the hinge for the clumping and failed-lot moments. The review themes that `ai.if` should find are ground truth in `validation/plants.json`, never columns in the table.

### 3.8 BigQuery only

**`ga4_export.events_YYYYMMDD`** (365 daily tables for calendar 2026, ~22M events; each table holds only its own US-Eastern day): the GA4 export shape, trimmed to the fields a lab would touch. `event_date STRING` · `event_timestamp INT64` (µs) · `event_name` (session_start, page_view, view_search_results, view_item_list, view_item, add_to_cart, remove_from_cart, begin_checkout, add_shipping_info, add_payment_info, purchase) · `event_params ARRAY<STRUCT<key, value STRUCT<string_value, int_value, double_value>>>` (ga_session_id, page_location, search_term, …) · `user_id` (customer_number when signed in) · `user_pseudo_id` · `device STRUCT<category, operating_system, web_info STRUCT<browser>>` · `geo STRUCT<country, region, city>` · `traffic_source STRUCT<name, medium, source>` · `ecommerce STRUCT<transaction_id, purchase_revenue, shipping_value, tax_value, total_item_quantity>` · `items ARRAY<STRUCT<item_id (sku_id), item_name, item_category, price, quantity>>` · `platform`

Each 2026 online order has a `purchase` event with `transaction_id = order_number`, **except about 8%**, lost to consent banners and ad blockers as in real GA4 data. Reconciling the two is a Module 4 talking point. About 4.0M sessions at roughly 3% conversion and ~5 events per session give ~20M events.

The order history also goes to BigQuery (Module 4) from the same Parquet. Its BigQuery schemas, with column descriptions, are generated from the same source as `schema.sql`.

## 4 · How the data behaves

**Demand.** Each product has a base popularity (Zipf, s ≈ 1.1 over the content's 1–5 popularity ranks: the top 10% of SKUs carry about 60% of units). It also has a monthly seasonality profile (`seasonality.csv`), dated event spikes (`events.csv`), a weekday pattern per channel, and an hourly pattern per channel and time zone:

| Channel | Weekly shape | Daily shape |
| :-- | :-- | :-- |
| Store | Saturday high, Monday low | 10:00–19:00 local, peak 12:00 and 17:00 |
| Online | Sunday and Monday evenings high | Peak 20:00–22:00 local |
| Wholesale | Monday and Tuesday ordering days | Before 11:00 local; account reps' cutoff |

**Consumers.** Each has a segment (`consumer_segments.csv`), a lognormal activity level, a home store and serving DC by geography, and churn (about 25% a year with no further orders). Gift givers concentrate in Nov–Dec. Pack sizes follow channel: jars and refill bags in stores, more ½ lb and 1 lb online.

**Restaurants.** Each account has a cuisine (`cuisines.csv`) and a "usual" basket of 12–90 SKUs, drawn by tag affinity and weighted to wholesale packs (1 lb, 5 lb, 25 lb). It reorders on a lognormal cadence around the cuisine's median, with seasonal quantity swings (BBQ in summer, caterers in December), occasional substitutions when a SKU is out, and contract prices on its usuals.

**Prices.** A per-pound base by price band. Per-ounce price falls with pack size (a 25 lb box costs about 45% per ounce of a jar). Store and online share list prices; online runs promo codes. Wholesale tiers T1/T2/T3 are 10/15/20% off wholesale list, and contracts go 3–12% below tier on usuals. Increases every January 1, plus the supply moves in `price_history`.

**Supply.** Demand by product, DC and week is netted against stock to plan pack and blend batches. Batches draw lots first-in, first-out (FIFO) from released lots at that DC. Lot demand becomes POs to the ingredient's suppliers at each DC, with lead times from `supplier_countries.csv`. Received lots get a quality result: about 97% pass, 2.5% conditional, 0.5% fail and are rejected. Stores replenish from their region's DC weekly. Order lines take the oldest available batch at the store or DC. Inventory on Dec 31 is what's left, so it's consistent with the flows by construction.

**Fraud.** About 0.4% of online orders get a `risk_score` above the review threshold. About 70% of those are cleared as false positives, which is realistic and makes the planted ring stand out (section 5).

**Dirty edges, kept small.** About 0.3% of consumer emails in mixed case, phone numbers in three formats, a few hundred customers missing `home_store_id`, and 20 restaurant business names shared by two or three accounts in different cities. The legacy package adds more (section 7).

## 5 · Planted teaching moments

Every plant is written to `validation/plants.json` with its ids, the counts it should produce, and the query that finds it. Stage 1's validation runs those queries against the Parquet, through DuckDB, and fails the build if a count is off. All plants sit in **2026**, so any slice that includes the latest months keeps them.

| ID | Module | The moment | Where it lives | Finding query (sketch) |
| :-- | :-- | :-- | :-- | :-- |
| P1 | 1 | **MONEY rounding.** Per-ounce contract pricing stored as MONEY (4 decimals) then multiplied by 400 oz for a 25 lb box leaves some invoice lines a cent off after conversion | Legacy `fn_PricePerOz` and `usp_GetPrice`; `plants.json` lists ~40 (account, SKU) pairs where the SQL Server and exact answers differ | Smoke test compares `usp_GetPrice` before and after migration |
| P2 | 1 | **Case-insensitive search.** `usp_SearchProducts 'saigon cinnamon'` finds rows on SQL Server and none after a literal conversion | Legacy procedure + default CI collation | Smoke test, three casings |
| P3 | 1 | **FLOAT rounding.** Legacy weights are FLOAT; `ROUND(weight_lb * 16, 0)` on exact halves differs where the converted code rounds `double precision` half-to-even | `usp_QuoteBulkOrder` | Smoke test on the planted half-ounce quantities |
| P4 | 2 | **Exact identifiers.** SKU and lot codes with hyphens; semantic search can't find `CF-10427-B08`, and full-text search tokenizes it unless configured | `skus.sku_id`, `inbound_lots.lot_code` | BM25 vs. vector on code queries |
| P5 | 2 | **"Something smoky for brisket that isn't too hot."** Smoked Paprika (Sweet) and Smokehouse Brisket Rub rank first, Chipotle Meco and Smoked Paprika (Hot) rank near but run too hot, so a heat filter or `ai.rank` changes the order | `product_descriptions`, `products.heat_level` | Vector top 10, then with `heat_level <= 1` |
| P6 | 2 | **Review themes for `ai.if`.** Clumping 2.5%, stale or weak aroma 2%, too hot 3%, great value 6%, wrong-cinnamon surprise 0.5%, leaking bottle 1% | `reviews` | `ai.if` per theme vs. ground truth |
| P7 | 2 | **Humid DC clumping.** Clumping reviews run about 4× the baseline on ground spices and salt blends packed at DC-CE (Houston) Jun–Sep | `reviews` → `order_lines.batch_id` → `production_batches.dc_id` | Clumping rate by DC and month |
| P8 | 2 | **The failed lot.** Lot **L-ES-2608-0117**, Smoked Paprika (Sweet) from Spain, received at DC-NE Aug 24, 2026, passed receiving inspection, and was drawn first-in-first-out by every DC-NE batch that needed sweet smoked paprika from Sep 1 to Oct 9: the single-origin paprika (plain and organic) and 12 house blends, 26 batches in all, now on hold. A retest after complaints failed on Oct 12 (mold count and moisture above spec). **Measured:** the trace reaches **158 restaurants, 293 consumers, all 12 Northeast stores and 716 orders**; 109 reviews mention a musty smell | `supplier_quality_reports` facts (receiving pass, retest fail), `inbound_lots` (qc_status hold), `batch_inputs`, `production_batches` (qc_status hold), `order_lines.batch_id`, review and account-note facts | Recursive CTE or graph query from the lot code |
| P9 | 3 | **Ambiguous names.** Four cinnamons, chili powder vs. chile powder, three paprikas, Mediterranean vs. Mexican oregano, pink and Sichuan "pepper", real vs. "wasabi-style", and others (`ambiguity_group`) | `products`, `ingredients.aliases`, the Four Cinnamons Flight gift set, account notes ("use the Saigon, not the Ceylon") | Questions that need a clarifying turn |
| P10 | 3 | **Private contract prices; store and region ownership** | `contract_prices`, `restaurant_accounts.account_rep_id`, `staff`, `locations.region_id` | Secure views per rep, store manager and regional manager |
| P11 | 3 | **Look-alike accounts.** 20 business names shared by 2–3 accounts in different cities | `restaurant_accounts` | Agent must ask which one |
| P12 | 4 | **Seasonality to forecast.** Three fall-baking seasons and three holiday rushes; Lunar New Year and Diwali move; 2027 events are in `events.csv` | `orders`, `order_lines` | BigQuery forecast by product family; write back to `reorder_points` |
| P13 | 4 | **Viewed but can't ship.** From Monday Dec 7, 2026, DC-CE has no Saigon Cinnamon: 203 online and wholesale lines were substituted with Korintje and 214 backordered, and on Dec 31 it has zero on hand (1,996 units elsewhere) with PO-26-014618 in transit, due Jan 7, 2027. The 2026 Holiday Baking Collection sold out everywhere on Dec 14; GA4 records 3,272 views of it afterward | `inventory_positions`, `order_lines.line_status`, `purchase_orders`, GA4 `view_item` | Join views (BigQuery) to live inventory (AlloyDB) |
| P14 | 4 | **GA4 vs. orders gap.** About 8% of online orders have no `purchase` event | GA4, `orders` | Anti-join on transaction_id |
| P15 | 5 | **The holiday rush.** Cyber Monday Nov 30, 2026 is the busiest day of the three years: **1,626 online orders, about 3.9× a normal November 2026 day**, peaking 21:00 ET. Stores peak on Black Friday | `orders.ordered_at` | Orders per hour |
| P16 | 5 | **Index hot spots.** `indexes-baseline.sql` deliberately omits the indexes behind catalog queries Q07, Q11, Q13, Q18 and Q20 (section 10), so Query Insights and the Index Advisor have real targets | `indexes-baseline.sql` vs. `indexes-ops-answers.sql` | Index Advisor recommendations under load |
| P17 | 5 | **Columnar candidates.** Wide scans over `order_lines` and `orders` for the store and category dashboards (Q19, Q21) | — | Columnar on vs. off |
| F1 | callouts | **Fraud ring.** 14 guest accounts created Nov 16–25, 2026, sharing 2 ship-to addresses and 3 payment fingerprints, buying saffron tins, vanilla beans and the Saffron and Vanilla Reserve. 9 were flagged and cancelled; 5 were cleared by mistake | `customers`, `orders.ship_address_hash`, `payment_fingerprint`, `risk_*` | Shared-attribute graph or GROUP BY |

## 6 · Text corpus

Written in `pipeline/11-text-corpus.ipynb` (Colab Enterprise): `gemini-3.8-flash` from the `global` endpoint, Vertex AI batch prediction if it serves this model there, otherwise 01's throttled online pool. Embeddings use `gemini-embedding-001`, 3,072 dims, `RETRIEVAL_DOCUMENT`, regional endpoint.

| Type | Count | Length | Facts the generator supplies | Planted signal |
| :-- | --: | :-- | :-- | :-- |
| Product descriptions | ~835 | 90–150 words | Product, form, origin, heat, flavor tags, uses, blend recipe | Heat and smoke vocabulary for P5; ambiguity wording for P9 |
| Reviews | 70,000 | 20–120 words | Product, pack, rating, theme(s), reviewer segment, time since purchase, batch age | P6, P7, P8 (a few "musty smell" on failed-lot batches) |
| Recipes | 3,000 | 150–300 words | Cuisine, course, 2–5 Cymbal products with amounts, servings, time | Links products to dishes for semantic search |
| Supplier quality reports | 15,000 | 80–160 words | Lot, supplier, test values vs. spec, result, report type | P8's retest fail; conditional results with reasons |
| Account notes | 10,000 | 30–120 words | Account, rep, note type, the usual SKUs, a substitution or complaint, contract timing | P8 complaints; P9 preferences ("Saigon, not Ceylon"); private by design |
| **Total** | **~99,000** | | | |

**Rules:** the model writes only from the fact block. It never invents numbers, dates, brands, people or health benefits, and never uses an "it's not X, it's Y" construction. Reviews are written several to a request (8–10 fact blocks in, a JSON array out) to cut input cost. QA (01's pattern): hard failures are regenerated (missing or unparseable fields, length out of range, a number not in the facts, a real brand from a stoplist, health-claim words, plumbing words like "fact block"); soft flags are counted for a look.

**Process:** 25 documents per type, then stop for Patrick's review. The sample calibrates tokens per document, and the full run's cost estimate is recomputed from it before anything else is spent. Then the full run with checkpoints and resumable embedding shards (01's machinery).

**Cost (estimate, to be recalibrated from the sample).** About 11M visible output tokens, plus roughly as many reasoning tokens, and ~30M input tokens. At 00's batch prices ($0.375 / $1.875 per 1M in/out) that is **~$55 batched or ~$110 online**. Embeddings: ~20M tokens, about $3. Demo scale (500,000 documents): about 5×.

## 7 · Legacy SQL Server package (Stage 3)

Database **`CymbalFoodsOrders`**, compatibility level 120 (SQL Server 2014). It is built and restored with `pipeline/05`'s mechanics (Cloud SQL for SQL Server, the Python connector with `pytds`, `.bak` export, restore as Start Lab will, then CDC), and published to `…/dataset/v1/sqlserver/`.

**Tables** (the order system only, 2014-style): `Regions`, `Stores`, `Products`, `Skus`, `UnitConversions`, `PriceTiers`, `Customers` (consumers **and** wholesale accounts in one table with a `CustomerType` flag, as legacy systems do), `ContractPrices`, `Orders`, `OrderLines`, `OrderLineAudit`. Keys are `UNIQUEIDENTIFIER ROWGUIDCOL` with the dataset's UUIDs, so the migrated rows match the modern dataset. New rows from the procedures use `NEWID()`, which is the clustered-index fragmentation story behind random keys. Prices are `MONEY`, weights `FLOAT`, timestamps `DATETIME`, status codes `TINYINT` with a lookup table, text `NVARCHAR`. The legacy copy also adds dirt the modern tables don't have: trailing spaces in product names, `'N/A'` in place of NULL in a few columns, and two date formats in a free-text `Notes` column.

**Programmability:**

| Object | What it does | Legacy constructs |
| :-- | :-- | :-- |
| `fn_ConvertUom` | oz, ½ lb, lb, 5 lb, 25 lb conversions | FLOAT math, CASE |
| `fn_PricePerOz` | Price per ounce for a SKU and channel | **MONEY ÷ MONEY (P1)** |
| `usp_GetPrice` | Tiered price by channel, volume tier and contract | **MONEY × 400 (P1)**, OUTPUT parameters, ISNULL |
| `usp_QuoteBulkOrder` | Quote a restaurant's mixed-UoM order | Cursor, temp table, **ROUND on FLOAT (P3)** |
| `usp_SearchProducts` | Name search | **LIKE under CI collation (P2)**, TOP |
| `usp_PlaceOrder` | Insert an order and lines | BEGIN TRAN in TRY/CATCH, THROW, `@@ROWCOUNT`, OUTPUT INSERTED |
| `usp_ReorderUsuals` | Copy an account's last order at current contract prices | MERGE … OUTPUT `$action` |
| `usp_OrderHistory` | Paged history | `ROW_NUMBER()` paging CTE |
| `usp_DailyStoreSales` | Store sales summary | Cursor, `+` concatenation, FOR XML PATH |
| `vw_OrderSummary` | Order header with customer and store | ISNULL, CASE on status codes |
| `trg_OrderLines_Audit` | Writes before/after to `OrderLineAudit` | AFTER INSERT, UPDATE with `inserted`/`deleted` |

Whether DMS and Gemini actually trip on P1–P3 is unknown until Module 1's checks run. The smoke tests record SQL Server's answers either way (05's pattern), so the lab has a comparison whatever the conversion does. **Size:** full catalog and customers, plus orders for the year range Module 1's checks support. Default: 2026 only (~370,000 orders, ~1.1M lines).

## 8 · Generator and pipeline

```
datagen/
  DESIGN.md                this document
  content/                 hand-curated reference data (README.md explains every file)
  cfgen/                   the generator package (Stage 1)
    config.py              SEED, SCALE, calendar, every knob
    content.py             loads and validates content/; prints projected counts
    catalog.py             products, SKUs, prices, blend components
    places.py              locations, staff, addresses
    people.py              consumers, restaurant accounts, names
    demand.py              daily demand curves (seasonality × events × weekday × hour)
    orders.py              orders and lines, chunked by month
    supply.py              batches, lots, POs; FIFO allocation of batches to order lines
    inventory.py           Dec 31 positions and reorder points
    fraud.py               risk scores and the F1 ring
    clickstream.py         GA4 events for 2026
    plants.py              every planted moment, and plants.json
    facts.py               fact blocks for the text corpus
    schemas.py             single source: PostgreSQL DDL, BigQuery schemas, Spanner DDL, column descriptions
    slicer.py              cuts named slices from Parquet into staged CSVs and manifests
    sqlserver.py           renders the T-SQL package (Stage 3)
    validate.py            DuckDB checks: counts, referential integrity, every plant
  slices.yaml              the named slices (section 9)
  bigquery/                generated BigQuery schemas
  sql/                     generated schema.sql, constraints.sql, indexes-*.sql
  generate_dataset.py      CLI: generate → validate → cut slices
  Makefile                 make generate | validate | slices | stage
```

**Order of generation:** places and catalog → people → demand → orders (without batches) → supply plan from demand → FIFO allocation of batches to lines → inventory → fraud → plants → clickstream → fact blocks. Chunks are table × month, so Spanner scale runs in bounded memory.

**Notebooks** (students read these, so each section explains what it does as well as running it):

| Notebook | Runs where | Does |
| :-- | :-- | :-- |
| `pipeline/10-generate-dataset.ipynb` | Colab Enterprise (any runtime) | `REGENERATE` = True runs the generator and validation; `SLICES` = [...] cuts and stages slices; one handback block |
| `pipeline/11-text-corpus.ipynb` | Colab Enterprise | Fact blocks → Gemini (sample, then full) → QA → embeddings → corpus tables staged into the slices |
| `pipeline/12-sqlserver-package.ipynb` | Colab Enterprise in a lab project | 05 adapted to Cymbal Foods |
| `pipeline/13-load-test.ipynb` | Colab Enterprise in a lab project | 02 adapted: per slice, server-side import vs. parallel client COPY, index builds, against each module's budget |

I develop and test `cfgen/` in my own workspace (no cloud needed). Patrick's run of notebook 10 is what stages the data. The bucket guard from 01 is carried over: writes only under the workshop prefix, never under `alloydb-labs/cymbalgoal/`, no deletes.

## 9 · Staging, slices and the loader contract

### 9.1 Layout

```
gs://class-demo/alloydb-labs/cymbalfoods-alloydb-workshop/dataset/v1/
  parquet/<table>/part-NNN.parquet          the master copy
  sql/schema.sql  constraints.sql  indexes-baseline.sql  indexes-search.sql  indexes-ops-answers.sql
  slices/<slice>/manifest.json              one manifest per slice (the loader reads this)
  slices/<slice>/<table>.csv.gz             headerless, NULL = unquoted empty, vectors as pgvector literals
  ga4/events_YYYYMMDD.parquet (one per day → one BigQuery table per day)  bigquery/history/<table>/*.parquet  bigquery/schemas/*.json
  corpus/facts/  corpus/checkpoints/  corpus/results/  corpus/emb/
  sqlserver/CymbalFoodsOrders.bak  *.sql  smoke-sqlserver.json  manifest.json
  validation/plants.json  validation/report.json
  load-tests/
```

`v1` is pinned by the labs, so a later regeneration (`v2`) never changes a running lab. Demo and Spanner scale go to `dataset/v1-demo/` and `dataset/v1-spanner/`.

### 9.2 Groups

| Group | Tables | Rows (lab) | Needs |
| :-- | :-- | --: | :-- |
| `reference` | regions, locations, staff, categories, uom_conversions | ~400 | — |
| `catalog` | ingredients, suppliers, supplier_ingredients, products, blend_components, gift_set_items, skus, price_history | ~32,000 | reference |
| `consumers` | customers | 250,000 | reference |
| `wholesale` | restaurant_accounts, contract_prices | ~164,000 | reference, catalog |
| `orders` | orders, order_lines (one file per year) | 4.0M | consumers, wholesale, supply* |
| `supply` | purchase_orders, po_lines, inbound_lots, production_batches, batch_inputs | ~710,000 | catalog |
| `inventory` | inventory_positions, reorder_points | ~284,000 | catalog, supply* |
| `corpus_products` | product_descriptions | ~835 + vectors | catalog |
| `corpus_reviews` | reviews (one file per year) | 70,000 + vectors | orders |
| `corpus_recipes` | recipes, recipe_products | ~13,000 + 3,000 vectors | catalog |
| `corpus_quality` | supplier_quality_reports | 15,000 + vectors | supply |
| `corpus_accounts` | account_notes | 10,000 + vectors | wholesale |

\* `order_lines.batch_id` and `inventory_positions.batch_id` point into `supply`. A slice that leaves `supply` out still loads, because foreign keys live in `constraints.sql`. The manifest lists each constraint with the tables it needs, and the loader applies only those whose tables are present.

### 9.3 Named slices (`datagen/slices.yaml`)

Patrick's Oct 16 request: options in the notebook for loading sized segments. A slice is **groups × date window × optional cap**:

- **Window** (for example `2026-07-01..2026-12-31`) filters the dated tables: orders, lines, supply, reviews, quality reports and notes.
- **Closure:** rows a kept row points at are always kept (the batches and lots behind every kept order line, the customer behind every kept order), so a slice is referentially whole even with `supply` windowed.
- **Cap** (optional, for example `consumers: 100000`) samples consumers by a seeded hash and keeps all of their orders. Wholesale accounts are kept whole.

The slices as built (Stage 1, relational tables only), with measured rows. The load column is a local PostgreSQL 16 check (`tools/pg_load_check.py`); **Stage 4 measures AlloyDB.**

| Slice | For | Contents | Rows (measured) | CSV.gz | Local PG16 load (COPY + constraints + indexes) | Budget |
| :-- | :-- | :-- | --: | --: | :-- | :-- |
| `full` | demos, BigQuery, archive | everything relational (Stage 2 adds the corpus) | 5,399,734 | 252 MB | — | none |
| `aldb001` | Module 1 | nothing preloaded (DMS fills it) | 0 | — | — | small |
| `aldb002_search` | Module 2 notebook Task 1 | reference, catalog, consumers, wholesale, orders + supply Jul–Dec 2026 (Stage 2 adds products, reviews, recipes, quality reports) | 1,386,193 | 64 MB | not run | ~5 min |
| `aldb003_agents` | Module 3 | reference, catalog, consumers, wholesale, inventory, orders Oct–Dec 2026 (Stage 2 adds products and account notes) | 954,678 | 42 MB | 12 s | **~7 min** |
| `aldb004_datacloud` | Module 4 | reference, catalog, consumers, wholesale, inventory, orders 2025–2026 (BigQuery gets all three years plus GA4) | 3,412,037 | 161 MB | not run | ~9 min |
| `aldb005_ops` | Module 5 | reference, catalog, consumers, wholesale, inventory, supply, orders 2025–2026 | 3,926,264 | 183 MB | 35 s | ~8 min |

Locally, PostgreSQL 16 loads the heaviest setup-VM slice in about 35 seconds, so data volume is not the constraint the CymbalGoal numbers suggested. What Stage 4 must measure is AlloyDB through the setup VM, and in particular the per-file overhead of server-side import (a slice has 20–29 files). A slice is changed by editing `slices.yaml` and re-running notebook 10 with `REGENERATE = False`.

### 9.4 Manifest (per slice)

```json
{
  "dataset": "cymbalfoods", "version": "v1", "scale": 1, "seed": 20261016,
  "slice": "aldb003_agents",
  "spec": {"groups": ["reference", "catalog", "consumers", "wholesale", "inventory", "orders", "corpus_products", "corpus_accounts"],
           "window": ["2026-10-01", "2026-12-31"], "cap": null},
  "extensions": ["vector", "alloydb_scann", "google_ml_integration", "pg_trgm"],
  "schema_files": ["../../sql/schema.sql"],          // plus "../../sql/schema-corpus.sql" when the slice has corpus tables
  "load_order": ["regions", "locations", "..."],
  "tables": {
    "orders": {"file": "orders.csv.gz", "column_order": ["order_id", "..."], "rows": 112408, "bytes": 9812233, "sha256": "...", "group": "orders"}
  },
  "constraints": [{"name": "order_lines_batch_fk", "requires": ["order_lines", "production_batches"], "sql": "ALTER TABLE ..."}],
  "index_files": ["../../sql/indexes-baseline.sql", "../../sql/indexes-search.sql"],
  "estimates": {"copy_seconds": 160, "import_seconds": 420}
}
```

`schema.sql` holds `CREATE TABLE IF NOT EXISTS` with primary keys and NOT NULL only. Each index file uses `CREATE INDEX IF NOT EXISTS` throughout. Each ScaNN index sets `num_leaves` ≈ √rows of the table as loaded, and the slicer writes that number into the index file for the slice.

## 10 · Application query catalog

The queries the Cymbal Foods applications run. Module 5's holiday-rush load generator will be built from this list (not now). "Rush ×" is how much the query's rate rises on the peak day, and "Hot spot" means the baseline indexes deliberately leave it slow (P16).

| ID | Feature | Query | Tables | Rush × | Hot spot |
| :-- | :-- | :-- | :-- | --: | :-- |
| Q01 | Storefront | Keyword product search | products, product_descriptions | 6 | |
| Q02 | Storefront | Product page: product, its SKUs, price, rating summary | products, skus, reviews | 6 | |
| Q03 | Storefront | Semantic search ("smoky for brisket") | product_descriptions | 4 | |
| Q04 | Storefront | Availability of a SKU at the customer's serving DC | inventory_positions | 6 | |
| Q05 | Storefront | Reviews for a product, newest first, paged | reviews | 5 | |
| Q06 | Checkout | Place online order: insert order and lines, reserve inventory | orders, order_lines, inventory_positions | 6 | |
| Q07 | Checkout | Fraud check: recent orders sharing ship-to hash or payment fingerprint | orders | 6 | **yes** |
| Q08 | Account | Order history for a consumer, paged | orders | 4 | |
| Q09 | Account | Order status by order number | orders | 5 | |
| Q10 | Store POS | Ring up a sale (price lookup by UPC, insert order) | skus, orders, order_lines | 3 | |
| Q11 | Store ops | Stock of a SKU across all stores in a region | inventory_positions | 2 | **yes** |
| Q12 | Wholesale portal | Contract price list for an account | contract_prices, skus | 1.5 | |
| Q13 | Wholesale portal | "Reorder my usuals": an account's most frequent SKUs in the last 90 days | orders, order_lines | 1.5 | **yes** |
| Q14 | Wholesale portal | Place wholesale order at contract prices | orders, order_lines, contract_prices | 1.5 | |
| Q15 | DC ops | SKUs below reorder point at a DC | inventory_positions, reorder_points | 2 | |
| Q16 | DC ops | Receive a lot against a PO | inbound_lots, po_lines | 1 | |
| Q17 | Quality | Trace a lot to every order and customer | inbound_lots … customers | 1 | |
| Q18 | Merchandising | Who bought this SKU in the last 30 days | order_lines, orders | 1 | **yes** |
| Q19 | Store dashboard | Daily sales by store and category | orders, order_lines, skus, products | 2 | columnar |
| Q20 | Customer service | Orders for a customer email or phone | customers, orders | 3 | **yes** |
| Q21 | Merchandising | Weekly category sales by region, 52 weeks | order_lines, orders, products | 1 | columnar |
| Q22 | Merchandising | Top sellers by category, last 7 days | order_lines, orders, products | 2 | columnar |

The baseline index set covers primary keys, foreign-key columns on the small tables, `orders(order_number)`, and the contract-price PK. `indexes-ops-answers.sql` holds the indexes Module 5 students should arrive at, and is never loaded at Start Lab.

## 11 · Spanner shape

| PostgreSQL | Spanner (GoogleSQL) | Notes |
| :-- | :-- | :-- |
| `uuid` | `STRING(36)` (or `UUID` where available) | Random v4. Fine for Spanner; for AlloyDB B-tree locality this is the classic random-key trade-off, a Module 5 talking point (PostgreSQL 18's `uuidv7()` is the contrast) |
| `text` codes as PKs (`sku_id`, `product_id`, `location_id`) | `STRING` | Small reference tables, not insert hot spots |
| `numeric(p,s)` | `NUMERIC` | |
| `timestamptz` / `date` | `TIMESTAMP` / `DATE` | |
| `text[]` | `ARRAY<STRING>` | |
| `vector(3072)` | `ARRAY<FLOAT32>(vector_length=>3072)` | |

Interleaving: `order_lines` IN PARENT `orders`; `po_lines` IN PARENT `purchase_orders`; `batch_inputs` IN PARENT `production_batches`; `contract_prices` IN PARENT `restaurant_accounts`. No enums, sequences, serials or table inheritance. `schemas.py` emits the Spanner DDL from the same column definitions.

## 12 · Size, time and cost

| | Lab | Demo | Spanner |
| :-- | --: | --: | --: |
| AlloyDB rows (all groups) | ~5.4M | ~54M | n/a |
| Relational CSV.gz | ~170 MB | ~1.7 GB | ~17 GB |
| Vectors CSV.gz (12.7 KB/row measured on CymbalGoal) | ~1.3 GB | ~6.4 GB | n/a |
| Parquet master (relational) | ~350 MB | ~3.5 GB | ~35 GB |
| GA4 Parquet | ~1.2 GB | ~12 GB | ~120 GB |
| In-database size, all groups with indexes | ~3 GB | ~20 GB | n/a |
| Generation time | ~10 min on 8 vCPU | ~1.5 h | ~12–15 h on 32 vCPU, or a batch job later |
| Text corpus | ~$55–110 + ~$3 embeddings; 3–6 h | ~5× | reuses demo corpus |

These are estimates. Stage 1 replaces the generation and size rows with measured numbers, Stage 2 the corpus cost, and Stage 4 the load times.

## 13 · Stages from here

| Stage | Output | Patrick runs |
| :-- | :-- | :-- |
| 1 | `cfgen/`, `slices.yaml`, notebook 10, validation report, staged slices without corpus | Notebook 10 in a temp lab project |
| 2 | Notebook 11: 25 per type → review → full run → embeddings → corpus tables staged into slices | Notebook 11 twice (sample, full) |
| 3 | `cfgen/sqlserver.py`, notebook 12, the `.bak` | Module 1 checks first, then notebook 12 |
| 4 | Notebook 13: per-slice load times both ways; slices trimmed if needed | Notebook 13 on the heaviest module's slice |

Each stage updates `state/dataset-handback.md`.
