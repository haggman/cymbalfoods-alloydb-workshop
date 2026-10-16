"""
Every knob for the Cymbal Foods generator.

Change a number here (or SCALE), rerun `python3 generate_dataset.py`, and every table, the planted
moments, the validation report and the slices regenerate consistently. Nothing else in the package
holds a tunable constant. See datagen/DESIGN.md for what each part means.
"""
from __future__ import annotations

import datetime as dt
import os

# --- Determinism -------------------------------------------------------------------------------
SEED = 20261016                      # master seed; every module derives named streams from it
SCALE = float(os.environ.get("CF_SCALE", "1"))   # 1 = lab, 10 = demo, 100 = Spanner

# --- Calendar (fixed; the data is a snapshot and reads the same way in any year) ---------------
START = dt.date(2024, 1, 1)
END = dt.date(2026, 12, 31)          # inclusive; "now" is the end of this day
NOW_UTC = dt.datetime(2027, 1, 1, 5, 0, 0, tzinfo=dt.timezone.utc)   # midnight Dec 31→Jan 1, US Eastern
SUPPLY_LEAD_IN = dt.date(2023, 10, 1)   # POs, lots and batches start here so Jan 2024 has stock
GA4_START = dt.date(2026, 1, 1)
YEARS = [2024, 2025, 2026]

# --- Volumes at SCALE = 1 (the approved one-pager, catalog per decision D7) --------------------
N_CONSUMERS = 250_000
N_ACCOUNTS = 4_000
ORDERS_PER_YEAR = {                  # target orders per channel per year
    "store":     {2024: 152_000, 2025: 157_000, 2026: 161_000},
    "online":    {2024:  90_500, 2025: 108_500, 2026: 130_000},
    "wholesale": {2024:  62_000, 2025:  66_500, 2026:  71_500},
}
EXTRA_LINES_MEAN = {"store": 0.30, "online": 1.80}   # lines per order = 1 + Poisson(mean); wholesale from the account
WHOLESALE_LINES_MEAN = 7.4
STORE_IDENTIFIED_SHARE = 0.45        # store sales with a loyalty scan
GUEST_SHARE = 0.15                   # consumers who are guest-checkout records
TARGET_PO = 60_000
TARGET_LOTS = 120_000
N_STAFF_REPS_PER_REGION = 15

# --- Corpus facts (Stage 2 writes the text) -------------------------------------------------------
N_REVIEWS = 70_000
N_RECIPES = 3_000
N_QUALITY_REPORTS = 15_000
N_ACCOUNT_NOTES = 10_000
GA4_SESSIONS_2026 = 3_600_000        # ~5.5 events per session → ~20M events, ~3.5% conversion
GA4_PURCHASE_LOSS = 0.08             # online orders with no purchase event (consent banners, blockers)

# --- Pricing ---------------------------------------------------------------------------------------
# Retail price per ounce for a 2.5 oz reference pack, by price band; per-ounce price falls with pack size.
RETAIL_PER_OZ_BY_BAND = {1: 2.10, 2: 3.30, 3: 5.20, 4: 9.50, 5: 16.0}
SIZE_EXPONENT = -0.18                # per-oz multiplier = (net_oz / 2.5) ** SIZE_EXPONENT (a 25 lb box is ~40% of a jar per oz)
JAR_PREMIUM = 1.15                   # glass jars cost more per ounce than refill bags
PER_UNIT_SPECIAL = {                 # families priced per unit instead of per ounce
    "saffron_per_g": 15.0, "vanilla_per_bean": 3.40,
    "liquid_per_floz": {1: 0.55, 2: 0.95, 3: 1.45, 4: 2.40, 5: 4.80},
    "gift_base": {2: 29.0, 3: 44.0, 4: 64.0, 5: 98.0},
}
WHOLESALE_FACTOR = 0.62              # wholesale list as a share of the retail-equivalent price
TIER_DISCOUNT = {"T1": 0.10, "T2": 0.15, "T3": 0.20}
TIER_SHARE = {"T1": 0.50, "T2": 0.35, "T3": 0.15}
CONTRACT_DISCOUNT = (0.03, 0.12)     # below tier price on contract SKUs
COST_FACTOR = 0.55                   # unit cost as a share of wholesale list
ANNUAL_INCREASE = (0.025, 0.055)     # every Jan 1
SUPPLY_PRICE_EVENTS = [              # (ingredient group, date, increase, reason)
    ("vanilla", dt.date(2025, 4, 1), 0.18, "supply: Madagascar crop shortfall"),
    ("saffron", dt.date(2025, 9, 1), 0.09, "supply: Spanish harvest"),
]
SALES_TAX = {"NY": .0888, "NJ": .0663, "PA": .0634, "MA": .0625, "RI": .07, "CT": .0635, "DC": .06, "VA": .0577,
             "GA": .0738, "NC": .0699, "TN": .0955, "SC": .0749, "FL": .0702, "TX": .0820, "LA": .0956,
             "MO": .0841, "CO": .0781, "MN": .0804, "CA": .0882, "AZ": .0838, "OR": 0.0, "UT": .0720,
             "MD": .06, "VT": .0624, "ME": .055, "DE": 0.0, "AL": .0929, "KY": .06, "MS": .0707,
             "IL": .0886, "WI": .0570, "NE": .0697, "OK": .0899, "AR": .0945, "IA": .0694, "IN": .07,
             "OH": .0724, "MI": .06, "NM": .0762, "WA": .0938, "ID": .0603, "NV": .0823, "HI": .0450, "AK": .0182}
FREE_SHIPPING_OVER = 49.0
SHIPPING_FEE = 6.95
ONLINE_PROMO_SHARE = 0.12

# --- Demand shape ------------------------------------------------------------------------------------
ZIPF_BY_POPULARITY = {1: 0.10, 2: 0.25, 3: 0.55, 4: 1.0, 5: 2.2}   # base product weight by content popularity
WEEKDAY = {   # Monday = 0
    "store":     [0.80, 0.85, 0.90, 0.95, 1.10, 1.45, 1.10],
    "online":    [1.20, 1.00, 0.95, 0.95, 0.90, 0.85, 1.15],
    "wholesale": [1.55, 1.40, 1.00, 0.95, 0.85, 0.20, 0.10],
}
HOURS = {     # local-time hour weights 0..23
    "store":     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 5, 7, 6, 5, 5, 6, 7, 6, 4, 0, 0, 0, 0],
    "online":    [2, 1, .6, .4, .3, .3, .5, 1, 1.5, 2, 2.5, 3, 3.2, 3, 2.8, 2.8, 3, 3.3, 3.8, 4.5, 5.6, 6, 5, 3.4],
    "wholesale": [0, 0, 0, 0, 0, .5, 2, 4, 6, 6, 4.5, 3, 1.5, 1, 1, 1, .8, .5, .2, .1, 0, 0, 0, 0],
}
CHANNEL_PACK_PREF = {                # how likely each pack is chosen in a channel
    "store":     {"JAR": 0.62, "B04": 0.38, "B02": 0.38, "G05": .6, "G02": .4, "V02": 1, "O250": .65, "O500": .35,
                  "N250": .6, "N500": .4, "S05": .6, "S12": .4, "C08": 1, "T02": .55, "T20": .45, "K12": .7,
                  "K12G": .3, "D08": .55, "D16": .45, "E02": .45, "E04": .55, "Y08": .6, "Y16": .4, "R01": 1, "GIFT": 1},
    "online":    {"JAR": 0.36, "B04": 0.24, "B02": 0.24, "B08": 0.22, "B16": 0.18, "G05": .45, "G02": .4, "G28": .15,
                  "V02": .5, "V10": .35, "V25": .15, "O250": .5, "O500": .5, "N250": .5, "N500": .5, "S05": .5,
                  "S12": .5, "C08": .7, "C16": .3, "T02": .4, "T20": .3, "T04": .2, "T16": .1, "K12": .55,
                  "K12G": .25, "K32": .2, "D08": .45, "D16": .55, "E02": .35, "E04": .45, "E08": .2, "Y08": .5,
                  "Y16": .5, "R01": .6, "R04": .4, "GIFT": 1},
    "wholesale": {"B08": 0.15, "B16": 0.45, "B80": 0.30, "P400": 0.10, "G28": 1, "V25": .6, "V100": .4, "O3L": .5,
                  "O1G": .5, "N1G": 1, "S1G": 1, "C16": .5, "C80": .5, "T16": 1, "K32": .5, "K80": .5, "D16": .3,
                  "D80": .5, "D400": .2, "E08": .4, "E32": .6, "Y1G": 1, "R16": 1, "GIFTW": 1},
}

# --- Customers ----------------------------------------------------------------------------------------
CONSUMER_ACTIVITY_SIGMA = 1.1        # lognormal spread of how much a consumer buys
ANNUAL_CHURN = 0.25
ONLINE_ONLY_SHARE = 0.18             # consumers outside store metros (online only)
LOYALTY = {"none": 0.45, "member": 0.43, "plus": 0.12}
EMAIL_DOMAINS = ["example.com", "example.net", "example.org"]
MIXED_CASE_EMAIL_SHARE = 0.003
SHARED_BUSINESS_NAMES = 20           # look-alike accounts (P11)

# --- Supply ------------------------------------------------------------------------------------------
BATCH_WEEKS = {5: 2, 4: 3, 3: 4, 2: 6, 1: 8}    # weeks of demand a batch covers, by popularity
BATCH_OVERAGE = (1.03, 1.15)
LOTS_PER_LINE = 1.33
QC = {"released": 0.97, "conditional": 0.025, "rejected": 0.005}

# --- Fraud -------------------------------------------------------------------------------------------
RISK_THRESHOLD = 0.70
RISK_FLAG_SHARE = 0.004
RISK_FALSE_POSITIVE = 0.70

# --- Planted moments (DESIGN section 5) --------------------------------------------------------------
PLANT = {
    "failed_lot_code": "L-ES-2608-0117",
    "failed_lot_item": "smoked_paprika_sweet",
    "failed_lot_dc": "DC-NE",
    "failed_lot_received": dt.date(2026, 8, 24),
    "failed_lot_use": (dt.date(2026, 9, 1), dt.date(2026, 10, 9)),
    "failed_lot_retest": dt.date(2026, 10, 12),
    "clumping_dc": "DC-CE", "clumping_months": (6, 7, 8, 9), "clumping_lift": 4.0,
    "stockout_item": "cinnamon_saigon", "stockout_dc": "DC-CE", "stockout_from": dt.date(2026, 12, 7),
    "stockout_substitute": "cinnamon_korintje", "stockout_po_due": dt.date(2027, 1, 14),
    "sellout_gift": "holiday_baking_set", "sellout_edition": 2026, "sellout_date": dt.date(2026, 12, 14),
    "peak_day": dt.date(2026, 11, 30),
    "fraud_ring_size": 14, "fraud_ring_window": (dt.date(2026, 11, 16), dt.date(2026, 11, 25)),
    "fraud_ring_addresses": 2, "fraud_ring_fingerprints": 3, "fraud_ring_missed": 5,
}
REVIEW_THEMES = {   # base probability a review carries the theme (P6)
    "clumping": 0.025, "stale": 0.02, "too_hot": 0.03, "great_value": 0.06, "wrong_cinnamon": 0.005, "leaking": 0.01,
}
