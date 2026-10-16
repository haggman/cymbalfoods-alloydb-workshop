# Cymbal Foods reference data (`datagen/content/`)

Hand-curated inputs to the generator. Everything a person would want to read, argue with, or edit lives here; everything statistical lives in `cfgen/config.py`. Edit a file, rerun the generator, and every table that depends on it regenerates consistently.

**Status:** approved Oct 16, 2026 (Stage 0) and used for the Stage 1 build.

## Files

| File | Rows | What it is | How the generator uses it |
| :-- | --: | :-- | :-- |
| `spices.csv` | 163 | Single-origin spices, herbs, chiles, peppercorns, seeds, salts and aromatics | One row per ingredient. Products = each `forms` entry (× each origin when `origin_variants` = Y), plus organic versions of the first two forms when `organic` = Y. Inbound lots are bought at this grain |
| `blends.csv` | 143 | House blends with recipes (`slug:percent` by weight, summing to 100) | One product per blend plus one per entry in `variants` (salt_free, hot, mild, organic). Recipes become `blend_components`; blend batches consume lots in these proportions |
| `pantry.csv` | 56 | Base items for oils, vinegars, sauces and condiments, tea, coffee and baking | One product per `variants` entry (or one if empty). `(Country)` in a variant overrides the origin. `sourcing` = `co_packed` means the item arrives finished from a co-packer |
| `gift_sets.csv` | 24 | Gift sets and their contents | Evergreen sets are one product; annual sets are one product per edition, discontinued after their season |
| `packaging.csv` | 61 | Pack formats (jar, ½ lb, 1 lb, 5 lb, 25 lb, bottles, tins…) | SKUs = product × every pack in its family and density whose `min_popularity` the product meets. `net_oz` drives the unit-of-measure conversions |
| `locations.csv` | 44 | 4 distribution centers and 40 stores | Real cities, fictional store names; street addresses are generated. Coordinates are city-center approximations |
| `supplier_countries.csv` | 25 | Where the ~150 suppliers are based, with lead times | Supplier counts per country; a supplier's country can differ from a lot's origin (trading houses) |
| `seasonality.csv` | 13 | Monthly demand profiles | Every product carries one profile |
| `events.csv` | 53 | Dated events 2024–2027 (holidays, moving festivals, the rush) | Spikes layered on the monthly profiles; 2027 rows are the forecast horizon for Module 4 |
| `cuisines.csv` | 16 | Restaurant account cuisines | Share of accounts, what each buys (tag affinity), reorder cadence and basket size |
| `consumer_segments.csv` | 7 | Consumer behavior segments | Category affinity, channel mix, frequency, gift propensity |
| `restaurant_names.csv` | — | Word lists and patterns for fictional business names | Combined by the seeded generator |
| `us_cities.csv` | 82 | Cities consumers and accounts live in: the 37 store cities plus 45 online-only cities, with ZIP prefixes and area codes | Weights where people live; online-only customers have no home store |
| `names/*.csv` | 7,500 | 5,000 surnames, 1,500 female and 1,000 male first names with frequencies | Weighted (and flattened) draws for people's names |

Vocabularies used across files:

- **heat**: 0 (none) to 5 (extreme). Chiles carry an approximate Scoville range.
- **price_band**: 1 commodity (about $8–14 per lb retail), 2 ($14–24), 3 ($24–40), 4 ($40–90), 5 premium (saffron, vanilla, real wasabi).
- **density**: light (leafy herbs, whole chiles, flakes), medium (most ground spices), dense (seeds, whole spices, salts). It sets jar fill weights and which bulk sizes exist.
- **popularity**: 1 (niche) to 5 (staple). It sets the item's popularity rank and which pack sizes it gets.
- **ambiguity_group**: items people confuse. Module 3's disambiguation moments come from these groups (cinnamon, chile_powder, paprika, oregano, pepper, cardamom, cumin, saffron, vanilla, curry, bay, basil, mint, mustard, salt, garlic, wasabi, sesame, dill, fenugreek, ginger, allspice).

## Where the content came from

| Content | Source | Verification status |
| :-- | :-- | :-- |
| Ingredient names, botanical names, forms, flavor notes | Compiled by Claude from general culinary knowledge | Patrick review |
| Producing countries | Widely documented producing regions for each spice | Cross-checked Oct 16, 2026 against 2023 exporter rankings by HS heading (trendeconomy.com, from UN Comtrade): cinnamon (Sri Lanka, China, Indonesia), cardamom/nutmeg (Guatemala, Indonesia, India), vanilla (Madagascar), pepper and dried capsicum (India, China, Spain, Brazil, Peru, Vietnam), seeds (India, China, Egypt, Turkey); CBI for oregano (Turkey first, then Greece, Albania, Egypt). Origins consistent |
| Scoville ranges | Commonly published approximate ranges | Approximate by design; the data uses heat bands, not exact values |
| Blend recipes | Written by Claude for this dataset; traditional blends follow their usual components | Patrick review. Names are generic styles, never a real company's product names |
| Pantry items | Compiled by Claude | Patrick review |
| Store and DC cities | Chosen with Patrick (Oct 16): US only, four regions | Coordinates are approximate city centers |
| ZIP prefixes and area codes (`us_cities.csv`) | General knowledge of US numbering | Street addresses are fictional; phone numbers are all in the 555-01XX fiction range |
| Seasonality profiles | Hand-authored with a written rationale per profile | Shapes, not measurements |
| Event dates | Published calendars (US holidays, Lunar New Year, Diwali, Easter, Ramadan, championship game) | Islamic dates can shift a day with moon sighting |
| People's names | US Census 1990 name-frequency files (public domain), as packaged in the `names` 0.3.0 PyPI distribution. The Census 2010 and SSA sites were not reachable from the build environment | Fetched Oct 16, 2026 |
| Restaurant names | `restaurant_names.csv` word lists | Combinatorial names can coincide with a real small business somewhere; patterns are chosen to make that unlikely, and Stage 1 rejects a short list of known chains |

## Rules the content follows

- **No real brands, companies, people or restaurants.** Blend names are generic styles ("Chesapeake-Style Seafood Seasoning", "Montreal-Style Steak Spice"). Protected place names appear only as an origin or style (Pimentón de la Vera, Espelette, Modena).
- **No sanctioned origins.** Nothing is sourced from Iran, Syria, Russia, Cuba or North Korea. Aleppo pepper is "Aleppo-style" from Turkey, and saffron comes from Spain, India (Kashmir) and Greece.
- **Shelf-stable only.** There is no dairy, so no buttermilk ranch or cheese seasonings. There are no fresh nuts, which go rancid. Tonka bean is excluded (not permitted in US food).
- **Older terms.** "Makrut lime" is used rather than the older name for the same leaf.
- **No health claims.** Flavor notes describe taste and use, never benefits.

## Editing

- Keep `slug` values stable. Blends, gift sets and the planted moments refer to them.
- Lists inside a field use `;`. No commas inside fields.
- `python3 -m cfgen.content --check` (Stage 1) validates every file: slugs resolve, recipes sum to 100, packs exist for every family, and the projected product and SKU counts are printed.
