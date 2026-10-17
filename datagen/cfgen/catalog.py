"""Catalog: ingredients (what lots are bought as), products, blend recipes, gift sets, SKUs and prices."""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

from . import config as C
from .content import pairs, split
from .util import pretty_price, rng, stable_hash

CATEGORY_ORDER = ["SPICE", "HERB", "CHILE", "PEPPER", "SALT", "AROMATIC", "BLEND", "OIL", "VINEGAR", "SAUCE",
                  "CONDIMENT", "TEA", "COFFEE", "BAKING", "GIFT"]
CATEGORIES = [
    ("SPICE", "Spices", "Spices & Blends"), ("HERB", "Herbs", "Spices & Blends"), ("CHILE", "Chiles", "Spices & Blends"),
    ("PEPPER", "Peppercorns", "Spices & Blends"), ("SALT", "Salts", "Spices & Blends"),
    ("AROMATIC", "Garlic, Onion and Aromatics", "Spices & Blends"), ("BLEND", "House Blends", "Spices & Blends"),
    ("OIL", "Oils", "Pantry"), ("VINEGAR", "Vinegars", "Pantry"), ("SAUCE", "Sauces", "Pantry"),
    ("CONDIMENT", "Condiments", "Pantry"), ("TEA", "Tea", "Tea & Coffee"), ("COFFEE", "Coffee", "Tea & Coffee"),
    ("BAKING", "Baking", "Baking"), ("GIFT", "Gift Sets", "Gifts"),
]
TYPE_TO_CATEGORY = {"chile": "CHILE", "peppercorn": "PEPPER", "herb": "HERB", "salt": "SALT", "aromatic": "AROMATIC"}
UOM = [("oz", "lb", 1 / 16), ("lb", "oz", 16), ("half_lb", "oz", 8), ("half_lb", "lb", 0.5), ("5lb", "oz", 80),
       ("5lb", "lb", 5), ("25lb", "oz", 400), ("25lb", "lb", 25), ("g", "oz", 0.035274), ("oz", "g", 28.349523),
       ("kg", "lb", 2.204623), ("lb", "kg", 0.453592), ("fl_oz", "ml", 29.573530), ("ml", "fl_oz", 0.033814),
       ("gal", "fl_oz", 128), ("fl_oz", "gal", 1 / 128), ("ct", "ct", 1), ("oz", "oz", 1), ("lb", "lb", 1),
       ("kg", "oz", 35.273962)]
FORM_LABEL = {"toasted ground": "Toasted, Ground", "on the stem": "On the Stem", "cut": "Cut and Sifted",
              "decorticated": "Decorticated Seeds"}
SESAME = {"sesame_white", "sesame_black", "tahini", "toasted_sesame_oil", "furikake_blend", "sesame_seasoning",
          "dukkah_sesame", "zaatar", "everything_bagel", "shichimi_togarashi", "korean_bbq_rub", "sichuan_mala"}


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _variant_origin(v: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", v)
    if m and m.group(2)[0].isupper() and " " not in m.group(2).replace("United States", "X").replace(
            "South Korea", "X").replace("Sri Lanka", "X").replace("French Polynesia", "X").replace(
            "Costa Rica", "X").replace("Ivory Coast", "X"):
        return m.group(1).strip(), m.group(2).strip()
    return v.strip(), None


def build(c: dict) -> dict[str, pd.DataFrame]:
    r = rng("catalog")
    spices = {s["slug"]: s for s in c["spices"]}
    pantry = {p["slug"]: p for p in c["pantry"]}
    blends = {b["slug"]: b for b in c["blends"]}

    # ---- ingredients: spices plus one supply item per pantry product ------------------------------------
    ing = []
    for s in c["spices"]:
        sc = s["scoville"].split("-") if s["scoville"] else []
        ing.append(dict(ingredient_id=s["slug"], name=s["name"], ingredient_type=s["ingredient_type"],
                        botanical_name=s["botanical_name"] or None, ambiguity_group=s["ambiguity_group"] or None,
                        aliases=split(s["aliases"]), heat_level=int(s["heat"]),
                        scoville_min=int(sc[0]) if len(sc) == 2 else None,
                        scoville_max=int(sc[1]) if len(sc) == 2 else None,
                        flavor_notes=split(s["flavor_notes"]), primary_origins=split(s["origins"]),
                        x_price_band=int(s["price_band"]), x_popularity=int(s["popularity"])))
    pantry_items = {}   # pantry slug -> list of (supply id, variant label, origin)
    for p in c["pantry"]:
        vs = split(p["variants"]) or [""]
        items = []
        for v in vs:
            label, origin = _variant_origin(v) if v else ("", None)
            if v and origin is None:
                origin = next((o for o in split(p["origins"]) if o.split()[0] in label), None)
            sid = p["slug"] if not v else f"{p['slug']}__{_slugify(label)}"
            origins = [origin] if origin else split(p["origins"])
            items.append((sid, label, origins[0]))
            ing.append(dict(ingredient_id=sid, name=f"{p['name']}, {label}" if label else p["name"],
                            ingredient_type=p["category"].lower(), botanical_name=None, ambiguity_group=None,
                            aliases=[], heat_level=int(p["heat"]), scoville_min=None, scoville_max=None,
                            flavor_notes=split(p["flavor_notes"]), primary_origins=origins,
                            x_price_band=int(p["price_band"]), x_popularity=int(p["popularity"])))
        pantry_items[p["slug"]] = items
    ingredients = pd.DataFrame(ing)

    def comp_ref(slug: str) -> str:
        return slug if slug in spices else pantry_items[slug][0][0]

    # ---- products -------------------------------------------------------------------------------------
    prods = []

    def add(**kw):
        prods.append(kw)

    for s in c["spices"]:
        forms = split(s["forms"])
        origins = split(s["origins"]) if s["origin_variants"] == "Y" else [None]
        cat = TYPE_TO_CATEGORY.get(s["ingredient_type"], "SPICE")
        pop = int(s["popularity"])
        for fi, form in enumerate(forms):
            for oi, origin in enumerate(origins):
                label = FORM_LABEL.get(form, form.title())
                nm = f"{s['name']}, {label}" + (f" ({origin})" if origin else "")
                add(name=nm, category_id=cat, product_type="single_origin", ingredient_id=s["slug"], form=form,
                    origin_country=origin or split(s["origins"])[0], is_organic=False, is_salt_free=s["ingredient_type"] != "salt",
                    heat_level=int(s["heat"]), flavor_tags=split(s["flavor_notes"]), cuisine_tags=split(s["cuisine_tags"]),
                    seasonality_profile=s["seasonality"], ambiguity_group=s["ambiguity_group"] or None,
                    x_slug=s["slug"], x_variant=f"{form}|{origin or ''}", x_pop=max(1, pop - (1 if fi >= 2 else 0) - (1 if oi >= 1 else 0)),
                    x_band=int(s["price_band"]), x_family=s["pack_family"], x_density=s["density"],
                    x_recipe=[(s["slug"], 100.0)], x_batch="pack", x_base=fi == 0 and oi == 0)
        if s["organic"] == "Y":
            for form in forms[:2]:
                label = FORM_LABEL.get(form, form.title())
                add(name=f"Organic {s['name']}, {label}", category_id=cat, product_type="single_origin",
                    ingredient_id=s["slug"], form=form, origin_country=split(s["origins"])[0], is_organic=True,
                    is_salt_free=s["ingredient_type"] != "salt", heat_level=int(s["heat"]),
                    flavor_tags=split(s["flavor_notes"]), cuisine_tags=split(s["cuisine_tags"]),
                    seasonality_profile=s["seasonality"], ambiguity_group=s["ambiguity_group"] or None,
                    x_slug=s["slug"], x_variant=f"organic|{form}", x_pop=max(1, pop - 1), x_band=min(5, int(s["price_band"]) + 1),
                    x_family=s["pack_family"], x_density=s["density"], x_recipe=[(s["slug"], 100.0)], x_batch="pack", x_base=False)

    for b in c["blends"]:
        base = [(comp_ref(k), v) for k, v in pairs(b["components"])]
        style = b["style"]
        dens = "dense" if style in ("salt_blend", "whole_spice") else "light" if style == "herb_blend" else "medium"
        heat = int(b["heat"])
        has_salt = any(spices.get(k, {}).get("ingredient_type") == "salt" for k, _ in pairs(b["components"]))
        variants = [("", base, heat, b["salt_free"] == "Y")]
        for v in split(b["variants"]):
            if v == "salt_free" and has_salt:
                rec = [(k, p) for k, p in base if spices.get(k, {}).get("ingredient_type") != "salt"]
                variants.append(("salt_free", rec, heat, True))
            elif v == "hot":
                rec = [(k, p * 0.92) for k, p in base]
                rec = [(k, p) for k, p in rec if k != "cayenne"] + [("cayenne", 8.0 + sum(p for k, p in base if k == "cayenne") * 0.92)]
                variants.append(("hot", rec, min(5, heat + 2), b["salt_free"] == "Y"))
            elif v == "mild":
                rec = [(k, p * (0.5 if spices.get(k, {}).get("ingredient_type") == "chile" and int(spices[k]["heat"]) >= 3 else 1))
                       for k, p in base]
                variants.append(("mild", rec, max(0, heat - 2), b["salt_free"] == "Y"))
            elif v == "organic":
                variants.append(("organic", base, heat, b["salt_free"] == "Y"))
        for vi, (v, rec, h, salt_free) in enumerate(variants):
            tot = sum(p for _, p in rec)
            rec = [(k, round(p * 100 / tot, 2)) for k, p in rec]
            nm = {"": b["name"], "salt_free": f"{b['name']} (Salt-Free)", "hot": f"Hot {b['name']}",
                  "mild": f"Mild {b['name']}", "organic": f"Organic {b['name']}"}[v]
            add(name=nm, category_id="BLEND", product_type="blend", ingredient_id=None, form="blend", origin_country=None,
                is_organic=v == "organic", is_salt_free=salt_free, heat_level=h, flavor_tags=split(b["flavor_notes"]),
                cuisine_tags=split(b["cuisine_tags"]), seasonality_profile=b["seasonality"],
                ambiguity_group=b["ambiguity_group"] or None, x_slug=b["slug"], x_variant=v,
                x_pop=max(1, int(b["popularity"]) - (1 if vi else 0)), x_band=2, x_family="std", x_density=dens,
                x_recipe=rec, x_batch="blend", x_base=vi == 0)

    for p in c["pantry"]:
        for vi, (sid, label, origin) in enumerate(pantry_items[p["slug"]]):
            add(name=f"{p['name']}, {label}" if label else p["name"], category_id=p["category"], product_type="pantry",
                ingredient_id=sid, form=p["subtype"], origin_country=origin, is_organic="Organic" in label,
                is_salt_free=True, heat_level=int(p["heat"]), flavor_tags=split(p["flavor_notes"]),
                cuisine_tags=split(p["cuisine_tags"]), seasonality_profile=p["seasonality"], ambiguity_group=None,
                x_slug=p["slug"], x_variant=label, x_pop=max(1, int(p["popularity"]) - (1 if vi >= 3 else 0)),
                x_band=int(p["price_band"]), x_family=p["pack_family"], x_density="",
                x_recipe=[(sid, 100.0)], x_batch="pack" if p["sourcing"] == "bulk_packed" else "receive_finished",
                x_base=vi == 0)

    for g in c["gift_sets"]:
        eds = ["evergreen"] if g["editions"] == "evergreen" else split(g["editions"])
        for e in eds:
            add(name=g["name"] if e == "evergreen" else f"{g['name']} {e}", category_id="GIFT", product_type="gift_set",
                ingredient_id=None, form="gift set", origin_country=None, is_organic=False, is_salt_free=False,
                heat_level=0, flavor_tags=[], cuisine_tags=[], seasonality_profile=g["seasonality"], ambiguity_group=None,
                x_slug=g["slug"], x_variant=e, x_pop=4 if e != "evergreen" else 3, x_band=int(g["price_band"]),
                x_family="gift", x_density="", x_recipe=[], x_batch="kit", x_base=e in ("evergreen", eds[-1]))

    P = pd.DataFrame(prods)
    P["x_cat_order"] = P["category_id"].map({k: i for i, k in enumerate(CATEGORY_ORDER)})
    P = P.sort_values(["x_cat_order", "name", "x_variant"], kind="stable").reset_index(drop=True)
    P["product_id"] = [f"P{10001 + i}" for i in range(len(P))]

    # allergens
    def allergens(row) -> list[str]:
        slugs = {k.split("__")[0] for k, _ in row["x_recipe"]} | {row["x_slug"]}
        a = []
        if slugs & SESAME:
            a.append("sesame")
        if any(s.startswith("mustard") or s in ("hot_mustard_powder", "wasabi_style_powder") for s in slugs):
            a.append("mustard")
        if row["x_slug"] in ("soy_sauce", "asian_sauces"):
            a.append("soy")
        if row["x_slug"] in ("malt_vinegar", "asian_sauces") or (row["x_slug"] == "soy_sauce" and "Tamari" not in row["x_variant"]):
            a.append("wheat")
        if row["x_slug"] == "fish_sauce":
            a.append("fish")
        return a

    P["allergens"] = P.apply(allergens, axis=1)

    # launch / discontinue
    launched, disc = [], []
    for row in P.itertuples():
        if row.product_type == "gift_set" and row.x_variant != "evergreen":
            y = int(row.x_variant)
            launched.append(dt.date(y, 10, 1))
            disc.append(dt.date(y + 1, 1, 15) if y < 2026 else None)
        else:
            h = int(stable_hash(row.name, 6), 16)
            launched.append(dt.date(1985 + h % 38, 1 + h % 12, 1) if not row.name.startswith("Organic") else dt.date(2012 + h % 10, 3, 1))
            disc.append(None)
    P["launched_on"] = launched
    P["discontinued_on"] = disc

    # blend components and gift items
    bc = [(row.product_id, k, round(p, 2)) for row in P.itertuples() if row.product_type == "blend" for k, p in row.x_recipe]
    blend_components = pd.DataFrame(bc, columns=["product_id", "ingredient_id", "pct"])
    blend_components = blend_components.groupby(["product_id", "ingredient_id"], as_index=False)["pct"].sum()

    base_by_slug = {}
    for row in P[P["x_base"]].itertuples():
        base_by_slug.setdefault(row.x_slug, row.product_id)
    gsi = []
    gifts = {g["slug"]: g for g in c["gift_sets"]}
    for row in P[P.product_type == "gift_set"].itertuples():
        for slug, q in pairs(gifts[row.x_slug]["contents"]):
            gsi.append((row.product_id, base_by_slug[slug], int(q)))
    gift_set_items = pd.DataFrame(gsi, columns=["gift_product_id", "product_id", "quantity"]).groupby(
        ["gift_product_id", "product_id"], as_index=False)["quantity"].sum()
    # a gift set is as hot as its hottest item (the Heat Seeker's Collection is not a mild product)
    heat_of = P.set_index("product_id").heat_level
    gift_heat = gift_set_items.assign(h=gift_set_items.product_id.map(heat_of)).groupby("gift_product_id").h.max()
    gm = P.product_type == "gift_set"
    P.loc[gm, "heat_level"] = P.loc[gm, "product_id"].map(gift_heat).fillna(0).astype(int).to_numpy()

    # ---- SKUs -------------------------------------------------------------------------------------------
    packs = pd.DataFrame(c["packaging"])
    packs["min_popularity"] = packs["min_popularity"].astype(int)
    skus = []
    for row in P.itertuples():
        fam = row.x_family
        # salts pack like the standard spice line (packaging.csv has no salt family; without this no salt had a SKU)
        pfam = "std" if fam == "salt" else fam
        dens = row.x_density if pfam == "std" else ("light" if fam in ("saffron", "vanilla") else "")
        cand = packs[(packs.pack_family == pfam) & (packs.density == dens) & (packs.min_popularity <= row.x_pop)]
        for pk in cand.itertuples():
            ch = split(pk.channels)
            net_oz = float(pk.net_oz) if pk.net_oz else (float(pk.net_qty) if pk.net_uom == "fl_oz" else float(pk.net_qty))
            skus.append(dict(sku_id=f"CF-{row.product_id[1:]}-{pk.pack_code}", product_id=row.product_id,
                             pack_code=pk.pack_code, pack_description=pk.description, container=pk.container,
                             net_qty=float(pk.net_qty), net_uom=pk.net_uom, net_oz=round(net_oz, 3),
                             sold_in_stores="store" in ch, sold_online="online" in ch, sold_wholesale="wholesale" in ch,
                             x_band=row.x_band, x_family=fam, x_pop=row.x_pop, x_shelf=int(pk.shelf_life_months)))
    S = pd.DataFrame(skus)

    # UPC: '2' (in-store range) + 10 digits + check digit
    def upc(s: str) -> str:
        body = "2" + str(int(stable_hash(s, 12), 16) % 10**10).zfill(10)
        d = [int(x) for x in body]
        chk = (10 - ((sum(d[0::2]) * 3 + sum(d[1::2])) % 10)) % 10
        return body + str(chk)

    S["upc"] = S["sku_id"].map(upc)

    # ---- prices (2026 level), then history ------------------------------------------------------------
    jit = np.exp(r.normal(0, 0.06, len(P)))
    pj = dict(zip(P.product_id, jit))
    gift_n = gift_set_items.groupby("gift_product_id")["quantity"].sum().to_dict()
    retail = []
    for s in S.itertuples():
        band = s.x_band
        if s.x_family == "saffron":
            grams = s.net_oz * 28.349523
            base = C.PER_UNIT_SPECIAL["saffron_per_g"] * grams * (grams / 0.5) ** -0.10
        elif s.x_family == "vanilla":
            beans = s.net_qty if s.net_uom == "ct" else s.net_qty * 150
            base = C.PER_UNIT_SPECIAL["vanilla_per_bean"] * beans * (beans / 2) ** -0.10
        elif s.net_uom == "fl_oz" and s.x_family != "gift":
            base = C.PER_UNIT_SPECIAL["liquid_per_floz"][band] * s.net_qty * (s.net_qty / 8.5) ** -0.15
        elif s.x_family == "gift":
            base = C.PER_UNIT_SPECIAL["gift_base"][max(2, band)] * (1 + 0.04 * gift_n.get(s.product_id, 3)) * (6 * 0.8 if s.pack_code == "GIFTW" else 1)
        else:
            base = C.RETAIL_PER_OZ_BY_BAND[band] * s.net_oz * (s.net_oz / 2.5) ** C.SIZE_EXPONENT
            if s.container == "jar":
                base *= C.JAR_PREMIUM
        retail.append(base * pj[s.product_id])
    retail = np.array(retail)
    S["list_price"] = pretty_price(retail)
    S["wholesale_price"] = np.round(retail * C.WHOLESALE_FACTOR, 2)
    S["unit_cost"] = np.round(S["wholesale_price"].to_numpy() * C.COST_FACTOR * np.exp(r.normal(0, 0.04, len(S))), 4)
    S["status"] = "active"
    prod_launch = dict(zip(P.product_id, P.launched_on))
    prod_disc = dict(zip(P.product_id, P.discontinued_on))
    S["introduced_on"] = [prod_launch[p] for p in S.product_id]
    S.loc[S.product_id.map(lambda p: prod_disc[p] is not None), "status"] = "discontinued"

    # price periods: every Jan 1 plus the supply events. Current (2026) prices are walked backward.
    groups = dict(zip(ingredients.ingredient_id, ingredients["ambiguity_group"]))
    prod_ing = dict(zip(P.product_id, P.ingredient_id))
    sku_group = np.array([groups.get(prod_ing[p]) if prod_ing[p] else None for p in S.product_id], dtype=object)
    periods = sorted({C.START, dt.date(2025, 1, 1), dt.date(2026, 1, 1)} | {d for _, d, _, _ in C.SUPPLY_PRICE_EVENTS})
    inc25 = r.uniform(*C.ANNUAL_INCREASE, len(S))
    inc26 = r.uniform(*C.ANNUAL_INCREASE, len(S))
    intro = S["introduced_on"].to_numpy()
    hist, mats = [], {}
    for ch, cur in (("retail", S["list_price"].to_numpy(dtype=float)), ("wholesale", S["wholesale_price"].to_numpy(dtype=float))):
        rp = pretty_price if ch == "retail" else (lambda x: np.round(x, 2))
        m = np.zeros((len(S), len(periods)))
        val = cur / (1 + inc26)                               # price during late 2025
        for j in range(len(periods) - 1, -1, -1):
            d = periods[j]
            if d == dt.date(2026, 1, 1):
                m[:, j] = cur
                continue
            m[:, j] = rp(val)
            ev = [(g, inc) for g, dd, inc, _ in C.SUPPLY_PRICE_EVENTS if dd == d]
            for g, inc in ev:
                val = np.where(sku_group == g, val / (1 + inc), val)
            if d == dt.date(2025, 1, 1):
                val = val / (1 + inc25)
        mats[ch] = m
        reasons = {dt.date(2025, 1, 1): "annual increase", dt.date(2026, 1, 1): "annual increase"}
        reasons.update({d: rs for _, d, _, rs in C.SUPPLY_PRICE_EVENTS})
        for i, sku in enumerate(S["sku_id"].to_numpy()):
            prev = None
            for j, d in enumerate(periods):
                nxt = periods[j + 1] if j + 1 < len(periods) else None
                if nxt is not None and intro[i] >= nxt:
                    continue                                   # not on sale yet in this period
                if prev is None:
                    hist.append((sku, ch, max(d, intro[i]), float(m[i, j]), "list price"))
                elif m[i, j] != prev:
                    hist.append((sku, ch, d, float(m[i, j]), reasons[d]))
                prev = m[i, j]
    price_history = pd.DataFrame(hist, columns=["sku_id", "channel", "effective_from", "price", "change_reason"])

    categories = pd.DataFrame(CATEGORIES, columns=["category_id", "name", "department"])
    uom = pd.DataFrame(UOM, columns=["from_uom", "to_uom", "factor"]).drop_duplicates(["from_uom", "to_uom"])
    uom["factor"] = uom["factor"].round(6)

    return dict(ingredients=ingredients, products=P, blend_components=blend_components, gift_set_items=gift_set_items,
                skus=S, price_history=price_history, categories=categories, uom_conversions=uom,
                price_periods=periods, price_matrix=mats)
