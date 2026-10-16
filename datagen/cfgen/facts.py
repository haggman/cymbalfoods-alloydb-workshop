"""Fact blocks for the Stage 2 text corpus. Gemini writes prose from these and nothing else.

Every planted text signal (review themes, the clumping pattern, the failed lot's musty complaints, the
'Saigon, not Ceylon' preferences) is decided here, in data, so it can be validated before any text exists.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .supply import DCS
from .util import rng, utc64, uuids, weighted_choice

GROUND_FORMS = {"ground", "powder", "granulated", "fine", "toasted ground", "blend", "flakes", "crushed"}
LIQUID_FAMILIES = {"oil", "vinegar", "sauce", "syrup", "extract"}


def _cap(n: float) -> int:
    return int(min(round(n * C.SCALE), round(n * 5)))      # the corpus stops growing at 5x lab scale


def reviews(cat: dict, consumers: pd.DataFrame, O: pd.DataFrame, Lx: pd.DataFrame, sup: dict) -> pd.DataFrame:
    r = rng("facts", "reviews")
    P, S, B = cat["products"], cat["skus"].reset_index(drop=True), sup["batches"]
    n = _cap(C.N_REVIEWS)
    ok = (Lx.line_status.isin(["fulfilled", "substituted"]) & Lx.x_channel.isin(["online", "store"])
          & (O.x_cust.to_numpy()[Lx.x_order.to_numpy()] >= 0)).to_numpy()
    cand = np.nonzero(ok)[0]
    w = np.where(Lx.x_channel.to_numpy()[cand] == "online", 1.0, 0.2)
    w = w * np.where(np.isin(Lx.x_batch.to_numpy()[cand], sup["hold_batches"]), 25.0, 1.0)   # unhappy customers write reviews
    pick = np.sort(cand[weighted_choice(r, w, int(n * 1.1))])
    pick = np.unique(pick)
    R = Lx.iloc[pick][["x_order", "x_sku", "line_no", "order_id", "sku_id", "batch_id", "x_batch", "x_channel"]].copy()
    R["ordered_at"] = O.ordered_at.iloc[R.x_order.to_numpy()].set_axis(R.index)
    R["submitted_at"] = R.ordered_at + pd.to_timedelta(r.integers(4 * 24, 40 * 24, len(R)), "h") + pd.to_timedelta(r.integers(0, 3600, len(R)), "s")
    R = R[R.submitted_at < pd.Timestamp(C.NOW_UTC)]
    R = R.iloc[np.sort(r.choice(len(R), size=min(n, len(R)), replace=False))].copy()
    pidx = S.product_id.to_numpy()[R.x_sku]
    Pi = P.set_index("product_id")
    form = Pi.loc[pidx, "form"].to_numpy()
    heat = Pi.loc[pidx, "heat_level"].to_numpy()
    fam = S.x_family.to_numpy()[R.x_sku]
    pack = S.pack_code.to_numpy()[R.x_sku]
    amb = Pi.loc[pidx, "ambiguity_group"].to_numpy()
    cat_id = Pi.loc[pidx, "category_id"].to_numpy()
    bdc = np.where(R.x_batch.to_numpy() >= 0, B.dc_id.to_numpy()[np.maximum(R.x_batch.to_numpy(), 0)], None)
    bmon = np.where(R.x_batch.to_numpy() >= 0, B.produced_at.dt.month.to_numpy()[np.maximum(R.x_batch.to_numpy(), 0)], 0)
    bage = np.where(R.x_batch.to_numpy() >= 0,
                    (utc64(R.ordered_at) - utc64(B.produced_at)[np.maximum(R.x_batch.to_numpy(), 0)]) / np.timedelta64(1, "D"), 0)
    held = np.isin(R.x_batch.to_numpy(), sup["hold_batches"])
    m = len(R)
    T = C.REVIEW_THEMES
    ground = np.isin(form, list(GROUND_FORMS)) | (cat_id == "SALT")
    clump_lift = np.where((bdc == C.PLANT["clumping_dc"]) & np.isin(bmon, C.PLANT["clumping_months"]), C.PLANT["clumping_lift"], 1.0)
    p_clump = np.where(ground, T["clumping"] / max(ground.mean(), 1e-9) * 0.75, 0) * clump_lift
    p_stale = T["stale"] * np.where(bage > 240, 3.0, 0.7)
    p_hot = np.where(heat >= 3, T["too_hot"] / max((heat >= 3).mean(), 1e-9) * 0.8, np.where(heat == 2, 0.01, 0))
    p_value = T["great_value"] * np.where(np.isin(pack, ["B16", "B08", "B80", "K32", "D16", "O500", "V10"]), 2.0, 0.85)
    p_cin = np.where(amb == "cinnamon", 0.06, 0)
    p_leak = np.where(np.isin(fam, list(LIQUID_FAMILIES)), T["leaking"] / max(np.isin(fam, list(LIQUID_FAMILIES)).mean(), 1e-9), 0)
    themes = []
    u = r.random((m, 7))
    for i in range(m):
        t = []
        if u[i, 0] < p_clump[i]:
            t.append("clumping")
        if u[i, 1] < p_stale[i]:
            t.append("stale")
        if u[i, 2] < p_hot[i]:
            t.append("too_hot")
        if u[i, 3] < p_value[i]:
            t.append("great_value")
        if u[i, 4] < p_cin[i]:
            t.append("wrong_cinnamon")
        if u[i, 5] < p_leak[i]:
            t.append("leaking")
        if held[i] and u[i, 6] < 0.5:
            t.append("musty_smell")
        themes.append(t)
    neg = np.array([any(x in ("clumping", "stale", "leaking", "musty_smell", "wrong_cinnamon") for x in t) for t in themes])
    hot = np.array(["too_hot" in t for t in themes])
    val = np.array(["great_value" in t for t in themes])
    base = np.array([5, 4, 3, 2, 1])[weighted_choice(r, np.array([.55, .22, .10, .06, .07]), m)]
    rating = np.where(neg, np.array([1, 2, 3])[weighted_choice(r, np.array([.45, .4, .15]), m)],
                      np.where(hot, np.array([2, 3, 4, 5])[weighted_choice(r, np.array([.25, .35, .25, .15]), m)],
                               np.where(val, np.maximum(base, 4), base)))
    cons = O.x_cust.to_numpy()[R.x_order]
    first = consumers.first_name.to_numpy()[cons]
    last = consumers.last_name.to_numpy()[cons]
    out = pd.DataFrame(dict(
        review_id=uuids(r, m), product_id=pidx, sku_id=R.sku_id.to_numpy(), customer_id=consumers.customer_id.to_numpy()[cons],
        order_id=R.order_id.to_numpy(), line_no=R.line_no.to_numpy(), rating=rating.astype(int),
        submitted_at=R.submitted_at.dt.floor("s").to_numpy(), verified_purchase=True,
        helpful_votes=r.poisson(np.where(neg, 3.0, 0.8)), reviewer_display=[f"{a} {b[0]}." for a, b in zip(first, last)],
        themes=themes, target_words=np.clip(r.lognormal(np.log(45), 0.5, m), 20, 120).astype(int),
        batch_id=R.batch_id.to_numpy(), x_batch_dc=bdc, x_batch_month=bmon, x_segment=consumers.marketing_segment.to_numpy()[cons]))
    out["submitted_at"] = pd.to_datetime(out.submitted_at, utc=True)
    return out.sort_values("submitted_at", kind="stable").reset_index(drop=True)


def quality_reports(cat: dict, places: dict, suppliers: pd.DataFrame, sup: dict) -> pd.DataFrame:
    r = rng("facts", "quality")
    LT = sup["lots"]
    n = _cap(C.N_QUALITY_REPORTS)
    ING = cat["ingredients"].set_index("ingredient_id")
    qa = places["staff"][places["staff"].role == "quality_analyst"]
    qa_by_dc = {d: g.staff_id.tolist() for d, g in qa.groupby("location_id")}
    rows = []
    pl = C.PLANT
    fl = LT[LT.lot_code == pl["failed_lot_code"]].iloc[0]
    rows.append(dict(lot_idx=int(fl.name), report_type="receiving_inspection", result="pass",
                     inspected_at=fl.received_at + pd.Timedelta(hours=20), x_plant="failed_lot_receiving"))
    rows.append(dict(lot_idx=int(fl.name), report_type="retest", result="fail",
                     inspected_at=pd.Timestamp(pl["failed_lot_retest"], tz="UTC") + pd.Timedelta(hours=15), x_plant="failed_lot_retest"))
    for i in LT.index[LT.qc_status == "rejected"]:
        rows.append(dict(lot_idx=int(i), report_type="receiving_inspection", result="fail",
                         inspected_at=LT.received_at[i] + pd.Timedelta(hours=18), x_plant=None))
    for i in LT.index[LT.qc_status == "conditional"]:
        rows.append(dict(lot_idx=int(i), report_type="receiving_inspection", result="conditional",
                         inspected_at=LT.received_at[i] + pd.Timedelta(hours=22), x_plant=None))
    sup_rows = [dict(lot_idx=-1, supplier_id=s, report_type="supplier_audit", result=("pass" if r.random() < 0.85 else "conditional"),
                     inspected_at=pd.Timestamp(f"{y}-{int(r.integers(2, 12)):02d}-{int(r.integers(1, 28)):02d}", tz="UTC") + pd.Timedelta(hours=14),
                     x_plant=None)
                for s in suppliers.supplier_id for y in C.YEARS if r.random() < 0.6]
    remaining = max(0, n - len(rows) - len(sup_rows))
    pool = LT.index[(LT.qc_status == "released") & (LT.received_at >= pd.Timestamp(C.START, tz="UTC"))].to_numpy()
    pick = np.sort(r.choice(pool, size=min(remaining, len(pool)), replace=False))
    coa = r.random(len(pick)) < 0.25
    for i, cflag in zip(pick, coa):
        rows.append(dict(lot_idx=int(i), report_type="coa_review" if cflag else "receiving_inspection", result="pass",
                         inspected_at=LT.received_at[i] + pd.Timedelta(hours=int(r.integers(6, 60))), x_plant=None))
    Q = pd.DataFrame(rows + sup_rows)
    has = Q.lot_idx.to_numpy() >= 0
    li = np.maximum(Q.lot_idx.to_numpy(), 0)
    Q["lot_id"] = np.where(has, LT.lot_id.to_numpy()[li], None)
    Q["lot_code"] = np.where(has, LT.lot_code.to_numpy()[li], None)
    Q["supplier_id"] = np.where(has, LT.supplier_id.to_numpy()[li], Q.get("supplier_id"))
    Q["ingredient_id"] = np.where(has, LT.ingredient_id.to_numpy()[li], None)
    Q["dc_id"] = np.where(has, LT.dc_id.to_numpy()[li], "DC-NE")
    Q["inspector_staff_id"] = [qa_by_dc.get(d, qa.staff_id.tolist())[int(r.integers(0, 2))] for d in Q.dc_id]
    itype = Q.ingredient_id.map(ING["ingredient_type"]).to_numpy()
    moist = np.where(has, LT.moisture_pct.to_numpy()[li], np.nan)
    voil = np.where(has, LT.volatile_oil_pct.to_numpy()[li], np.nan)
    spec_moist = np.where(itype == "herb", 10.0, np.where(itype == "salt", 0.5, 12.0))
    tpc = np.round(r.uniform(2.0, 4.6, len(Q)), 1)
    fails = Q.result.to_numpy() == "fail"
    tpc[fails] = np.round(r.uniform(5.8, 6.6, int(fails.sum())), 1)
    plant = Q.x_plant.to_numpy() == "failed_lot_retest"
    moist[plant] = 13.4
    Q["facts"] = [dict(moisture_pct=None if np.isnan(m) else float(m), moisture_spec_max=float(sm),
                       volatile_oil_pct=None if np.isnan(v) else float(v), total_plate_count_log_cfu_g=float(t),
                       total_plate_count_spec_max=5.0, foreign_matter=("trace" if r.random() < 0.08 else "none"),
                       color_asta=(int(r.integers(95, 165)) if it == "chile" else None))
                  for m, sm, v, t, it in zip(moist, spec_moist, voil, tpc, itype)]
    Q["target_words"] = np.clip(r.lognormal(np.log(110), 0.25, len(Q)), 80, 160).astype(int)
    Q = Q.sort_values("inspected_at", kind="stable").reset_index(drop=True)
    Q.insert(0, "report_id", uuids(r, len(Q)))
    Q["inspected_at"] = pd.to_datetime(Q.inspected_at, utc=True).dt.floor("s")
    return Q.drop(columns=["lot_idx"])


def account_notes(cat: dict, accounts: pd.DataFrame, O: pd.DataFrame, Lx: pd.DataFrame, cp: pd.DataFrame, sup: dict) -> pd.DataFrame:
    r = rng("facts", "notes")
    n = _cap(C.N_ACCOUNT_NOTES)
    P, S = cat["products"], cat["skus"]
    sku_name = S.product_id.map(P.set_index("product_id").name).to_numpy()
    sku_name = dict(zip(S.sku_id, [f"{a} ({b})" for a, b in zip(sku_name, S.pack_description)]))
    rows = []
    # P8: complaints from accounts that received the failed lot's batches
    wl = Lx[(Lx.x_channel == "wholesale") & Lx.x_batch.isin(sup["hold_batches"])]
    hit_accts = O.account_id.to_numpy()[wl.x_order.to_numpy()]
    for a in pd.unique(hit_accts)[: max(1, int(len(pd.unique(hit_accts)) * 0.25))]:
        rows.append(dict(account_id=a, note_type="complaint", topic="musty_paprika",
                         noted_at=pd.Timestamp("2026-10-05", tz="UTC") + pd.Timedelta(hours=int(r.integers(0, 24 * 10))),
                         skus=list(wl[hit_accts == a].sku_id.unique()[:2])))
    # P13: substitutions at DC-CE
    sl = Lx[(Lx.line_status == "substituted") & (Lx.x_channel == "wholesale")]
    for oi, g in sl.groupby("x_order"):
        rows.append(dict(account_id=O.account_id.iat[oi], note_type="call", topic="substitution",
                         noted_at=O.ordered_at.iat[oi] + pd.Timedelta(hours=3),
                         skus=list(g.substituted_from_sku) + list(g.sku_id)))
    acc = accounts.reset_index(drop=True)
    w = acc.x_volume.to_numpy()
    topics = ["preference", "new_menu", "contract_renewal", "price_question", "delivery_issue", "clumping", "general_checkin",
              "seasonal_planning"]
    tw = np.array([.18, .14, .12, .10, .10, .05, .21, .10])
    k = max(0, n - len(rows))
    ai = weighted_choice(r, w, k)
    tp = np.array(topics)[weighted_choice(r, tw, k)]
    nt = np.array(["call", "visit", "email", "complaint"])[weighted_choice(r, np.array([.4, .25, .25, .1]), k)]
    days = r.integers(0, (C.END - C.START).days + 1, k)
    usual = acc.account_id.to_numpy()
    for a, t, typ, d in zip(ai, tp, nt, days):
        aid = usual[a]
        if t in ("delivery_issue", "clumping"):
            typ = "complaint"
        rows.append(dict(account_id=aid, note_type=typ, topic=t,
                         noted_at=pd.Timestamp(C.START, tz="UTC") + pd.Timedelta(days=int(d), hours=int(r.integers(13, 23))),
                         skus=None))
    N = pd.DataFrame(rows)
    am = acc.set_index("account_id")
    N["staff_id"] = am.loc[N.account_id, "account_rep_id"].to_numpy()
    N["cuisine"] = am.loc[N.account_id, "cuisine"].to_numpy()
    cps = cp.groupby("account_id")
    facts = []
    for row in N.itertuples():
        f = {}
        if row.topic == "preference" and row.cuisine in ("bakery_cafe", "mexican", "caterer_events", "american_new"):
            f["preference"] = "Saigon cinnamon for the cinnamon rolls, never the Ceylon" if r.random() < 0.6 else \
                              "Ceylon (canela) for the horchata and flan, not cassia"
        elif row.topic == "preference" and row.cuisine in ("tex_mex", "bbq_smokehouse"):
            f["preference"] = "the Chili Powder (Classic Blend), not straight ancho chile powder" if r.random() < 0.5 else \
                              "sweet smoked paprika only; the hot one is too much for their brisket"
        if row.topic in ("contract_renewal", "price_question") and row.account_id in cps.groups:
            g = cps.get_group(row.account_id)
            g = g[pd.to_datetime(g.effective_from) <= row.noted_at.tz_localize(None)].tail(3)
            f["contract_lines"] = [dict(sku=sku_name.get(s, s), price=float(p)) for s, p in zip(g.sku_id, g.contract_price)]
        if row.skus is not None and isinstance(row.skus, list):
            f["items"] = [sku_name.get(s, s) for s in row.skus]
        facts.append(f)
    N["facts"] = facts
    N["target_words"] = np.clip(r.lognormal(np.log(60), 0.45, len(N)), 30, 120).astype(int)
    N = N.sort_values("noted_at", kind="stable").reset_index(drop=True)
    N.insert(0, "note_id", uuids(r, len(N)))
    N["noted_at"] = N.noted_at.dt.floor("s")
    return N.drop(columns=["skus"])


def recipes(cat: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = rng("facts", "recipes")
    n = _cap(C.N_RECIPES)
    P = cat["products"]
    cand = P[P.product_type.isin(["single_origin", "blend", "pantry"]) & ~P.is_organic].reset_index(drop=True)
    tags = sorted({t for ts in cand.cuisine_tags for t in ts})
    tagw = np.array([sum(t in ts for ts in cand.cuisine_tags) for t in tags], dtype=float)
    courses = np.array(["main", "side", "soup", "appetizer", "dessert", "baked good", "breakfast", "drink", "sauce", "snack"])
    rows, links = [], []
    ids = uuids(r, n)
    for k in range(n):
        t = tags[weighted_choice(r, tagw, 1)[0]]
        pool = cand[[t in ts for ts in cand.cuisine_tags]]
        w = pool.x_pop.map(C.ZIPF_BY_POPULARITY).to_numpy()
        m = int(min(len(pool), r.integers(2, 6)))
        pick = pool.product_id.to_numpy()[r.choice(len(pool), size=m, replace=False, p=w / w.sum())]
        course = "baked good" if t == "baking" and r.random() < 0.7 else ("drink" if t == "tea" else courses[r.integers(0, 9)])
        rows.append(dict(recipe_id=ids[k], cuisine=t, course=course, servings=int(r.choice([2, 4, 4, 6, 6, 8, 12])),
                         total_minutes=int(r.choice([15, 20, 30, 45, 60, 90, 120, 240])), product_ids=list(pick),
                         target_words=int(r.integers(150, 300))))
        links += [(ids[k], p) for p in pick]
    return pd.DataFrame(rows), pd.DataFrame(links, columns=["recipe_id", "product_id"])


def product_descriptions(cat: dict) -> pd.DataFrame:
    P, S, ING = cat["products"], cat["skus"], cat["ingredients"].set_index("ingredient_id")
    packs = S.groupby("product_id").pack_description.apply(list)
    bc = cat["blend_components"].merge(ING[["name"]], left_on="ingredient_id", right_index=True)
    recipe = bc.sort_values(["product_id", "pct"], ascending=[True, False]).groupby("product_id").apply(
        lambda g: [f"{n} {p:g}%" for n, p in zip(g.name, g.pct)], include_groups=False)
    out = []
    for p in P.itertuples():
        aliases = ING.loc[p.ingredient_id, "aliases"] if p.ingredient_id in ING.index else []
        out.append(dict(product_id=p.product_id, name=p.name, category_id=p.category_id, product_type=p.product_type,
                        form=p.form, origin_country=p.origin_country, heat_level=int(p.heat_level),
                        flavor_tags=list(p.flavor_tags), cuisine_tags=list(p.cuisine_tags), allergens=list(p.allergens),
                        is_salt_free=bool(p.is_salt_free), is_organic=bool(p.is_organic), aliases=list(aliases),
                        ambiguity_group=p.ambiguity_group, recipe=recipe.get(p.product_id, []),
                        packs=packs.get(p.product_id, []), target_words=110))
    return pd.DataFrame(out)
