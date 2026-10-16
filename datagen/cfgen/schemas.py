"""The single source of truth for every table: columns, types, descriptions, keys, groups and Spanner shape.

From these definitions the generator writes schema.sql (with COMMENT ON for agents and NL2SQL), constraints,
the per-module index files, BigQuery schemas and Spanner DDL. Descriptions are written for an analyst or an
agent reading the catalog: they name units and list the values a column can hold.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

CH = "store | online | wholesale"


@dataclass
class Table:
    name: str
    group: str
    description: str
    columns: list[tuple[str, str, bool, str]]          # name, PostgreSQL type, nullable, description
    pk: list[str]
    fks: list[tuple[str, str, str]] = field(default_factory=list)   # column(s), parent table, parent column(s)
    parent: str | None = None                          # Spanner INTERLEAVE IN PARENT
    window: str | None = None                          # column a slice's date window applies to
    year_split: bool = False                           # staged as one file per year
    stage: int = 1                                     # 1 = relational (Stage 1), 2 = text corpus (Stage 2)


T = [
    Table("regions", "reference", "The four Cymbal Foods regions, each served by one distribution center.", [
        ("region_id", "text", False, "Region code: NE (Northeast), SE (Southeast), CE (Central), WE (West)."),
        ("name", "text", False, "Region name."),
        ("dc_location_id", "text", False, "The distribution center that serves the region; joins to locations.location_id."),
    ], ["region_id"]),
    Table("locations", "reference", "The 4 distribution centers (DC-…) and 40 retail stores (ST-…). Street addresses are fictional; cities are real.", [
        ("location_id", "text", False, "Location code, e.g. ST-014 for a store or DC-CE for a distribution center."),
        ("location_type", "text", False, "dc | store."),
        ("name", "text", False, "Location name, e.g. Cymbal Foods Decatur."),
        ("region_id", "text", False, "Region; joins to regions.region_id. Regional managers see their region's stores."),
        ("street_address", "text", False, "Street address (fictional)."),
        ("city", "text", False, "City."),
        ("state", "text", False, "Two-letter US state code."),
        ("postal_code", "text", False, "ZIP code."),
        ("latitude", "numeric(8,5)", False, "Approximate latitude (city center)."),
        ("longitude", "numeric(8,5)", False, "Approximate longitude (city center)."),
        ("time_zone", "text", False, "IANA time zone; store hours and order times are local to it."),
        ("opened_on", "date", False, "Date the location opened. Two stores opened inside the data window (ST-027 in Mar 2025, ST-040 in Aug 2026)."),
        ("sq_ft", "integer", False, "Selling floor (stores) or warehouse (DCs) area in square feet."),
    ], ["location_id"], [("region_id", "regions", "region_id")]),
    Table("staff", "reference", "Cymbal Foods employees who own data: store and regional managers, account reps, DC staff.", [
        ("staff_id", "text", False, "Employee id, e.g. E10042."),
        ("full_name", "text", False, "Employee name (fictional)."),
        ("email", "text", False, "Work email (@cymbalfoods.example)."),
        ("role", "text", False, "store_manager | assistant_manager | regional_manager | account_rep | dc_manager | buyer | quality_analyst | merchandiser."),
        ("location_id", "text", False, "Home location; joins to locations.location_id."),
        ("region_id", "text", True, "Region the employee covers; NULL for head-office merchandisers."),
        ("hired_on", "date", False, "Hire date."),
    ], ["staff_id"], [("location_id", "locations", "location_id")]),
    Table("categories", "reference", "Product categories.", [
        ("category_id", "text", False, "Category code: SPICE, HERB, CHILE, PEPPER, SALT, AROMATIC, BLEND, OIL, VINEGAR, SAUCE, CONDIMENT, TEA, COFFEE, BAKING, GIFT."),
        ("name", "text", False, "Category name."),
        ("department", "text", False, "Department: Spices & Blends, Pantry, Tea & Coffee, Baking, Gifts."),
    ], ["category_id"]),
    Table("uom_conversions", "reference", "Unit-of-measure conversion factors (oz, half_lb, lb, 5lb, 25lb, g, kg, fl_oz, ml, gal, ct).", [
        ("from_uom", "text", False, "Unit converted from."),
        ("to_uom", "text", False, "Unit converted to."),
        ("factor", "numeric(12,6)", False, "Multiply a quantity in from_uom by this to get to_uom."),
    ], ["from_uom", "to_uom"]),
    Table("ingredients", "catalog", "What Cymbal Foods buys as inbound lots: single-origin spices, herbs, chiles and salts, plus one supply item per pantry product.", [
        ("ingredient_id", "text", False, "Ingredient code, e.g. cinnamon_saigon or evoo__arbequina."),
        ("name", "text", False, "Ingredient name."),
        ("ingredient_type", "text", False, "chile | peppercorn | spice | seed | herb | aromatic | salt | flower | citrus | specialty, or a pantry category (oil, vinegar, sauce, condiment, tea, coffee, baking)."),
        ("botanical_name", "text", True, "Latin name of the plant, when there is one."),
        ("ambiguity_group", "text", True, "Items customers confuse, e.g. cinnamon (Ceylon, cassia, Saigon, Korintje), paprika, oregano, chile_powder. Ask which one is meant."),
        ("aliases", "text[]", False, "Other names customers use, e.g. 'Vietnamese cinnamon' for Saigon cinnamon."),
        ("heat_level", "smallint", False, "Heat from 0 (none) to 5 (extreme)."),
        ("scoville_min", "integer", True, "Approximate low end of the Scoville range (chiles only)."),
        ("scoville_max", "integer", True, "Approximate high end of the Scoville range (chiles only)."),
        ("flavor_notes", "text[]", False, "Short flavor descriptors."),
        ("primary_origins", "text[]", False, "Countries the ingredient is sourced from, main origin first."),
    ], ["ingredient_id"]),
    Table("suppliers", "catalog", "The ~150 suppliers in 25 countries that ship lots to the DCs. Names are fictional.", [
        ("supplier_id", "uuid", False, "Supplier key."),
        ("supplier_code", "text", False, "Readable supplier code, e.g. SUP-0042."),
        ("name", "text", False, "Supplier name (fictional)."),
        ("country_code", "text", False, "ISO country code where the supplier is based (may differ from a lot's origin for trading houses)."),
        ("supplier_kind", "text", False, "grower | grower_exporter | processor | trading_house | co_packer | importer."),
        ("quality_tier", "text", False, "A | B | C, from supplier audits."),
        ("onboarded_on", "date", False, "First approved."),
        ("is_active", "boolean", False, "FALSE when the supplier is no longer used."),
    ], ["supplier_id"]),
    Table("supplier_ingredients", "catalog", "Which supplier sells which ingredient from which origin.", [
        ("supplier_id", "uuid", False, "Supplier; joins to suppliers."),
        ("ingredient_id", "text", False, "Ingredient; joins to ingredients."),
        ("origin_country", "text", False, "Country the ingredient is grown or made in."),
        ("is_primary", "boolean", False, "TRUE for the preferred supplier of the ingredient's main origin."),
        ("lead_time_days", "smallint", False, "Typical days from purchase order to receipt."),
    ], ["supplier_id", "ingredient_id", "origin_country"], [("supplier_id", "suppliers", "supplier_id"), ("ingredient_id", "ingredients", "ingredient_id")]),
    Table("products", "catalog", "Everything Cymbal Foods sells (~835 products). A product comes in one or more SKUs (pack sizes).", [
        ("product_id", "text", False, "Product code, e.g. P10124."),
        ("name", "text", False, "Customer-facing name, e.g. 'Saigon Cinnamon, Ground'."),
        ("category_id", "text", False, "Category; joins to categories."),
        ("product_type", "text", False, "single_origin | blend | pantry | gift_set."),
        ("ingredient_id", "text", True, "For single-origin and pantry products, the ingredient it is packed from; NULL for blends and gift sets."),
        ("form", "text", False, "ground, whole, crushed, flakes, threads, beans, … or blend, gift set, or a pantry subtype."),
        ("origin_country", "text", True, "Origin named on the label, when there is one."),
        ("is_organic", "boolean", False, "Certified organic."),
        ("is_salt_free", "boolean", False, "Contains no added salt."),
        ("heat_level", "smallint", False, "Heat from 0 (none) to 5 (extreme)."),
        ("flavor_tags", "text[]", False, "Flavor descriptors, e.g. {smoky,sweet}."),
        ("cuisine_tags", "text[]", False, "Cuisines the product suits, e.g. {bbq,tex_mex}."),
        ("allergens", "text[]", False, "Major allergens the product contains: sesame, mustard, soy, wheat, fish. Empty when none."),
        ("seasonality_profile", "text", False, "Demand profile: base, baking, pumpkin, grilling, chili_gameday, soup_stew, holiday_gift, lunar_new_year, diwali, pickling, thanksgiving, summer_iced, hot_tea."),
        ("ambiguity_group", "text", True, "Group of easily confused items (cinnamon, paprika, chile_powder, oregano, pepper, …)."),
        ("launched_on", "date", False, "Date first sold."),
        ("discontinued_on", "date", True, "Date retired (annual gift editions); NULL while on sale."),
    ], ["product_id"], [("category_id", "categories", "category_id"), ("ingredient_id", "ingredients", "ingredient_id")]),
    Table("blend_components", "catalog", "House blend recipes: each blend's ingredients by percent of weight.", [
        ("product_id", "text", False, "The blend; joins to products."),
        ("ingredient_id", "text", False, "A component; joins to ingredients."),
        ("pct", "numeric(5,2)", False, "Percent of the blend by weight; a blend's components sum to 100."),
    ], ["product_id", "ingredient_id"], [("product_id", "products", "product_id"), ("ingredient_id", "ingredients", "ingredient_id")]),
    Table("gift_set_items", "catalog", "What is in each gift set.", [
        ("gift_product_id", "text", False, "The gift set; joins to products."),
        ("product_id", "text", False, "A product inside it; joins to products."),
        ("quantity", "smallint", False, "How many of that product the set holds."),
    ], ["gift_product_id", "product_id"], [("gift_product_id", "products", "product_id"), ("product_id", "products", "product_id")]),
    Table("skus", "catalog", "Sellable pack sizes (~3,400 SKUs). The SKU code is the product number plus a pack code.", [
        ("sku_id", "text", False, "SKU code, e.g. CF-10124-B08 (product 10124 in a ½ lb bag)."),
        ("product_id", "text", False, "Product; joins to products."),
        ("pack_code", "text", False, "Pack: JAR, B02/B04 (refill bags), B08 (½ lb), B16 (1 lb), B80 (5 lb), P400 (25 lb), bottles, tins, GIFT, …"),
        ("pack_description", "text", False, "Pack as printed, e.g. '1/2 lb bag'."),
        ("container", "text", False, "jar | bag | box | tin | vial | bottle | jug | tube | pack | sack | tub | case."),
        ("net_qty", "numeric(8,2)", False, "Net quantity in net_uom."),
        ("net_uom", "text", False, "Unit of net_qty: oz, lb, g, fl_oz, ct."),
        ("net_oz", "numeric(9,3)", False, "Net weight in ounces (fluid ounces for liquids); used for unit-of-measure conversions."),
        ("upc", "text", False, "12-digit UPC in the in-store '2' range."),
        ("sold_in_stores", "boolean", False, "Stocked in retail stores."),
        ("sold_online", "boolean", False, "Sold in the online store."),
        ("sold_wholesale", "boolean", False, "Sold to restaurant accounts."),
        ("list_price", "numeric(10,2)", False, "Current retail price in USD (stores and online)."),
        ("wholesale_price", "numeric(10,2)", False, "Current wholesale list price in USD, before tier and contract discounts."),
        ("unit_cost", "numeric(10,4)", False, "Current unit cost in USD."),
        ("status", "text", False, "active | discontinued."),
        ("introduced_on", "date", False, "Date first sold."),
    ], ["sku_id"], [("product_id", "products", "product_id")]),
    Table("price_history", "catalog", "Every list price change by SKU and channel since Jan 1, 2024.", [
        ("sku_id", "text", False, "SKU; joins to skus."),
        ("channel", "text", False, "retail (stores and online) | wholesale (list, before tier and contract discounts)."),
        ("effective_from", "date", False, "First day the price applied."),
        ("price", "numeric(10,2)", False, "Price in USD."),
        ("change_reason", "text", False, "list price | annual increase | supply: … (vanilla Apr 2025, saffron Sep 2025)."),
    ], ["sku_id", "channel", "effective_from"], [("sku_id", "skus", "sku_id")]),
    Table("customers", "consumers", "Consumers who buy in stores (loyalty scan) or online. Names, emails and phones are fictional (555-01XX numbers, example.com addresses).", [
        ("customer_id", "uuid", False, "Customer key."),
        ("customer_number", "text", False, "Readable customer number, e.g. C10482211; GA4's user_id for signed-in visitors."),
        ("first_name", "text", False, "First name (fictional)."),
        ("last_name", "text", False, "Last name (fictional)."),
        ("email", "text", False, "Email (example.com/.net/.org). A few are stored in mixed case."),
        ("phone", "text", False, "Phone, mostly NXX-555-01XX; a few in older formats."),
        ("street_address", "text", False, "Street address (fictional)."),
        ("city", "text", False, "City."),
        ("state", "text", False, "Two-letter state code."),
        ("postal_code", "text", False, "ZIP code."),
        ("home_store_id", "text", True, "Nearest store; NULL for online-only customers outside store metros."),
        ("serving_dc_id", "text", False, "DC that ships this customer's online orders."),
        ("created_at", "timestamptz", False, "When the customer record was created."),
        ("account_type", "text", False, "registered | guest (guest checkout)."),
        ("loyalty_tier", "text", False, "none | member | plus."),
        ("marketing_segment", "text", False, "everyday_cook | home_baker | grill_master | global_explorer | gift_giver | tea_and_coffee | salt_free_shopper."),
        ("email_opt_in", "boolean", False, "Accepts marketing email."),
    ], ["customer_id"], [("home_store_id", "locations", "location_id"), ("serving_dc_id", "locations", "location_id")]),
    Table("restaurant_accounts", "wholesale", "The ~4,000 restaurants, caterers and bakeries supplied on contract pricing. Business names are fictional; some names repeat in different cities.", [
        ("account_id", "uuid", False, "Account key."),
        ("account_number", "text", False, "Readable account number, e.g. W100604."),
        ("business_name", "text", False, "Business name (fictional). Not unique: a few names belong to different businesses in different cities."),
        ("business_type", "text", False, "restaurant | caterer | bakery | cafe | food_truck | hotel_kitchen | commissary."),
        ("cuisine", "text", False, "Cuisine, e.g. bbq_smokehouse, mexican, indian, bakery_cafe."),
        ("contact_name", "text", False, "Buyer's name (fictional)."),
        ("contact_email", "text", False, "Buyer's email (fictional)."),
        ("phone", "text", False, "Phone (555-01XX)."),
        ("street_address", "text", False, "Delivery address (fictional)."),
        ("city", "text", False, "City."),
        ("state", "text", False, "Two-letter state code."),
        ("postal_code", "text", False, "ZIP code."),
        ("region_id", "text", False, "Region; joins to regions."),
        ("serving_dc_id", "text", False, "DC that ships the account's orders."),
        ("account_rep_id", "text", False, "The account rep who owns the relationship; joins to staff. Only this rep may see the account's contract prices."),
        ("price_tier", "text", False, "Volume tier: T1 (10% off wholesale list), T2 (15%), T3 (20%)."),
        ("credit_terms", "text", False, "Invoice terms, e.g. NET30."),
        ("contract_start", "date", False, "Start of the first pricing contract; contracts renew on this anniversary."),
        ("contract_end", "date", False, "End of the current contract term."),
        ("status", "text", False, "active | closed."),
        ("created_at", "timestamptz", False, "Account opened."),
    ], ["account_id"], [("region_id", "regions", "region_id"), ("account_rep_id", "staff", "staff_id")]),
    Table("contract_prices", "wholesale", "Private, per-account negotiated prices on each account's usual SKUs, one row per contract year.", [
        ("account_id", "uuid", False, "Account; joins to restaurant_accounts. Confidential to the account and its rep."),
        ("sku_id", "text", False, "SKU; joins to skus."),
        ("effective_from", "date", False, "First day of the contract year."),
        ("effective_to", "date", False, "Last day of the contract year."),
        ("contract_price", "numeric(10,2)", False, "Negotiated unit price in USD, 3–12% below the account's tier price."),
    ], ["account_id", "sku_id", "effective_from"], [("account_id", "restaurant_accounts", "account_id"), ("sku_id", "skus", "sku_id")],
        parent="restaurant_accounts"),
    Table("orders", "orders", "Every order in all three channels, Jan 2024 – Dec 2026: store sales, online orders and wholesale orders.", [
        ("order_id", "uuid", False, "Order key."),
        ("order_number", "text", False, "Readable order number: S- (store), O- (online), W- (wholesale), two-digit year, sequence. GA4 purchase events carry it as transaction_id."),
        ("channel", "text", False, CH + "."),
        ("customer_id", "uuid", True, "Consumer; NULL for anonymous store sales and for wholesale."),
        ("account_id", "uuid", True, "Restaurant account (wholesale only)."),
        ("location_id", "text", False, "The store for store sales, otherwise the DC that fulfilled the order."),
        ("ordered_at", "timestamptz", False, "When the order was placed (UTC)."),
        ("status", "text", False, "completed (store) | shipped | delivered | cancelled | returned."),
        ("ship_city", "text", True, "Ship-to city (online and wholesale)."),
        ("ship_state", "text", True, "Ship-to state."),
        ("ship_postal_code", "text", True, "Ship-to ZIP."),
        ("ship_address_hash", "text", True, "Hash of the ship-to street and ZIP, for matching orders that ship to the same place."),
        ("subtotal", "numeric(12,2)", False, "Sum of line extended prices, USD."),
        ("discount_total", "numeric(12,2)", False, "Promo discount, USD."),
        ("shipping_total", "numeric(12,2)", False, "Shipping charged, USD (online orders under $49)."),
        ("tax_total", "numeric(12,2)", False, "Sales tax, USD (wholesale is tax-exempt resale)."),
        ("order_total", "numeric(12,2)", False, "subtotal − discount + shipping + tax, USD."),
        ("promo_code", "text", True, "Promo code used, if any."),
        ("payment_method", "text", False, "card | cash | gift_card | invoice."),
        ("payment_fingerprint", "text", True, "Hash identifying the card used (never a card number); NULL for cash and invoice."),
        ("device", "text", True, "web | app (online orders)."),
        ("risk_score", "numeric(4,3)", True, "Fraud risk 0–1 (online orders); 0.70 and above goes to review."),
        ("risk_flags", "text[]", True, "Reasons for the score, e.g. {new_account,high_value,velocity}."),
        ("risk_review", "text", True, "none | cleared | cancelled_fraud (online orders)."),
    ], ["order_id"], [("customer_id", "customers", "customer_id"), ("account_id", "restaurant_accounts", "account_id"),
                      ("location_id", "locations", "location_id")], window="ordered_at", year_split=True),
    Table("order_lines", "orders", "Order lines. Each line records the production batch it shipped from, so a lot can be traced to customers.", [
        ("order_id", "uuid", False, "Order; joins to orders."),
        ("line_no", "smallint", False, "Line number within the order."),
        ("sku_id", "text", False, "SKU; joins to skus."),
        ("quantity", "integer", False, "Units."),
        ("unit_price", "numeric(10,2)", False, "Price per unit charged, USD."),
        ("price_source", "text", False, "list (retail) | tier (wholesale tier price) | contract (account's negotiated price)."),
        ("extended_price", "numeric(12,2)", False, "quantity × unit_price, USD."),
        ("batch_id", "uuid", True, "Production batch shipped; joins to production_batches. NULL for cancelled and backordered lines."),
        ("line_status", "text", False, "fulfilled | substituted | backordered | cancelled | returned."),
        ("substituted_from_sku", "text", True, "The SKU the customer ordered when a substitute was shipped."),
        ("return_reason", "text", True, "Why the line was returned."),
    ], ["order_id", "line_no"], [("order_id", "orders", "order_id"), ("sku_id", "skus", "sku_id"),
                                 ("batch_id", "production_batches", "batch_id")], parent="orders", year_split=True),
    Table("purchase_orders", "supply", "Purchase orders to suppliers, one per supplier, DC and ordering week.", [
        ("po_id", "uuid", False, "Purchase order key."),
        ("po_number", "text", False, "Readable PO number, e.g. PO-26-012345."),
        ("supplier_id", "uuid", False, "Supplier; joins to suppliers."),
        ("destination_dc_id", "text", False, "Receiving DC."),
        ("ordered_on", "date", False, "Date ordered."),
        ("expected_on", "date", False, "Expected receipt date."),
        ("received_on", "date", True, "Date received; NULL while in transit."),
        ("status", "text", False, "received | in_transit."),
        ("freight", "text", False, "sea | truck."),
        ("total_cost", "numeric(14,2)", False, "Sum of line costs, USD."),
    ], ["po_id"], [("supplier_id", "suppliers", "supplier_id"), ("destination_dc_id", "locations", "location_id")], window="ordered_on"),
    Table("po_lines", "supply", "Purchase order lines: an ingredient, an origin and a quantity.", [
        ("po_id", "uuid", False, "Purchase order; joins to purchase_orders."),
        ("line_no", "smallint", False, "Line number."),
        ("ingredient_id", "text", False, "Ingredient; joins to ingredients."),
        ("origin_country", "text", False, "Origin ordered."),
        ("quantity_kg", "numeric(10,2)", False, "Quantity ordered, kg."),
        ("unit_cost_per_kg", "numeric(10,4)", False, "Cost per kg, USD."),
    ], ["po_id", "line_no"], [("po_id", "purchase_orders", "po_id"), ("ingredient_id", "ingredients", "ingredient_id")], parent="purchase_orders"),
    Table("inbound_lots", "supply", "Supplier lots received at a DC. A PO line can arrive as several lots. The lot code is printed on the bag.", [
        ("lot_id", "uuid", False, "Lot key."),
        ("lot_code", "text", False, "Lot code L-<origin ISO>-<yymm received>-<seq>, e.g. L-ES-2608-0117."),
        ("po_id", "uuid", False, "Purchase order; joins to purchase_orders."),
        ("po_line_no", "smallint", False, "PO line; with po_id joins to po_lines."),
        ("supplier_id", "uuid", False, "Supplier; joins to suppliers."),
        ("ingredient_id", "text", False, "Ingredient; joins to ingredients."),
        ("origin_country", "text", False, "Country the lot was grown or made in."),
        ("harvest_year", "smallint", False, "Crop year."),
        ("dc_id", "text", False, "Receiving DC."),
        ("received_at", "timestamptz", False, "Received at the dock."),
        ("quantity_kg", "numeric(10,2)", False, "Net kg received."),
        ("moisture_pct", "numeric(5,2)", True, "Moisture at receipt, % (spices, herbs and salts)."),
        ("volatile_oil_pct", "numeric(5,2)", True, "Volatile oil, % (spices and herbs)."),
        ("qc_status", "text", False, "released | conditional (released with a note) | rejected | hold (quality hold after release)."),
        ("qc_released_at", "timestamptz", True, "When quality released the lot; NULL if rejected."),
        ("best_by", "date", False, "Best-by date."),
    ], ["lot_id"], [("supplier_id", "suppliers", "supplier_id"), ("ingredient_id", "ingredients", "ingredient_id"),
                    ("po_id,po_line_no", "po_lines", "po_id,line_no")], window="received_at"),
    Table("production_batches", "supply", "Pack, blend, receive and kit batches made at a DC. Order lines point at the batch they shipped from.", [
        ("batch_id", "uuid", False, "Batch key."),
        ("batch_code", "text", False, "Batch code B-<DC>-<yymmdd>-<seq>."),
        ("batch_type", "text", False, "pack (single ingredient into bags and jars) | blend (house blend) | receive_finished (co-packed goods) | kit (gift sets)."),
        ("product_id", "text", False, "Product made; joins to products."),
        ("dc_id", "text", False, "DC where it was made."),
        ("produced_at", "timestamptz", False, "When the batch was made."),
        ("quantity_kg", "numeric(12,3)", False, "Batch size in kg (units for gift kits)."),
        ("best_by", "date", False, "Best-by date."),
        ("qc_status", "text", False, "released | hold (made from a lot that later failed a retest)."),
    ], ["batch_id"], [("product_id", "products", "product_id")], window="produced_at"),
    Table("batch_inputs", "supply", "Which lots went into each batch: the edge from inbound lot to batch in the trace graph.", [
        ("batch_id", "uuid", False, "Batch; joins to production_batches."),
        ("input_no", "smallint", False, "Input number within the batch."),
        ("lot_id", "uuid", False, "Lot consumed; joins to inbound_lots."),
        ("quantity_kg", "numeric(12,3)", False, "Kg of the lot used."),
    ], ["batch_id", "input_no"], [("batch_id", "production_batches", "batch_id"), ("lot_id", "inbound_lots", "lot_id")], parent="production_batches"),
    Table("inventory_positions", "inventory", "Stock on hand at the end of Dec 31, 2026, by location, SKU and batch.", [
        ("location_id", "text", False, "Store or DC; joins to locations."),
        ("sku_id", "text", False, "SKU; joins to skus."),
        ("batch_id", "uuid", False, "Batch the units came from; joins to production_batches."),
        ("on_hand", "integer", False, "Units on hand."),
        ("reserved", "integer", False, "Units reserved for open online and wholesale orders."),
        ("best_by", "date", False, "Best-by date of the batch."),
        ("last_counted_at", "timestamptz", False, "Last cycle count."),
    ], ["location_id", "sku_id", "batch_id"], [("location_id", "locations", "location_id"), ("sku_id", "skus", "sku_id"),
                                                ("batch_id", "production_batches", "batch_id")]),
    Table("reorder_points", "inventory", "When each DC reorders each SKU. Starts as manual settings; Module 4's forecast writes source = 'forecast' rows.", [
        ("location_id", "text", False, "DC; joins to locations."),
        ("sku_id", "text", False, "SKU; joins to skus."),
        ("reorder_point", "integer", False, "Reorder when on hand falls to this many units."),
        ("reorder_qty", "integer", False, "Units to reorder."),
        ("source", "text", False, "manual | forecast."),
        ("updated_at", "timestamptz", False, "Last change."),
    ], ["location_id", "sku_id"], [("location_id", "locations", "location_id"), ("sku_id", "skus", "sku_id")]),
    # ---- Stage 2: text corpus with embeddings -----------------------------------------------------------
    Table("product_descriptions", "corpus_products", "Customer-facing product descriptions with embeddings for semantic search.", [
        ("product_id", "text", False, "Product; joins to products."),
        ("description", "text", False, "Description shown on the product page."),
        ("description_embedding", "vector(3072)", False, "gemini-embedding-001 embedding of description (3,072 dimensions)."),
    ], ["product_id"], [("product_id", "products", "product_id")], stage=2),
    Table("reviews", "corpus_reviews", "Verified customer reviews with embeddings.", [
        ("review_id", "uuid", False, "Review key."),
        ("product_id", "text", False, "Product; joins to products."),
        ("sku_id", "text", False, "SKU bought; joins to skus."),
        ("customer_id", "uuid", False, "Reviewer; joins to customers."),
        ("order_id", "uuid", False, "Order; with line_no joins to order_lines (and so to the batch)."),
        ("line_no", "smallint", False, "Order line reviewed."),
        ("rating", "smallint", False, "Stars, 1–5."),
        ("title", "text", False, "Review title."),
        ("body", "text", False, "Review text."),
        ("submitted_at", "timestamptz", False, "When the review was posted."),
        ("verified_purchase", "boolean", False, "TRUE for reviews tied to an order."),
        ("helpful_votes", "integer", False, "Helpful votes from other shoppers."),
        ("body_embedding", "vector(3072)", False, "Embedding of title and body."),
    ], ["review_id"], [("product_id", "products", "product_id"), ("customer_id", "customers", "customer_id"),
                       ("order_id,line_no", "order_lines", "order_id,line_no")], window="submitted_at", year_split=True, stage=2),
    Table("recipes", "corpus_recipes", "Test-kitchen recipes that use Cymbal Foods products.", [
        ("recipe_id", "uuid", False, "Recipe key."),
        ("title", "text", False, "Recipe title."),
        ("cuisine", "text", False, "Cuisine tag."),
        ("course", "text", False, "Course."),
        ("servings", "smallint", False, "Servings."),
        ("total_minutes", "integer", False, "Total time, minutes."),
        ("ingredients_text", "text", False, "Ingredient list."),
        ("instructions", "text", False, "Method."),
        ("recipe_embedding", "vector(3072)", False, "Embedding of title, ingredients and method."),
    ], ["recipe_id"], stage=2),
    Table("recipe_products", "corpus_recipes", "Which products each recipe uses.", [
        ("recipe_id", "uuid", False, "Recipe; joins to recipes."),
        ("product_id", "text", False, "Product; joins to products."),
    ], ["recipe_id", "product_id"], [("recipe_id", "recipes", "recipe_id"), ("product_id", "products", "product_id")], parent="recipes", stage=2),
    Table("supplier_quality_reports", "corpus_quality", "Quality reports: receiving inspections, certificate reviews, retests and supplier audits.", [
        ("report_id", "uuid", False, "Report key."),
        ("lot_id", "uuid", True, "Lot inspected; NULL for supplier audits."),
        ("supplier_id", "uuid", False, "Supplier; joins to suppliers."),
        ("report_type", "text", False, "receiving_inspection | coa_review | retest | supplier_audit."),
        ("inspected_at", "timestamptz", False, "When the inspection happened."),
        ("result", "text", False, "pass | conditional | fail."),
        ("inspector_staff_id", "text", False, "Quality analyst; joins to staff."),
        ("report_text", "text", False, "The report."),
        ("report_embedding", "vector(3072)", False, "Embedding of report_text."),
    ], ["report_id"], [("lot_id", "inbound_lots", "lot_id"), ("supplier_id", "suppliers", "supplier_id")], window="inspected_at", stage=2),
    Table("account_notes", "corpus_accounts", "Account reps' notes on restaurant accounts. Private: visible to the account's rep only.", [
        ("note_id", "uuid", False, "Note key."),
        ("account_id", "uuid", False, "Account; joins to restaurant_accounts."),
        ("staff_id", "text", False, "Author; joins to staff."),
        ("noted_at", "timestamptz", False, "When the note was written."),
        ("note_type", "text", False, "call | visit | email | complaint."),
        ("note_text", "text", False, "The note."),
        ("note_embedding", "vector(3072)", False, "Embedding of note_text."),
    ], ["note_id"], [("account_id", "restaurant_accounts", "account_id"), ("staff_id", "staff", "staff_id")], window="noted_at", stage=2),
]
BY_NAME = {t.name: t for t in T}
LOAD_ORDER = [t.name for t in T]
GROUP_NEEDS = {
    "reference": [], "catalog": ["reference"], "consumers": ["reference"], "wholesale": ["reference", "catalog"],
    "orders": ["reference", "catalog", "consumers", "wholesale"], "supply": ["reference", "catalog"],
    "inventory": ["reference", "catalog"], "corpus_products": ["catalog"], "corpus_reviews": ["orders"],
    "corpus_recipes": ["catalog"], "corpus_quality": ["supply"], "corpus_accounts": ["wholesale"],
}
GROUP_EXTENSIONS = {"corpus_products": ["vector", "alloydb_scann", "google_ml_integration"],
                    "corpus_reviews": ["vector", "alloydb_scann", "google_ml_integration"],
                    "corpus_recipes": ["vector", "alloydb_scann", "google_ml_integration"],
                    "corpus_quality": ["vector", "alloydb_scann", "google_ml_integration"],
                    "corpus_accounts": ["vector", "alloydb_scann", "google_ml_integration"],
                    "catalog": ["pg_trgm"]}
EXTENSION_ORDER = ["vector", "alloydb_scann", "google_ml_integration", "pg_trgm"]


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def schema_sql(stage: int = 1) -> str:
    """stage 1: relational tables (schema.sql). stage 2: the corpus tables with vector columns (schema-corpus.sql)."""
    head = ("-- Cymbal Foods: relational tables only (no indexes, no foreign keys)." if stage == 1 else
            "-- Cymbal Foods: text corpus tables with vector(3072) columns. Needs CREATE EXTENSION vector first.")
    out = [head + " Generated by datagen/cfgen/schemas.py.",
           "-- Load data, then apply the constraints the loaded tables support, then the module's index files.", ""]
    for t in T:
        if t.stage != stage:
            continue
        cols = [f"    {n:<24} {ty}{'' if nul else ' NOT NULL'}" for n, ty, nul, _ in t.columns]
        cols.append(f"    PRIMARY KEY ({', '.join(t.pk)})")
        out.append(f"CREATE TABLE IF NOT EXISTS {t.name} (\n" + ",\n".join(cols) + "\n);")
        out.append(f"COMMENT ON TABLE {t.name} IS {_q(t.description)};")
        for n, _, _, d in t.columns:
            out.append(f"COMMENT ON COLUMN {t.name}.{n} IS {_q(d)};")
        out.append("")
    return "\n".join(out)


def constraints() -> list[dict]:
    """Foreign keys, each with the tables it needs; added NOT VALID then validated, so a re-run is safe."""
    out = []
    for t in T:
        for cols, parent, pcols in t.fks:
            name = f"{t.name}_{cols.replace(',', '_')}_fkey"
            sql = (f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN "
                   f"ALTER TABLE {t.name} ADD CONSTRAINT {name} FOREIGN KEY ({cols.replace(',', ', ')}) "
                   f"REFERENCES {parent} ({pcols.replace(',', ', ')}) NOT VALID; END IF; END $$;\n"
                   f"ALTER TABLE {t.name} VALIDATE CONSTRAINT {name};")
            out.append(dict(name=name, requires=sorted({t.name, parent}), sql=sql))
    return out


INDEX_FILES = {
    "indexes-baseline.sql": [
        "-- Baseline indexes for every module. Deliberately missing the indexes behind application queries Q07, Q11, Q13, Q18 and Q20",
        "-- (DESIGN.md section 10): Module 5's Query Insights and Index Advisor find them under load. Answers: indexes-ops-answers.sql.",
        "CREATE UNIQUE INDEX IF NOT EXISTS skus_upc_key ON skus (upc);",
        "CREATE INDEX IF NOT EXISTS skus_product_id_idx ON skus (product_id);",
        "CREATE INDEX IF NOT EXISTS products_category_id_idx ON products (category_id);",
        "CREATE UNIQUE INDEX IF NOT EXISTS customers_customer_number_key ON customers (customer_number);",
        "CREATE UNIQUE INDEX IF NOT EXISTS restaurant_accounts_account_number_key ON restaurant_accounts (account_number);",
        "CREATE UNIQUE INDEX IF NOT EXISTS orders_order_number_key ON orders (order_number);",
        "CREATE INDEX IF NOT EXISTS orders_customer_id_idx ON orders (customer_id, ordered_at DESC);",
        "CREATE INDEX IF NOT EXISTS orders_ordered_at_idx ON orders (ordered_at);",
    ],
    "indexes-trace.sql": [
        "-- Lot tracing (Module 2): code lookups and the edges of the trace graph.",
        "CREATE UNIQUE INDEX IF NOT EXISTS inbound_lots_lot_code_key ON inbound_lots (lot_code);",
        "CREATE UNIQUE INDEX IF NOT EXISTS production_batches_batch_code_key ON production_batches (batch_code);",
        "CREATE UNIQUE INDEX IF NOT EXISTS purchase_orders_po_number_key ON purchase_orders (po_number);",
        "CREATE INDEX IF NOT EXISTS batch_inputs_lot_id_idx ON batch_inputs (lot_id);",
        "CREATE INDEX IF NOT EXISTS order_lines_batch_id_idx ON order_lines (batch_id);",
    ],
    "indexes-search.sql": [
        "-- Keyword search on the catalog (Module 2 and 3). Vector (ScaNN) indexes for the corpus tables are added in Stage 2.",
        "CREATE INDEX IF NOT EXISTS products_name_trgm_idx ON products USING gin (name gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS ingredients_name_trgm_idx ON ingredients USING gin (name gin_trgm_ops);",
    ],
    "indexes-ops-answers.sql": [
        "-- The indexes Module 5 students should arrive at. Never loaded at Start Lab.",
        "CREATE INDEX IF NOT EXISTS orders_ship_address_hash_idx ON orders (ship_address_hash, ordered_at DESC);   -- Q07",
        "CREATE INDEX IF NOT EXISTS orders_payment_fingerprint_idx ON orders (payment_fingerprint, ordered_at DESC);   -- Q07",
        "CREATE INDEX IF NOT EXISTS inventory_positions_sku_id_idx ON inventory_positions (sku_id);   -- Q11",
        "CREATE INDEX IF NOT EXISTS orders_account_id_idx ON orders (account_id, ordered_at DESC);   -- Q13",
        "CREATE INDEX IF NOT EXISTS order_lines_sku_id_idx ON order_lines (sku_id);   -- Q18",
        "CREATE INDEX IF NOT EXISTS customers_email_idx ON customers (lower(email));   -- Q20",
        "CREATE INDEX IF NOT EXISTS customers_phone_idx ON customers (phone);   -- Q20",
    ],
}

BQ_TYPES = {"text": "STRING", "uuid": "STRING", "integer": "INT64", "smallint": "INT64", "boolean": "BOOL",
            "timestamptz": "TIMESTAMP", "date": "DATE"}


def bq_schema(table: str) -> list[dict]:
    out = []
    for n, ty, nul, d in BY_NAME[table].columns:
        if ty.startswith("vector"):
            out.append(dict(name=n, type="FLOAT64", mode="REPEATED", description=d))
        elif ty.endswith("[]"):
            out.append(dict(name=n, type="STRING", mode="REPEATED", description=d))
        else:
            bt = "NUMERIC" if ty.startswith("numeric") else BQ_TYPES[ty]
            out.append(dict(name=n, type=bt, mode="NULLABLE" if nul else "REQUIRED", description=d))
    return out


GA4_SCHEMA = [
    dict(name="event_date", type="STRING", mode="REQUIRED", description="Event date as GA4 writes it, YYYYMMDD (America/New_York). Each day is its own table, events_YYYYMMDD."),
    dict(name="event_timestamp", type="INT64", mode="REQUIRED", description="Event time, microseconds since the Unix epoch (UTC)."),
    dict(name="event_name", type="STRING", mode="REQUIRED", description="session_start, page_view, view_search_results, view_item_list, view_item, add_to_cart, begin_checkout, add_shipping_info, add_payment_info, purchase."),
    dict(name="event_params", type="RECORD", mode="REPEATED", description="Event parameters: ga_session_id, page_location, search_term.", fields=[
        dict(name="key", type="STRING", mode="NULLABLE"),
        dict(name="value", type="RECORD", mode="NULLABLE", fields=[dict(name="string_value", type="STRING", mode="NULLABLE"),
                                                                  dict(name="int_value", type="INT64", mode="NULLABLE"),
                                                                  dict(name="double_value", type="FLOAT64", mode="NULLABLE")])]),
    dict(name="user_id", type="STRING", mode="NULLABLE", description="customers.customer_number when the visitor is signed in."),
    dict(name="user_pseudo_id", type="STRING", mode="REQUIRED", description="Device/browser identifier."),
    dict(name="device", type="RECORD", mode="NULLABLE", fields=[dict(name="category", type="STRING", mode="NULLABLE"),
                                                                dict(name="operating_system", type="STRING", mode="NULLABLE"),
                                                                dict(name="web_info", type="RECORD", mode="NULLABLE",
                                                                     fields=[dict(name="browser", type="STRING", mode="NULLABLE")])]),
    dict(name="geo", type="RECORD", mode="NULLABLE", fields=[dict(name="country", type="STRING", mode="NULLABLE"),
                                                             dict(name="region", type="STRING", mode="NULLABLE"),
                                                             dict(name="city", type="STRING", mode="NULLABLE")]),
    dict(name="traffic_source", type="RECORD", mode="NULLABLE", fields=[dict(name="name", type="STRING", mode="NULLABLE"),
                                                                        dict(name="medium", type="STRING", mode="NULLABLE"),
                                                                        dict(name="source", type="STRING", mode="NULLABLE")]),
    dict(name="ecommerce", type="RECORD", mode="NULLABLE", description="Set on purchase events; transaction_id = orders.order_number.", fields=[
        dict(name="transaction_id", type="STRING", mode="NULLABLE"), dict(name="purchase_revenue", type="FLOAT64", mode="NULLABLE"),
        dict(name="shipping_value", type="FLOAT64", mode="NULLABLE"), dict(name="tax_value", type="FLOAT64", mode="NULLABLE"),
        dict(name="total_item_quantity", type="INT64", mode="NULLABLE")]),
    dict(name="items", type="RECORD", mode="REPEATED", description="Items on the event; item_id = skus.sku_id.", fields=[
        dict(name="item_id", type="STRING", mode="NULLABLE"), dict(name="item_name", type="STRING", mode="NULLABLE"),
        dict(name="item_category", type="STRING", mode="NULLABLE"), dict(name="price", type="FLOAT64", mode="NULLABLE"),
        dict(name="quantity", type="INT64", mode="NULLABLE")]),
    dict(name="platform", type="STRING", mode="NULLABLE"),
]

SPANNER_TYPES = {"text": "STRING(MAX)", "uuid": "STRING(36)", "integer": "INT64", "smallint": "INT64", "boolean": "BOOL",
                 "timestamptz": "TIMESTAMP", "date": "DATE"}


def spanner_ddl() -> str:
    out = ["-- Cymbal Foods for Spanner (GoogleSQL dialect). Generated by datagen/cfgen/schemas.py.", ""]
    for t in T:
        cols = []
        for n, ty, nul, _ in t.columns:
            if ty.startswith("vector"):
                st = "ARRAY<FLOAT32>(vector_length=>3072)"
            elif ty.endswith("[]"):
                st = "ARRAY<STRING(MAX)>"
            elif ty.startswith("numeric"):
                st = "NUMERIC"
            else:
                st = SPANNER_TYPES[ty]
            cols.append(f"  {n} {st}{'' if nul else ' NOT NULL'}")
        pk = t.pk
        if t.parent:
            ppk = BY_NAME[t.parent].pk
            assert pk[:len(ppk)] == ppk, (t.name, pk, ppk)
        ddl = f"CREATE TABLE {t.name} (\n" + ",\n".join(cols) + f"\n) PRIMARY KEY ({', '.join(pk)})"
        if t.parent:
            ddl += f",\n  INTERLEAVE IN PARENT {t.parent} ON DELETE CASCADE"
        out.append(ddl + ";\n")
    return "\n".join(out)


def write_all(sql_dir, bq_dir) -> None:
    sql_dir.mkdir(parents=True, exist_ok=True)
    bq_dir.mkdir(parents=True, exist_ok=True)
    (sql_dir / "schema.sql").write_text(schema_sql(stage=1) + "\n", encoding="utf-8")
    (sql_dir / "schema-corpus.sql").write_text(schema_sql(stage=2) + "\n", encoding="utf-8")
    (sql_dir / "constraints.sql").write_text("\n\n".join(c["sql"] for c in constraints()) + "\n", encoding="utf-8")
    for f, lines in INDEX_FILES.items():
        (sql_dir / f).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (sql_dir / "spanner-schema.sql").write_text(spanner_ddl(), encoding="utf-8")
    for name in ("orders", "order_lines", "customers", "restaurant_accounts", "products", "skus", "locations", "categories",
                 "inventory_positions", "reorder_points"):
        (bq_dir / f"{name}.json").write_text(json.dumps(bq_schema(name), indent=1) + "\n", encoding="utf-8")
    (bq_dir / "ga4_events.json").write_text(json.dumps(GA4_SCHEMA, indent=1) + "\n", encoding="utf-8")
