"""Stage 2 text corpus: fact blocks, prompts, QA and assembly for the five document types.

Gemini writes every document from a fact block built here from the Parquet master copy, and from nothing
else. The planted signals (review themes, the failed lot's retest and complaints, the cinnamon and chile
preferences, the Saigon substitutions) were decided in data by facts.py; this module only puts them into
words for the model and checks that the words came back.

    jobs = corpus.build_jobs(corpus.load(parquet_dir))      # {type: DataFrame of jobs}
    req = corpus.requests(doc_type, jobs[doc_type])         # one request per document (reviews: up to 10)
    hard, soft = corpus.qa(doc_type, job, output)
    table = corpus.assemble(doc_type, jobs[doc_type], outputs, embeddings)

Nothing here calls Gemini; pipeline/11-text-corpus.ipynb does that with cfgen/gemini.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

PROMPT_VERSION = "cf-v1.1"            # bump on ANY prompt, fact-block or QA change; names every artifact
TYPES = ["product_descriptions", "reviews", "recipes", "supplier_quality_reports", "account_notes"]
FACT_TABLES = {"product_descriptions": "product_description_facts", "reviews": "review_facts", "recipes": "recipe_facts",
               "supplier_quality_reports": "quality_report_facts", "account_notes": "account_note_facts"}
ID_COL = {"product_descriptions": "product_id", "reviews": "review_id", "recipes": "recipe_id",
          "supplier_quality_reports": "report_id", "account_notes": "note_id"}
REVIEWS_PER_REQUEST = 10
SAMPLE_N = 25

# words: (soft low, soft high, hard low, hard high). Outside soft is counted; outside hard is regenerated.
WORDS = {"product_descriptions": (90, 150, 60, 190), "reviews": (20, 120, 10, 150), "recipes": (150, 300, 110, 380),
         "supplier_quality_reports": (80, 160, 55, 200), "account_notes": (30, 120, 15, 160)}

HEAT = {0: "no heat", 1: "mild", 2: "medium heat", 3: "hot", 4: "very hot", 5: "extremely hot"}
KIND = {"single_origin": "single-origin spice", "blend": "house blend", "pantry": "pantry item", "gift_set": "gift set"}
CUISINE = {"bbq": "barbecue", "tex_mex": "Tex-Mex", "middle_eastern": "Middle Eastern", "north_african": "North African",
           "southeast_asian": "Southeast Asian", "south_asian": "South Asian", "eastern_european": "Eastern European",
           "american_new": "New American", "bakery_cafe": "bakery-cafe", "bbq_smokehouse": "barbecue smokehouse",
           "cajun_creole": "Cajun and Creole", "caterer_events": "catering and events", "japanese_korean": "Japanese and Korean",
           "mediterranean_greek": "Mediterranean and Greek", "peruvian_latin": "Peruvian and Latin", "tea": "tea",
           "pickling": "pickling", "baking": "baking"}
BUSINESS = {"restaurant": "restaurant", "caterer": "caterer", "food_truck": "food truck", "commissary": "commissary kitchen",
            "bakery": "bakery", "hotel_kitchen": "hotel kitchen", "cafe": "cafe"}
NOTE_TYPE = {"call": "phone call", "visit": "visit", "email": "email", "complaint": "complaint"}
REPORT_TYPE = {"receiving_inspection": "Receiving inspection", "coa_review": "Certificate of analysis review",
               "retest": "Retest", "supplier_audit": "Supplier audit"}
SUPPLIER_KIND = {"grower_exporter": "grower and exporter", "processor": "processor", "co_packer": "co-packer",
                 "trading_house": "trading house", "grower": "grower", "importer": "importer"}
SEGMENT = {"everyday_cook": "an everyday home cook", "home_baker": "a keen home baker", "grill_master": "someone who grills and smokes a lot",
           "global_explorer": "an adventurous cook who tries cuisines from everywhere", "salt_free_shopper": "a cook who avoids added salt",
           "gift_giver": "someone who mostly buys gifts", "pro_at_home": "a serious, chef-trained home cook"}


def cuisine(t: str) -> str:
    return CUISINE.get(t, str(t).replace("_", " ").title() if t not in ("baking", "tea") else t)


def _list(v) -> list:
    """Fact columns hold lists as JSON text in Parquet (writer.write_parquet_frame); tables hold real lists."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    if isinstance(v, str):
        return json.loads(v) if v.startswith(("[", "{")) else [v]
    return list(v)


def _obj(v) -> dict:
    return json.loads(v) if isinstance(v, str) and v else (v if isinstance(v, dict) else {})


def _pick(key: str, options: list):
    """Deterministic choice keyed by a document id: the same job always gets the same option."""
    h = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)
    return options[h % len(options)]


def _et(ts) -> pd.Timestamp:
    return pd.Timestamp(ts).tz_convert("America/New_York") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)


def day(ts) -> str:
    t = _et(ts)
    return f"{t:%b} {t.day}, {t.year}"


def num(x: float) -> str:
    return f"{x:g}" if abs(x) < 1000 else f"{x:,.0f}"


def fold(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(ch)).lower()


# ==================================================================================================
# Loading
# ==================================================================================================
TABLES = ["products", "skus", "ingredients", "blend_components", "gift_set_items", "categories", "suppliers",
          "inbound_lots", "staff", "restaurant_accounts", "locations", "contract_prices"]


def load(parquet_dir: Path) -> dict:
    """The tables and fact tables the corpus is written from (orders: only the columns reviews need)."""
    pq_dir = Path(parquet_dir)
    T = {t: pd.read_parquet(pq_dir / t) for t in TABLES}
    T["orders"] = pd.read_parquet(pq_dir / "orders", columns=["order_id", "ordered_at", "channel"])
    for ty, f in FACT_TABLES.items():
        T[f] = pd.read_parquet(pq_dir / "_facts" / f"{f}.parquet")
    T["recipe_product_facts"] = pd.read_parquet(pq_dir / "_facts" / "recipe_product_facts.parquet")
    return T


def fingerprint(parquet_dir: Path) -> str:
    """sha256 over the fact files: a corpus belongs to exactly one set of facts."""
    h = hashlib.sha256()
    for f in sorted((Path(parquet_dir) / "_facts").glob("*.parquet")):
        h.update(f.name.encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ==================================================================================================
# Fact blocks
# ==================================================================================================
def _product_ctx(T: dict) -> dict:
    P = T["products"].set_index("product_id")
    ING = T["ingredients"].set_index("ingredient_id")
    cats = T["categories"].set_index("category_id").name
    packs = T["skus"].groupby("product_id").pack_description.apply(list)
    bc = T["blend_components"].merge(ING[["name"]], left_on="ingredient_id", right_index=True)
    blend = {p: g.sort_values("pct", ascending=False)["name"].tolist() for p, g in bc.groupby("product_id")}
    gifts = {p: [(P.name.get(x, x), int(q), int(P.heat_level.get(x, 0))) for x, q in zip(g.product_id, g.quantity)]
             for p, g in T["gift_set_items"].groupby("gift_product_id")}
    return dict(P=P, ING=ING, cats=cats, packs=packs, blend=blend, gifts=gifts)


def _heat_line(p, ing) -> str:
    s = HEAT.get(int(p.heat_level), "no heat")
    if ing is not None and pd.notna(ing.get("scoville_max")) and float(ing.get("scoville_max") or 0) > 0:
        s += f" ({num(float(ing['scoville_min']))}–{num(float(ing['scoville_max']))} Scoville heat units)"
    return s


FORM_CATEGORIES = {"SPICE", "HERB", "CHILE", "PEPPER", "AROMATIC", "SALT"}      # where "ground", "whole", "flakes" mean something
HEAT_CATEGORIES = {"CHILE", "PEPPER", "BLEND"}                                  # where "no heat" is worth saying
PLACEHOLDER_FLAVORS = {"varies by origin"}
DESC_OPENINGS = ["Open with what it tastes and smells like.", "Open with the dish it is best in.", "Open with where it comes from.",
                 "Open with what sets it apart.", "Open with how a cook uses it.", "Open with a short, plain sentence about what it is."]


def _sizes(packs: list) -> str:
    if len(packs) <= 2:
        return "; ".join(packs)
    return f"{len(packs)} sizes, from {packs[0]} to {packs[-1]}"


def product_facts(T: dict) -> pd.DataFrame:
    X = _product_ctx(T)
    P, ING = X["P"], X["ING"]
    groups = ING[ING.ambiguity_group.notna()].groupby("ambiguity_group")
    rows = []
    for pid, p in P.iterrows():
        ing = ING.loc[p.ingredient_id] if isinstance(p.ingredient_id, str) and p.ingredient_id in ING.index else None
        flav = [f for f in dict.fromkeys(_list(p.flavor_tags) + (_list(ing.flavor_notes) if ing is not None else []))
                if f not in PLACEHOLDER_FLAVORS]
        smoky = bool(re.search(r"smok", " ".join([p["name"]] + flav), re.I))
        L = [f"Product: {p['name']}", f"Category: {X['cats'].get(p.category_id, p.category_id)}"]
        if p.product_type in ("blend", "gift_set"):
            L.append(f"Kind: {KIND[p.product_type]}")
        if p.category_id in FORM_CATEGORIES:
            L.append(f"Form: {p.form}")
        if isinstance(p.origin_country, str):
            L.append(f"Origin: {p.origin_country}")
        if ing is not None and isinstance(ing.botanical_name, str):
            L.append(f"Botanical name: {ing.botanical_name}")
        if ing is not None and len(_list(ing.aliases)):
            L.append("Also called: " + ", ".join(_list(ing.aliases)))
        if p.product_type == "gift_set":
            items = X["gifts"].get(pid, [])
            heats = [h for _n, _q, h in items]
            if heats and max(heats) > 0:
                hottest = items[int(np.argmax(heats))][0]
                L.append(f"Heat: every item is {HEAT[max(heats)]}" if min(heats) == max(heats) else
                         f"Heat: from {HEAT[min(heats)]} to {HEAT[max(heats)]}; the hottest is {hottest}")
        elif p.heat_level > 0 or p.category_id in HEAT_CATEGORIES or smoky or (
                isinstance(p.ambiguity_group, str) and p.ambiguity_group in ("paprika", "pepper", "chile_powder")):
            L.append(f"Heat: {_heat_line(p, ing)}")
        if flav:
            L.append("Flavor: " + ", ".join(flav))
        ct = _list(p.cuisine_tags)
        if p.category_id in ("TEA", "COFFEE"):
            other = [cuisine(c) for c in ct if c != "tea"]
            L.append("Use: brewing" + (f"; it also suits {', '.join(other)} cooking" if other and p.category_id == "TEA" else ""))
        elif ct:
            L.append("Good in: " + ", ".join(cuisine(c) for c in ct) + " cooking")
        if pid in X["blend"]:
            L.append("Blend ingredients, largest share first: " + ", ".join(X["blend"][pid]))
        if pid in X["gifts"]:
            L.append("The set contains: " + "; ".join(f"{n}" + (f" (x{q})" if q > 1 else "") for n, q, _h in X["gifts"][pid]))
        al = _list(p.allergens)
        if al:
            L.append("Allergens: " + ", ".join(al))
        if p.is_salt_free and p.product_type == "blend":
            L.append("Salt-free: yes")
        if p.is_organic:
            L.append("Organic: yes")
        packs = X["packs"].get(pid, [])
        if packs:
            L.append("Sizes: " + _sizes(packs))
        g = p.ambiguity_group if isinstance(p.ambiguity_group, str) else None
        if g and g in groups.groups and ing is not None:
            sib = groups.get_group(g)
            sib = sib[sib.index != p.ingredient_id]
            if len(sib):
                L.append("Often mistaken for: " + "; ".join(
                    f"{n}" + (f" ({b})" if isinstance(b, str) and b != (ing.botanical_name or "") else "")
                    for n, b in zip(sib.name.head(4), sib.botanical_name.head(4))) +
                    ". Make clear which one this is and how it differs.")
        L.append(f"Length: {WORDS['product_descriptions'][0]}–{WORDS['product_descriptions'][1]} words")
        L.append(_pick(pid, DESC_OPENINGS))
        heat = max([h for _n, _q, h in X["gifts"].get(pid, [])] + [int(p.heat_level)])
        rows.append(dict(job_id=pid, facts="\n".join(L), name=p["name"], heat=heat, smoky=smoky,
                         product_type=p.product_type, ambiguity_group=g, category_id=p.category_id))
    return pd.DataFrame(rows)


THEME_TEXT = {                      # short cues: the model says them in the shopper's own words
    "clumping": "clumped or caked into hard lumps",
    "stale": "weak or stale, little aroma",
    "too_hot": "much hotter than expected",
    "great_value": "good value for the amount (no prices or figures)",
    "leaking": "container leaked in transit; arrived sticky or partly spilled",
    "musty_smell": "musty, damp smell on opening",
}
LOW_STAR_REASONS = ["the flavor was not to their taste", "stronger than they like", "did not suit the dish they made",
                    "they prefer a different style of this product", "bigger pack than they needed; they won't finish it",
                    "the flavor was not what they pictured from the description"]
THEME_RE = {
    "clumping": r"clump|cak(e|ed|ing)\b|lump|brick|rock[- ]hard|hard(ened)? (block|mass)|solid",
    "stale": r"stale|weak|flat|faded|dull|bland|lifeless|little (aroma|flavor|smell)|no (aroma|flavor|smell)|old|lost",
    "too_hot": r"\bhot|heat|spic|fier|burn|kick",
    "great_value": r"value|price|money|worth|deal|bargain|afford|cheap|lasts|last(ed)? (me|us|for)|stretch|bulk",
    "wrong_cinnamon": r"cinnamon|ceylon|cassia|saigon|korintje",
    "leaking": r"leak|spill|seep|sticky|ooz|drip|mess",
    "musty_smell": r"must(y|iness)|damp|mildew|basement|moldy|mouldy|cellar",
}
MUSTY_RE = re.compile(r"must(y|iness)|mildew|moldy|mouldy|\bdamp\b", re.I)
OPENINGS = ["Open with the verdict.", "Open with what they cooked with it.", "Open with how it compares to what they used before (no brand names).",
            "Open with how it arrived or how it looked when opened.", "Open with a short, blunt sentence.",
            "Open with who they cooked for.", "Open with the smell or taste.", "Open with why they bought it."]


def _cinnamon_theme(name: str) -> str:
    if re.search(r"ceylon", name, re.I):
        return "wrong kind of cinnamon for them: wanted the strong, spicy cassia type; this Ceylon is milder"
    return ("wrong kind of cinnamon for them: wanted mild, delicate Ceylon (true) cinnamon; this is the bold cassia type"
            if re.search(r"saigon|korintje|cassia", name, re.I) else
            "the cinnamon in it was a different kind than they expected")


def review_facts(T: dict) -> pd.DataFrame:
    F = T["review_facts"]
    P = T["products"].set_index("product_id")
    S = T["skus"].set_index("sku_id")
    O = T["orders"].set_index("order_id")
    rows = []
    for r in F.itertuples(index=False):
        name = P.name[r.product_id]
        o = O.loc[r.order_id]
        themes = _list(r.themes)
        how = "online" if o.channel == "online" else "in a Cymbal Foods store"
        days = max(1, (pd.Timestamp(r.submitted_at) - pd.Timestamp(o.ordered_at)).days)
        must = []
        for t in themes:
            if t == "wrong_cinnamon":
                must.append(_cinnamon_theme(name))
            elif t == "leaking" and o.channel != "online":
                must.append("bottle leaked on the way home; sticky")
            else:
                must.append(THEME_TEXT[t])
        L = [f"Product: {name} ({S.pack_description[r.sku_id]})",
             f"Bought: {how}, {_et(o.ordered_at):%B %Y}; reviewed {days} days later",
             f"Stars: {int(r.rating)} of 5",
             f"Reviewer: {SEGMENT.get(r.x_segment, 'a home cook')}",
             ("Points to make: " + "; ".join(must)) if must else
             (f"Points to make: none about quality; the low rating is because {_pick(r.review_id, LOW_STAR_REASONS)}"
              if int(r.rating) <= 2 else "Points to make: none in particular; how they used it, in a way that fits the stars"),
             f"Length: about {int(r.target_words)} words",
             _pick(r.review_id, OPENINGS)]
        rows.append(dict(job_id=r.review_id, facts="\n".join(L), name=name, themes=themes, rating=int(r.rating),
                         channel=o.channel, ambiguity_group=P.ambiguity_group.get(r.product_id), target=int(r.target_words)))
    return pd.DataFrame(rows)


DRINK_OK = {"drink", "dessert", "baked good", "breakfast"}


def recipe_course(course: str, pids: list, P: pd.DataFrame) -> str:
    """facts.py picks the course before the products; a recipe built around tea or coffee becomes a drink."""
    if course not in DRINK_OK and any(P.category_id.get(p) in ("TEA", "COFFEE") for p in pids):
        return "drink"
    return course


def recipe_facts(T: dict) -> pd.DataFrame:
    F = T["recipe_facts"]
    P = T["products"].set_index("product_id")
    rows = []
    for r in F.itertuples(index=False):
        pids = _list(r.product_ids)
        course = recipe_course(r.course, pids, P)
        L = [f"Cuisine: {cuisine(r.cuisine)}", f"Course: {course}", f"Serves: {int(r.servings)}",
             f"Total time: {int(r.total_minutes)} minutes", "Cymbal Foods products to use, each named exactly as written here:"]
        for pid in pids:
            p = P.loc[pid]
            tags = ", ".join(_list(p.flavor_tags)[:3])
            L.append(f"- {p['name']} ({p.form}{'; ' + tags if tags else ''}; {HEAT.get(int(p.heat_level), 'no heat')})")
        L.append(f"Length: about {int(r.target_words)} words in total")
        rows.append(dict(job_id=r.recipe_id, facts="\n".join(L), names=[P.name[p] for p in pids], cuisine=r.cuisine,
                         course=course, servings=int(r.servings), minutes=int(r.total_minutes)))
    return pd.DataFrame(rows)


MOISTURE_SPEC = {"herb": 11.0, "salt": 0.75}
HARVESTED = {"chile", "peppercorn", "spice", "seed", "herb", "aromatic", "flower", "citrus", "specialty", "tea", "coffee"}
REPORT_OPENINGS = ["Open with the lot or supplier and what was done.", "Open with the result.", "Open with the date and the inspection.",
                   "Open with what was received or audited.", "Open with the reason for the inspection, if there is one; otherwise the result."]


def _supplier_line(sp, country: str) -> str:
    kind = SUPPLIER_KIND.get(sp.supplier_kind, sp.supplier_kind)
    name = sp["name"]
    return f"Supplier: {name} ({kind})" if country.lower() in name.lower() else f"Supplier: {name}, {country} ({kind})"

CONDITIONAL_REASONS = [
    ("labels", "the lot number on several bags did not match the purchase order; corrected on receipt and released"),
    ("packaging", "outer cartons were damaged in transit; inner liners intact; contents sampled and released"),
    ("coa_late", "the supplier's certificate of analysis arrived late; release waited until it was received"),
    ("sift", "trace stems and chaff found; lot to be sifted before it is used"),
    ("moisture", "moisture near the upper limit; released for use within the next 90 days"),
]
AUDIT_FINDINGS = [
    "pest-control logs were incomplete for two months",
    "metal-detector check records were missing for one shift",
    "allergen signage was missing in the blending room",
    "the traceability exercise took longer than the required four hours",
    "drying-yard covers needed repair before the rainy season",
]
AUDIT_MINOR = ["no findings", "one minor observation: a cracked floor tile in the packing hall, already scheduled for repair",
               "one minor observation: hand-wash signage faded at one station", "no findings; records were complete and easy to trace"]


def quality_facts(T: dict) -> pd.DataFrame:
    F = T["quality_report_facts"]
    LT = T["inbound_lots"].set_index("lot_id")
    SUP = T["suppliers"].set_index("supplier_id")
    ING = T["ingredients"].set_index("ingredient_id")
    ST = T["staff"].set_index("staff_id")
    LOC = T["locations"].set_index("location_id")
    countries = dict(zip(*_country_names()))
    rows = []
    for r in F.itertuples(index=False):
        f = _obj(r.facts)
        sp = SUP.loc[r.supplier_id]
        dc = LOC.loc[r.dc_id]
        L = [f"Report type: {REPORT_TYPE[r.report_type]}", f"Date: {day(r.inspected_at)}",
             f"{'Auditor' if r.report_type == 'supplier_audit' else 'Inspector'}: {ST.full_name[r.inspector_staff_id]}, quality analyst, "
             f"{dc['name']} ({dc.city}, {dc.state})",
             _supplier_line(sp, countries.get(sp.country_code, sp.country_code))]
        reason = action = None
        if r.report_type == "supplier_audit":
            L.append(f"Supplier quality tier: {sp.quality_tier}")
            if r.result == "conditional":
                fnd = _pick(r.report_id, AUDIT_FINDINGS)
                due = pd.Timestamp(r.inspected_at) + pd.Timedelta(days=60)
                L += [f"Result: conditional", f"Finding: {fnd}", f"Corrective action due: {day(due)}"]
            else:
                L += ["Result: pass", f"Finding: {_pick(r.report_id, AUDIT_MINOR)}"]
        else:
            lot = LT.loc[r.lot_id]
            ing = ING.loc[r.ingredient_id]
            L += [f"Lot: {r.lot_code}",
                  f"Ingredient: {ing['name']}" + (f"; botanical name {ing.botanical_name}" if isinstance(ing.botanical_name, str) else ""),
                  f"Origin: {lot.origin_country}" + (f", harvest {int(lot.harvest_year)}"
                                                      if pd.notna(lot.harvest_year) and ing.ingredient_type in HARVESTED else ""),
                  f"Received: {num(float(lot.quantity_kg))} kg on {day(lot.received_at)}"]
            if r.report_type == "coa_review":
                L.append("Scope: the supplier's certificate of analysis compared with our own receiving tests")
            if r.x_plant == "failed_lot_retest":
                L.append("Why retested: restaurant customers reported a musty smell in products made from this lot")
            res = ["Results (limit in brackets):"]
            if f.get("moisture_pct") is not None:
                # the house specification by ingredient type, above every released lot's range (supply.py);
                # v1's fact files carry a tighter herb and salt figure that a few released lots exceed
                spec = MOISTURE_SPEC.get(ing.ingredient_type, 12.0)
                res.append(f"- Moisture: {f['moisture_pct']:g}% (limit {spec:g}%)")
            if f.get("volatile_oil_pct") is not None:
                res.append(f"- Volatile oil: {f['volatile_oil_pct']:g}%")
            res.append(f"- Total plate count: {f['total_plate_count_log_cfu_g']:g} log CFU/g (limit {f['total_plate_count_spec_max']:g})")
            res.append("- Foreign matter: " + ("trace found" if f["foreign_matter"] == "trace" else "none found"))
            if f.get("color_asta") is not None:
                res.append(f"- Color: {int(f['color_asta'])} ASTA")
            L += res
            if r.result == "fail" and r.x_plant == "failed_lot_retest":
                reason = "moisture and total plate count above specification"
                action = "lot placed on hold; every production batch made from it placed on hold"
            elif r.result == "fail":
                reason = "total plate count above specification"
                action = "lot rejected and returned to the supplier"
            elif r.result == "conditional":
                key, reason = _pick(r.report_id, CONDITIONAL_REASONS)
                ok_m = bool(f.get("moisture_pct") and f["moisture_pct"] >= 0.85 * MOISTURE_SPEC.get(ing.ingredient_type, 12.0))
                if (key == "moisture" and not ok_m) or (key == "sift" and f.get("foreign_matter") != "trace"):
                    reason = _pick(r.report_id + "/fallback", [x[1] for x in CONDITIONAL_REASONS[:3]])
                action = "released with the condition noted"
            else:
                reason = "all results within specification"
                action = "released to production"
            L += [f"Result: {r.result}", f"Reason: {reason}", f"Action: {action}"]
        L.append(f"Length: about {int(r.target_words)} words")
        L.append(_pick(r.report_id, REPORT_OPENINGS))
        rows.append(dict(job_id=r.report_id, facts="\n".join(L), report_type=r.report_type, result=r.result,
                         lot_code=r.lot_code if isinstance(r.lot_code, str) else None, x_plant=r.x_plant))
    return pd.DataFrame(rows)


def _country_names():
    p = Path(__file__).resolve().parent.parent / "content" / "supplier_countries.csv"
    d = pd.read_csv(p)
    return d["iso2"].tolist(), d["country"].tolist()


TOPIC = {
    "general_checkin": "Routine check-in: how business is going and what is moving on their menu",
    "new_menu": "They are planning a new menu item and asked what to use",
    "contract_renewal": "Contract renewal discussion",
    "price_question": "A question about a price on their contract",
    "delivery_issue": "Complaint about a late or short delivery",
    "clumping": "Complaint: a ground product clumped in the bag",
    "seasonal_planning": "Planning their orders for the coming season",
    "preference": "A standing preference the chef wants on record",
    "musty_paprika": "Complaint: the chef reports a musty smell from product delivered recently and wants it replaced",
    "substitution": "Their item was out of stock at the distribution center, so a substitute was sent; the rep called to explain",
}


COMPLAINT_TOPICS = {"delivery_issue", "clumping", "musty_paprika"}
GROUND_WORDS = re.compile(r"ground|powder|granulated|fine\b|blend|seasoning|rub|spice\b|masala", re.I)
FALLBACK_PREFERENCES = ["no substitutions on {item} without a call first", "always the largest pack of {item}, never two smaller ones",
                        "{item} only; don't swap in the organic version without asking",
                        "deliveries before lunch service, and {item} never backordered without a heads-up"]
DELIVERY_PROBLEMS = ["the order arrived a day late", "the order came in short: {item} was missing",
                     "the delivery missed its window and arrived after service had started", "the order arrived without {item}"]


def note_type_for(topic: str, raw: str) -> str:
    """facts.py drew note types independently of topics; a complaint must be about something, and a complaint topic is one."""
    if topic in COMPLAINT_TOPICS:
        return "complaint"
    return "call" if raw == "complaint" else raw


def note_facts(T: dict) -> pd.DataFrame:
    F = T["account_note_facts"]
    A = T["restaurant_accounts"].set_index("account_id")
    ST = T["staff"].set_index("staff_id")
    LOC = T["locations"].set_index("location_id")
    P = T["products"].set_index("product_id")
    S = T["skus"].set_index("sku_id")
    cp = T["contract_prices"].sort_values(["account_id", "effective_from"])
    usual = {a: list(dict.fromkeys(g.sku_id)) for a, g in cp.groupby("account_id")}
    pname = lambda s: P.name[S.product_id[s]] if s in S.index else s
    sku_name = lambda s: f"{pname(s)} ({S.pack_description[s]})" if s in S.index else s
    ground_skus = [s for s in S.index if GROUND_WORDS.search(pname(s)) and S.sold_wholesale[s]]
    rows = []
    for r in F.itertuples(index=False):
        a = A.loc[r.account_id]
        f = _obj(r.facts)
        ntype = note_type_for(r.topic, r.note_type)
        u = usual.get(r.account_id, [])
        L = [f"Account: {a.business_name}, {BUSINESS.get(a.business_type, a.business_type)} ({cuisine(a.cuisine)}), {a.city}, {a.state}",
             f"Contact: {a.contact_name}", f"Written by: {ST.full_name[r.staff_id]}, account rep",
             f"Date: {day(r.noted_at)}", f"Note type: {NOTE_TYPE.get(ntype, ntype)}", f"Topic: {TOPIC[r.topic]}"]
        pref = f.get("preference")
        if r.topic == "preference" and not pref and u:
            pref = _pick(r.note_id, FALLBACK_PREFERENCES).format(item=pname(_pick(r.note_id + "/i", u[:3])))
        if r.topic == "delivery_issue":
            dc = LOC.loc[a.serving_dc_id]
            what = _pick(r.note_id, DELIVERY_PROBLEMS).format(item=sku_name(_pick(r.note_id + "/i", u[:3])) if u else "one item")
            L.append(f"What happened: {what}, from the {dc['name']} ({dc.city}, {dc.state})")
        if pref:
            L.append(f"Preference: {pref}")
        if f.get("contract_lines"):
            L.append("Contract prices: " + "; ".join(f"{x['sku']} at ${x['price']:,.2f}" for x in f["contract_lines"]))
        if r.topic == "contract_renewal" and pd.notna(a.contract_end):
            L.append(f"Contract ends: {day(pd.Timestamp(a.contract_end))}")
        items = f.get("items") or []
        if r.topic == "substitution" and items:
            half = max(1, len(items) // 2)
            L.append("Ordered: " + "; ".join(items[:half]))
            L.append("Substituted: " + "; ".join(items[half:]))
        elif r.topic == "musty_paprika" and items:
            L.append("Items: " + "; ".join(items))
        elif r.topic == "clumping":
            mine = [s for s in u if GROUND_WORDS.search(pname(s))]
            L.append("Item: " + sku_name(_pick(r.note_id, mine or ground_skus)))
        elif r.topic in ("new_menu", "general_checkin", "seasonal_planning", "price_question", "contract_renewal") \
                and not f.get("contract_lines") and u:
            L.append("They usually buy: " + "; ".join(sku_name(s) for s in u[:3]))
        L.append(f"Length: about {int(r.target_words)} words")
        rows.append(dict(job_id=r.note_id, facts="\n".join(L), topic=r.topic, note_type=ntype, preference=pref, items=items))
    return pd.DataFrame(rows)


def build_jobs(T: dict) -> dict:
    """{type: DataFrame(job_id, facts, ...check fields)} for all five types, in a stable order."""
    return {"product_descriptions": product_facts(T), "reviews": review_facts(T), "recipes": recipe_facts(T),
            "supplier_quality_reports": quality_facts(T), "account_notes": note_facts(T)}


# ==================================================================================================
# Prompts and response schemas
# ==================================================================================================
COMMON_RULES = (
    "Use only the facts given. Never invent numbers, dates, prices, people, places, awards or company history. "
    "The facts are notes for you: never copy their wording or their labels (such as 'pantry item', 'single-origin spice', "
    "'often mistaken for' or 'points to make'); write naturally. "
    "Never name a real company, brand, store, website, app or delivery carrier; the only company you may name is Cymbal Foods, "
    "and only where it is natural. "
    "Make no health, nutrition or medical claims of any kind (nothing about health benefits, immunity, inflammation, digestion, "
    "antioxidants, vitamins or curing anything). Do not use the construction \"it's not X, it's Y\" or \"not just X, but Y\". "
    "Never mention facts, notes, prompts, data or instructions. Write plain prose in American English: no markdown, headings, "
    "bullet points, brackets or emoji."
)
SYSTEM = {
    "product_descriptions": (
        "You write product descriptions for the website of Cymbal Foods, a specialty spice and pantry retailer. Each description is "
        "warm, concrete and useful to a cook: what it is, what it tastes and smells like, and two or three ways to use it. "
        "When the facts give a heat level, state it plainly and accurately: a product with no heat or mild heat must never be called "
        "fiery or very hot, and a hot one must say so. A smoked product must say it is smoky. When the facts list what it is often "
        "mistaken for, make clear which one this is and how it differs. Follow the opening hint, and do not start with the full "
        "product name followed by 'is a'. Mention sizes at most once, briefly, or not at all. " + COMMON_RULES),
    "reviews": (
        "You write customer reviews for products bought from Cymbal Foods, a specialty spice and pantry retailer. Each review is by a "
        "different ordinary shopper with their own voice: some short and plain, some chatty, a few with a typo-free but casual tone. "
        "The tone must fit the stars. Every point under 'Points to make' must come through clearly, in the shopper's own words, written "
        "as the shopper (I, we, my family); never copy the wording of the facts. Say nothing about smell, clumping, leaks, staleness "
        "or the wrong kind of product unless it is listed: a low rating with no listed problem is about taste or fit, never quality. "
        "Do not mention the number of stars, the price, or how many days it took. Each review has a short title of 2 to 8 words. "
        + COMMON_RULES),
    "recipes": (
        "You are a recipe developer in the Cymbal Foods test kitchen. Write one complete, cookable recipe that uses every listed Cymbal "
        "Foods product. In ingredients_text, name each listed product exactly once, exactly as written in the list, with no 'Cymbal "
        "Foods' in front; in the instructions, call it by a short everyday name (the cumin, the ponzu, the smoked paprika). Add "
        "everyday ingredients as needed (no brand names). ingredients_text is one ingredient per line, quantity first, in US units, "
        "with fractions (1/2 cup), never decimals. instructions is numbered steps, one per line ('1. ...'), that fit the total time "
        "and the number of servings. The title is appealing and plain, under 10 words, and does not include the word Cymbal. "
        "Amounts, temperatures and times in the recipe are yours to choose. " +
        COMMON_RULES.replace("Never invent numbers, dates, prices, people", "Never invent dates, prices, people")),
    "supplier_quality_reports": (
        "You are a quality analyst at Cymbal Foods writing the formal record of a quality inspection or supplier audit. Write it as one "
        "or two paragraphs of plain, precise prose in the past tense, the way a QA team writes for the file: what was inspected, the "
        "results against their limits, the result and the action taken. Quote every test value and limit exactly as given, with its "
        "units; do not convert units or round. Include the lot code when there is one. Follow the opening hint. " + COMMON_RULES),
    "account_notes": (
        "You are an account rep at Cymbal Foods, a spice and pantry distributor to restaurants, typing a private note in the CRM right "
        "after talking with a customer. Busy-rep shorthand is fine: short sentences, first person, past tense, no greeting or "
        "sign-off. Cover what was discussed, what the customer wants, and the next step. Use the contact's first name. Mention at most "
        "two of the items they usually buy, by a short name, and only when they matter to the note. Quote prices exactly as given. "
        "Do not name your own company. " + COMMON_RULES),
}
SCHEMA = {
    "product_descriptions": {"type": "OBJECT", "properties": {"description": {"type": "STRING"}}, "required": ["description"]},
    "reviews": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": {"type": "STRING"}, "title": {"type": "STRING"}, "body": {"type": "STRING"}}, "required": ["id", "title", "body"]}},
    "recipes": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}, "ingredients_text": {"type": "STRING"},
                                                 "instructions": {"type": "STRING"}},
                "required": ["title", "ingredients_text", "instructions"]},
    "supplier_quality_reports": {"type": "OBJECT", "properties": {"report_text": {"type": "STRING"}}, "required": ["report_text"]},
    "account_notes": {"type": "OBJECT", "properties": {"note_text": {"type": "STRING"}}, "required": ["note_text"]},
}
FIELDS = {"product_descriptions": ["description"], "reviews": ["title", "body"],
          "recipes": ["title", "ingredients_text", "instructions"], "supplier_quality_reports": ["report_text"],
          "account_notes": ["note_text"]}
TEMPERATURE = {"product_descriptions": 0.7, "reviews": 0.9, "recipes": 0.8, "supplier_quality_reports": 0.5, "account_notes": 0.8}
ASK = {"product_descriptions": "Write the product description now, as JSON with the field description.",
       "recipes": "Write the recipe now, as JSON with title, ingredients_text and instructions.",
       "supplier_quality_reports": "Write the report now, as JSON with the field report_text.",
       "account_notes": "Write the note now, as JSON with the field note_text."}


def requests(doc_type: str, jobs: pd.DataFrame, repair: dict | None = None) -> list[dict]:
    """One request per document; reviews go up to REVIEWS_PER_REQUEST to a request. repair: {job_id: note}."""
    repair = repair or {}
    out = []
    if doc_type == "reviews":
        recs = jobs.to_dict("records")
        # a repaired review goes alone, so its correction note can't bleed into the others
        solo = [r for r in recs if r["job_id"] in repair]
        rest = [r for r in recs if r["job_id"] not in repair]
        groups = [rest[i:i + REVIEWS_PER_REQUEST] for i in range(0, len(rest), REVIEWS_PER_REQUEST)] + [[r] for r in solo]
        for g in groups:
            parts = [f"REVIEW r{k + 1}\n{r['facts']}" for k, r in enumerate(g)]
            note = repair.get(g[0]["job_id"], "") if len(g) == 1 else ""
            prompt = (f"Write these {len(g)} reviews, each by a different shopper.\n\n" + "\n\n".join(parts) + note +
                      f"\n\nReturn a JSON array of {len(g)} objects in the same order, each with id (r1, r2, ...), title and body.")
            out.append(dict(request_id=g[0]["job_id"], job_ids=[r["job_id"] for r in g], prompt=prompt, doc_type=doc_type))
    else:
        for r in jobs.to_dict("records"):
            out.append(dict(request_id=r["job_id"], job_ids=[r["job_id"]], doc_type=doc_type,
                            prompt=f"FACTS\n{r['facts']}{repair.get(r['job_id'], '')}\n\n{ASK[doc_type]}"))
    return out


def parse(doc_type: str, text: str, job_ids: list[str]) -> dict:
    """Model JSON → {job_id: {field: text}}. Raises on anything malformed, so the call is retried."""
    d = json.loads(text)
    if doc_type == "reviews":
        if not isinstance(d, list) or len(d) != len(job_ids):
            raise ValueError(f"expected {len(job_ids)} reviews, got {len(d) if isinstance(d, list) else type(d).__name__}")
        by = {str(x.get("id", "")).strip().lower(): x for x in d}
        out = {}
        for k, jid in enumerate(job_ids):
            x = by.get(f"r{k + 1}", d[k])
            out[jid] = {"title": str(x.get("title") or "").strip(), "body": str(x.get("body") or "").strip()}
        return out
    return {job_ids[0]: {f: str(d.get(f) or "").strip() for f in FIELDS[doc_type]}}


def embed_text(doc_type: str, out: dict) -> str:
    """Exactly the text that is embedded, and so exactly what the embedding column describes."""
    if doc_type == "reviews":
        return f"{out['title']}\n{out['body']}"
    if doc_type == "recipes":
        return f"{out['title']}\n{out['ingredients_text']}\n{out['instructions']}"
    return out[FIELDS[doc_type][0]]


# ==================================================================================================
# QA: hard failures are regenerated, soft flags are counted for a look
# ==================================================================================================
_PLUMBING = re.compile(r"(?i)\b(as an ai|language model|fact block|the facts|these facts|the notes say|the data|this data|dataset|"
                       r"prompt|instructions? (?:say|given)|json|must say|points to make|cymbal foods data)\b")
_BRANDS = re.compile(r"(?i)\b(mccormick|penzeys|spice islands|simply organic|frontier co-?op|burlap (?:&|and) barrel|morton|"
                     r"diamond crystal|badia|tone'?s|durkee|lawry'?s|old bay|tabasco|cholula|frank'?s|kikkoman|goya|maldon|"
                     r"trader joe'?s|whole foods|costco|walmart|amazon|kroger|target|safeway|publix|aldi|instacart|sysco|us foods|"
                     r"restaurant depot|kitchenaid|le creuset|instant pot|vitamix|cuisinart|lodge|pyrex|yelp|google|facebook|"
                     r"instagram|tiktok|youtube|pinterest|fedex|ups|usps|dhl|doordash|uber eats|grubhub|starbucks|nespresso|"
                     r"keurig|hershey'?s|nestl[eé]|heinz|hellmann'?s|kraft|crisco|domino|c&h|land o'?lakes|kerrygold|"
                     r"ghirardelli|valrhona|lipton|twinings|tazo|bigelow|celestial seasonings|nielsen-massey|rodelle)\b")
_HEALTH = re.compile(r"(?i)\b(anti-?inflammator\w*|inflammation|antioxidants?|immun\w*|detox\w*|"
                     r"cures? (?:a |an |the |your )?(?:colds?|ills|ailments?|illness\w*|headaches?|anything|what ails)|healing|heals?|"
                     r"medicin\w*|remed(?:y|ies)|superfoods?|diseases?|cancer|diabet\w*|cholesterol|blood (?:sugar|pressure)|"
                     r"weight loss|metabolism|gut health|digestion|digestive|health benefits?|good for (?:you|your)\b|"
                     r"nutrient-?dense|vitamins?|probiotic\w*|wellness|healthy(?! (?:pinch|dash|sprinkle|splash|dose|amount|handful|glug|drizzle|dusting|spoonful|squeeze|grind|shake)))\b")
_NOT_X = re.compile(r"(?i)(?:\b(?:it|this|that|they)(?:'s|’s| is| are|'re) not (?:just |only |merely )?[^.;!?]{1,50}?[,;—–-]\s*"
                    r"(?:it|this|that|they)(?:'s|’s| is| are|'re)\b)|(?:\bnot (?:just|only|merely) (?:a |an |the )?[^.;!?]{1,40}?[,;—–-]?\s*but\b)")
_MD = re.compile(r"(\*\*|__|^#|^\s*[-*•]\s)", re.M)
_GARBLED = re.compile(r"[\x00-\x08\x0b-\x1f\x7f�]|[^\W\d_][!?#$%^*|~\\@][^\W\d_]")
_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,]*\d)|(?<![\w.])\d+(?:\.\d+)?")
_HOT_WORDS = re.compile(r"(?i)\b(fiery|scorching|blazing|blistering|searing heat|intense heat|serious heat|fierce heat|very hot|"
                        r"extremely hot|five-alarm|tongue-scorching|mouth-burning)\b")
_NEGATED = re.compile(r"(?i)\b(no|not|without|never|zero|nothing|none|isn't|is not|won't|free of)\b(?:\W+\w+){0,3}\W*$")


def _overstated(text: str):
    """A heat word not negated within the three words before it ('without any fiery heat' is fine)."""
    for m in _HOT_WORDS.finditer(text):
        if not _NEGATED.search(text[max(0, m.start() - 60):m.start()]):
            return m.group(0)
    return None


def _nums(text: str) -> set:
    out = set()
    for x in _NUM.findall(text):
        try:
            out.add(float(x.replace(",", "")))
        except ValueError:
            pass
    return out


def _words(s: str) -> int:
    return len(str(s).split())


def _common(text: str, hard: list, allow_md: bool = False):
    if _PLUMBING.search(text):
        hard.append(f"plumbing: {_PLUMBING.search(text).group(0)!r}")
    m = _BRANDS.search(text)
    if m:
        hard.append(f"brand: {m.group(0)!r}")
    m = _HEALTH.search(text)
    if m:
        hard.append(f"health: {m.group(0)!r}")
    m = _NOT_X.search(text)
    if m:
        hard.append(f"not-X-but-Y: {m.group(0)[:60]!r}")
    if not allow_md and _MD.search(text):
        hard.append("markdown")
    if re.search(r"[\[\]{}<>]", text):
        hard.append("brackets")
    if _GARBLED.search(text):
        hard.append("garbled characters")


def qa(doc_type: str, job: dict, out: dict | None) -> tuple[list, list]:
    """(hard, soft) for one document. job is a row of build_jobs()[doc_type] as a dict."""
    hard, soft = [], []
    if not out or out.get("error"):
        return ["no response: " + str((out or {}).get("error", ""))[:80]], soft
    fields = FIELDS[doc_type]
    if any(not str(out.get(f) or "").strip() for f in fields):
        return ["empty field"], soft
    text = "\n".join(str(out[f]) for f in fields)
    lo, hi, hlo, hhi = WORDS[doc_type]
    body = out["body"] if doc_type == "reviews" else text
    w = _words(body) if doc_type != "recipes" else _words(out["ingredients_text"]) + _words(out["instructions"])
    if not hlo <= w <= hhi:
        hard.append(f"{w} words")
    elif not lo <= w <= hi:
        soft.append(f"{w} words")
    _common(text, hard, allow_md=doc_type == "recipes")
    facts = job["facts"]
    stray = sorted(n for n in _nums(text) - _nums(facts) if n > 10 and not (doc_type == "recipes"))
    if doc_type == "reviews":
        if stray:
            hard.append(f"numbers not in facts: {[num(x) for x in stray[:4]]}")
        tw = _words(out["title"])
        if not 1 <= tw <= 12:
            hard.append(f"title {tw} words")
        for t in job["themes"]:
            if not re.search(THEME_RE[t], body + " " + out["title"], re.I):
                hard.append(f"theme missing: {t}")
        if "musty_smell" not in job["themes"] and MUSTY_RE.search(text):
            hard.append("musty wording without the musty theme")
        if "clumping" not in job["themes"] and re.search(r"clump|caked", text, re.I):
            hard.append("clumping wording without the theme")
        if "stale" not in job["themes"] and re.search(r"\bstale\b", text, re.I):
            hard.append("stale wording without the theme")
        elif "stale" not in job["themes"] and re.search(r"\b(bland|flat|weak|no flavor|lost its)\b", text, re.I):
            soft.append("staleness-like wording without the theme")
        if "leaking" not in job["themes"] and re.search(r"\bleak", text, re.I):
            soft.append("leak wording without the theme")
        if re.search(r"\b[1-5](?:\.0)? stars?\b|\bfive stars\b|\bone star\b", text, re.I):
            soft.append("mentions stars")
    elif doc_type == "product_descriptions":
        if stray:
            hard.append(f"numbers not in facts: {[num(x) for x in stray[:4]]}")
        if job["heat"] <= 1 and _overstated(text):
            hard.append(f"heat overstated: {_overstated(text)!r}")
        if job["heat"] >= 3 and not re.search(r"\b(hot|heat|fier\w*|spic\w*|burn\w*|kick)\b", text, re.I):
            hard.append("heat not mentioned")
        if job["smoky"] and not re.search(r"smok", text, re.I):
            hard.append("smoke not mentioned")
        core = fold(re.split(r",|\(", job["name"])[0]).strip()
        if core and core not in fold(text):
            soft.append("name not used")
    elif doc_type == "recipes":
        ing = fold(out["ingredients_text"])
        for n in job["names"]:
            if fold(n) not in ing:
                hard.append(f"product not named exactly: {n}")
        steps = [l for l in out["instructions"].splitlines() if re.match(r"\s*\d+[.)]\s", l)]
        if len(steps) < 3:
            hard.append(f"{len(steps)} numbered steps")
        if re.search(r"(?m)^\s*[-*•]\s", out["ingredients_text"]) or "**" in text or re.search(r"(?m)^#", text):
            hard.append("markdown")
        if re.search(r"cymbal", out["title"], re.I):
            soft.append("title names Cymbal")
        if len([l for l in out["ingredients_text"].splitlines() if l.strip()]) < len(job["names"]) + 1:
            soft.append("short ingredient list")
    elif doc_type == "supplier_quality_reports":
        if stray:
            hard.append(f"numbers not in facts: {[num(x) for x in stray[:4]]}")
        if isinstance(job["lot_code"], str) and job["lot_code"] not in text:
            hard.append("lot code missing")
        if job["result"] == "fail" and not re.search(r"fail|reject|out of spec|exceed|above (the )?spec|on hold", text, re.I):
            hard.append("fail result not stated")
        if job["result"] == "pass" and re.search(r"\b(fail(ed|s)?|rejected)\b", text, re.I):
            hard.append("pass report says failed")
        if job["x_plant"] == "failed_lot_retest" and not MUSTY_RE.search(text):
            hard.append("retest does not mention the musty complaints")
        if job["x_plant"] != "failed_lot_retest" and MUSTY_RE.search(text):
            hard.append("musty wording outside the failed lot")
    elif doc_type == "account_notes":
        if stray:
            hard.append(f"numbers not in facts: {[num(x) for x in stray[:4]]}")
        if job["topic"] == "musty_paprika" and not MUSTY_RE.search(text):
            hard.append("musty complaint not stated")
        if job["topic"] != "musty_paprika" and MUSTY_RE.search(text):
            hard.append("musty wording outside the failed lot")
        if job["topic"] == "substitution" and not re.search(r"korintje|substitut|swap|sub(bed)?\b", text, re.I):
            hard.append("substitution not stated")
        pref = job.get("preference")
        if isinstance(pref, str) and pref:
            key = re.findall(r"Saigon|Ceylon|Chili Powder|smoked paprika", pref, re.I)
            if key and not re.search(re.escape(key[0]), text, re.I):
                hard.append(f"preference missing: {key[0]}")
    return hard, soft


_FIX = {
    "plumbing": "Never mention facts, notes, prompts, data or JSON in the text.",
    "brand": "Do not name any real company, brand, store, website, app or carrier.",
    "health": "Make no health, nutrition or medical claims, and avoid the words healthy and wellness.",
    "not-X-but-Y": "Do not use 'it's not X, it's Y' or 'not just X, but Y'.",
    "markdown": "Plain text only: no markdown, bullets or headings.",
    "brackets": "Do not use brackets, braces or angle brackets.",
    "garbled characters": "Spell every word correctly.",
    "numbers not in facts": "Use no number that is not in the facts; write small counts as words.",
    "heat overstated": "This product has little or no heat: do not call it fiery, very hot or burning.",
    "heat not mentioned": "Say plainly how hot it is.",
    "smoke not mentioned": "Say that it is smoky.",
    "product not named exactly": "In ingredients_text, name every listed Cymbal Foods product exactly as written in the list.",
    "theme missing": "Make every point under 'Must say' come through clearly.",
    "musty wording without the musty theme": "Do not mention any musty, damp or mildew smell.",
    "clumping wording without the theme": "Do not mention clumping or caking.",
    "stale wording without the theme": "Do not call it stale.",
    "musty wording outside the failed lot": "Do not mention any musty, damp or mildew smell.",
    "lot code missing": "Include the lot code exactly as written.",
    "fail result not stated": "State plainly that the lot failed and what was done.",
    "pass report says failed": "This lot passed: do not say it failed or was rejected.",
    "retest does not mention the musty complaints": "Say why it was retested: customers reported a musty smell.",
    "musty complaint not stated": "Say that the chef reported a musty smell.",
    "substitution not stated": "Say which item was substituted for which.",
    "preference missing": "Record the chef's preference in their words.",
    "empty field": "Fill in every field.",
}


def repair_note(doc_type: str, hard: list) -> str:
    """Tell the model what QA rejected, so a repair is a correction and not just a reroll."""
    lo, hi = WORDS[doc_type][:2]
    fixes = []
    for h in hard:
        key = h.split(":")[0]
        if key.endswith("words") and not key.startswith("title"):
            fixes.append(f"Keep it between {lo} and {hi} words (the last draft was {key}).")
        elif key.startswith("title"):
            fixes.append("The title is 2 to 8 words.")
        elif key.endswith("numbered steps"):
            fixes.append("Write the method as numbered steps, one per line: '1. ...'.")
        else:
            fixes.append(_FIX.get(key, f"Avoid this problem: {h[:100]}."))
    return ("\n\nA previous draft was rejected. Write a new one that keeps every rule and fixes this:\n- "
            + "\n- ".join(dict.fromkeys(fixes)))


# ==================================================================================================
# The 25-per-type sample: chosen to cover the planted signals and the cases most likely to go wrong
# ==================================================================================================
def pick_sample(doc_type: str, jobs: pd.DataFrame, n: int = SAMPLE_N, seed: int = 2026) -> list[str]:
    picks: list[str] = []

    def take(df: pd.DataFrame, k: int):
        for jid in df.sample(frac=1, random_state=seed).job_id.tolist() if len(df) else []:
            if k <= 0:
                break
            if jid not in picks:
                picks.append(jid); k -= 1

    J = jobs
    if doc_type == "product_descriptions":
        take(J[J.name.str.contains(r"Smoked Paprika \((?:Sweet|Hot)\), Ground$", regex=True)], 2)
        take(J[J.name.str.contains("Chipotle Meco|Ghost Pepper", regex=True)], 2)
        take(J[J.name.str.contains(r"^(?:Saigon|Ceylon|Korintje) Cinnamon, Ground$", regex=True)], 3)
        take(J[J.ambiguity_group == "chile_powder"], 1)
        take(J[J.product_type == "blend"], 4)
        take(J[J.product_type == "gift_set"], 2)
        for c in ("OIL", "TEA", "SAUCE", "BAKING", "COFFEE", "SALT"):
            take(J[J.category_id == c], 1)
        take(J[J.name.str.startswith("Organic")], 1)
    elif doc_type == "reviews":
        has = lambda t: J.themes.map(lambda x: t in x)
        take(J[has("musty_smell")], 3)
        take(J[has("wrong_cinnamon")], 2)
        for t in ("clumping", "stale", "too_hot", "leaking", "great_value"):
            take(J[has(t)], 2)
        take(J[J.themes.map(len) >= 2], 2)
        take(J[(J.themes.map(len) == 0) & (J.rating == 5)], 3)
        take(J[(J.themes.map(len) == 0) & (J.rating <= 2)], 2)
        take(J[J.channel == "store"], 1)
    elif doc_type == "recipes":
        for c in ("baking", "indian", "mexican", "bbq", "italian", "tea", "thai", "middle_eastern", "japanese", "cajun"):
            take(J[J.cuisine == c], 1)
        for c in ("dessert", "drink", "sauce", "breakfast", "soup", "snack"):
            take(J[J.course == c], 1)
        take(J[J.names.map(len) >= 5], 2)
        take(J[J.servings >= 12], 1)
        take(J[J.minutes <= 15], 1)
    elif doc_type == "supplier_quality_reports":
        take(J[J.x_plant.notna()], 2)
        take(J[(J.result == "fail") & J.x_plant.isna()], 3)
        take(J[(J.result == "conditional") & (J.report_type == "receiving_inspection")], 4)
        take(J[(J.report_type == "supplier_audit") & (J.result == "conditional")], 2)
        take(J[(J.report_type == "supplier_audit") & (J.result == "pass")], 2)
        take(J[J.report_type == "coa_review"], 3)
        take(J[(J.report_type == "receiving_inspection") & (J.result == "pass")], 4)
    elif doc_type == "account_notes":
        take(J[J.topic == "musty_paprika"], 3)
        take(J[J.topic == "substitution"], 3)
        take(J[J.preference.fillna("").str.contains("Saigon|Ceylon|Chili Powder|smoked paprika", regex=True)], 4)
        take(J[J.topic == "preference"], 1)
        for t in ("contract_renewal", "price_question", "delivery_issue", "clumping", "new_menu", "seasonal_planning", "general_checkin"):
            take(J[J.topic == t], 1)
        for t in ("visit", "email"):
            take(J[J.note_type == t], 1)
    take(J, n - len(picks))
    return picks[:n]


# ==================================================================================================
# Assembly into the final tables (schemas.py, stage 2)
# ==================================================================================================
def assemble(doc_type: str, T: dict, outputs: dict, embeddings: dict | None) -> dict[str, pd.DataFrame]:
    """outputs: {job_id: {field: text}}; embeddings: {job_id: float32 array} or None (columns left out)."""
    F = T[FACT_TABLES[doc_type]]
    idc = ID_COL[doc_type]
    F = F[F[idc].isin(outputs)].copy()
    get = lambda f: F[idc].map(lambda j: outputs[j][f])
    emb = (lambda: F[idc].map(lambda j: embeddings[j])) if embeddings is not None else None
    if doc_type == "product_descriptions":
        D = pd.DataFrame({"product_id": F.product_id, "description": get("description")})
        cols = {"description_embedding": emb}
        out = {"product_descriptions": D}
    elif doc_type == "reviews":
        D = F[["review_id", "product_id", "sku_id", "customer_id", "order_id", "line_no", "rating"]].copy()
        D["title"], D["body"] = get("title"), get("body")
        D["submitted_at"] = pd.to_datetime(F.submitted_at, utc=True)
        D["verified_purchase"], D["helpful_votes"] = F.verified_purchase.astype(bool), F.helpful_votes.astype(int)
        cols = {"body_embedding": emb}
        out = {"reviews": D}
    elif doc_type == "recipes":
        D = F[["recipe_id"]].copy()
        D["title"] = get("title")
        P = T["products"].set_index("product_id")
        D["cuisine"] = F.cuisine.map(cuisine)
        D["course"] = [recipe_course(c, _list(p), P) for c, p in zip(F.course, F.product_ids)]
        D["servings"], D["total_minutes"] = F.servings.astype(int), F.total_minutes.astype(int)
        D["ingredients_text"], D["instructions"] = get("ingredients_text"), get("instructions")
        cols = {"recipe_embedding": emb}
        RP = T["recipe_product_facts"]
        out = {"recipes": D, "recipe_products": RP[RP.recipe_id.isin(D.recipe_id)].reset_index(drop=True)}
    elif doc_type == "supplier_quality_reports":
        D = F[["report_id", "lot_id", "supplier_id", "report_type", "inspected_at", "result", "inspector_staff_id"]].copy()
        D["inspected_at"] = pd.to_datetime(D.inspected_at, utc=True)
        D["lot_id"] = D.lot_id.where(D.lot_id.notna(), None)
        D["report_text"] = get("report_text")
        cols = {"report_embedding": emb}
        out = {"supplier_quality_reports": D}
    else:
        D = F[["note_id", "account_id", "staff_id", "noted_at", "note_type"]].copy()
        D["note_type"] = [note_type_for(tp, nt) for tp, nt in zip(F.topic, F.note_type)]
        D["noted_at"] = pd.to_datetime(D.noted_at, utc=True)
        D["note_text"] = get("note_text")
        cols = {"note_embedding": emb}
        out = {"account_notes": D}
    for c, f in cols.items():
        if f is not None:
            D[c] = f()
    for k in out:
        out[k] = out[k].reset_index(drop=True)
    return out
