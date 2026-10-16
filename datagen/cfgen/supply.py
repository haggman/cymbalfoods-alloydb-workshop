"""Supply: suppliers, production batches, batch inputs, inbound lots, POs; and the batch on every order line.

Demand drives everything backward: order lines → batches (pack, blend, receive, kit) per product and DC →
ingredient needs per DC → lots received ahead of need → PO lines → POs grouped by supplier, DC and week.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from . import config as C
from .content import split
from .util import log, rng, stable_hash, uuids, weighted_choice

OZ_TO_KG = 0.028349523
DCS = ["DC-NE", "DC-SE", "DC-CE", "DC-WE"]
WEEK0 = pd.Timestamp(C.SUPPLY_LEAD_IN) - pd.Timedelta(days=pd.Timestamp(C.SUPPLY_LEAD_IN).dayofweek)   # a Monday
ISO2 = {"United States": "US", "India": "IN", "China": "CN", "Mexico": "MX", "Spain": "ES", "Italy": "IT", "Turkey": "TR",
        "Vietnam": "VN", "Indonesia": "ID", "Egypt": "EG", "Sri Lanka": "LK", "Madagascar": "MG", "Greece": "GR",
        "France": "FR", "Japan": "JP", "Guatemala": "GT", "Morocco": "MA", "Peru": "PE", "Brazil": "BR", "Hungary": "HU",
        "Jamaica": "JM", "Grenada": "GD", "South Korea": "KR", "Netherlands": "NL", "Singapore": "SG", "Tanzania": "TZ",
        "Bulgaria": "BG", "Canada": "CA", "Ethiopia": "ET", "Czech Republic": "CZ", "Thailand": "TH", "Malawi": "MW",
        "Uganda": "UG", "Ghana": "GH", "Nepal": "NP", "Australia": "AU", "Albania": "AL", "North Macedonia": "MK",
        "Uzbekistan": "UZ", "Germany": "DE", "Oman": "OM", "Cyprus": "CY", "Portugal": "PT", "Pakistan": "PK",
        "Tunisia": "TN", "Philippines": "PH", "Austria": "AT", "United Kingdom": "GB", "Lebanon": "LB", "Taiwan": "TW",
        "Kenya": "KE", "South Africa": "ZA", "Colombia": "CO", "Costa Rica": "CR", "Mauritius": "MU", "Belgium": "BE",
        "Ivory Coast": "CI", "Ecuador": "EC", "French Polynesia": "PF", "Papua New Guinea": "PG"}
SUPPLIER_WORDS = ["Golden", "Coastal", "Highland", "Valley", "Harbor", "Sunrise", "Monsoon", "Silk Road", "Meridian",
                  "Terra", "Verde", "Blue River", "Red Earth", "Spice Coast", "Green Hills", "Old Port", "Twin Peaks",
                  "Southern Cross", "Evergreen", "Crescent", "Lotus", "Cedar", "Saffron Fields", "Clove Island", "Atlas"]
SUPPLIER_KIND_SUFFIX = {"grower_exporter": ["Growers", "Farms Export", "Agro Exports", "Plantations"],
                        "grower": ["Farms", "Growers", "Family Farms", "Harvest Co."],
                        "processor": ["Processing", "Mills", "Botanicals", "Ingredients"],
                        "trading_house": ["Trading", "Commodities", "Trade House", "Imports & Exports"],
                        "co_packer": ["Foods", "Co-Packing", "Kitchens", "Provisions"],
                        "importer": ["Importers", "Import Co.", "Trading Co.", "Sourcing"]}


def build_suppliers(c: dict, cat: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = rng("suppliers")
    rows = []
    for sc in c["supplier_countries"]:
        kinds = split(sc["supplier_kinds"])
        for k in range(int(sc["suppliers"])):
            kind = kinds[k % len(kinds)]
            w = SUPPLIER_WORDS[int(r.integers(0, len(SUPPLIER_WORDS)))]
            suf = SUPPLIER_KIND_SUFFIX[kind][int(r.integers(0, 4))]
            rows.append(dict(country=sc["country"], country_code=sc["iso2"], supplier_kind=kind,
                             name=f"{w} {suf} ({sc['country']})" if k % 3 else f"{w} {suf}",
                             x_lead=float(sc["lead_time_days_mean"]), x_lead_sd=float(sc["lead_time_days_sd"])))
    S = pd.DataFrame(rows)
    # names must be unique
    seen = {}
    for i, nm in enumerate(S.name):
        if nm in seen:
            seen[nm] += 1
            S.loc[i, "name"] = f"{nm} {['II', 'III', 'IV', 'V', 'VI'][min(seen[nm] - 1, 4)]}"
        else:
            seen[nm] = 1
    S.insert(0, "supplier_id", uuids(r, len(S)))
    S.insert(1, "supplier_code", [f"SUP-{i + 1:04d}" for i in range(len(S))])
    S["quality_tier"] = np.array(["A", "B", "C"])[weighted_choice(r, np.array([.55, .35, .10]), len(S))]
    S["onboarded_on"] = [dt.date(int(y), int(m), 1) for y, m in zip(r.integers(1998, 2024, len(S)), r.integers(1, 13, len(S)))]
    S["is_active"] = r.random(len(S)) > 0.03
    # which supplier sells which ingredient from which origin
    ing = cat["ingredients"]
    links = []
    by_country = {k: g.index.to_numpy() for k, g in S.groupby("country")}
    traders = S.index[S.supplier_kind.isin(["trading_house", "importer"])].to_numpy()
    for row in ing.itertuples():
        origins = row.primary_origins
        for oi, origin in enumerate(origins):
            cand = by_country.get(origin)
            if cand is None or len(cand) == 0:
                cand = traders
            k = min(len(cand), 1 + int(r.random() < 0.5))
            pick = r.choice(cand, size=k, replace=False)
            for j, si in enumerate(pick):
                links.append((S.supplier_id[si], row.ingredient_id, origin, bool(j == 0 and oi == 0),
                              int(max(5, r.normal(S.x_lead[si], S.x_lead_sd[si])))))
    SI = pd.DataFrame(links, columns=["supplier_id", "ingredient_id", "origin_country", "is_primary", "lead_time_days"])
    SI = SI.drop_duplicates(["supplier_id", "ingredient_id", "origin_country"]).reset_index(drop=True)
    return S, SI


def apply_stockout(O: pd.DataFrame, Lx: pd.DataFrame, cat: dict) -> dict:
    """P13: Saigon cinnamon runs out at DC-CE on Dec 10, 2026; lines are substituted to Korintje or backordered."""
    r = rng("plant", "stockout")
    P, S = cat["products"], cat["skus"].reset_index(drop=True)
    pl = C.PLANT
    sai = P[(P.ingredient_id == pl["stockout_item"])]
    kor = P[(P.ingredient_id == pl["stockout_substitute"])]
    S_prod = S.set_index("sku_id").product_id
    kor_by = {(f.split("|")[0] if not f.startswith("organic") else f, pk): sku
              for sku, f, pk in zip(S[S.product_id.isin(kor.product_id)].sku_id,
                                    S[S.product_id.isin(kor.product_id)].product_id.map(dict(zip(kor.product_id, kor.x_variant))),
                                    S[S.product_id.isin(kor.product_id)].pack_code)}
    sai_var = dict(zip(sai.product_id, sai.x_variant))
    d0 = (pl["stockout_from"] - C.START).days
    sku_ids = S.sku_id.to_numpy()
    is_sai = Lx.sku_id.isin(S[S.product_id.isin(sai.product_id)].sku_id).to_numpy()
    hit = np.nonzero(is_sai & (Lx.x_dc.to_numpy() == pl["stockout_dc"]) & (Lx.x_day.to_numpy() >= d0)
                     & np.isin(Lx.line_status.to_numpy(), ["fulfilled", "returned"]))[0]
    subst = back = 0
    for i in hit:
        old = Lx.sku_id.iat[i]
        var = sai_var[S_prod[old]]
        key = (var.split("|")[0] if not var.startswith("organic") else var, old.rsplit("-", 1)[1])
        new = kor_by.get(key)
        if new is None and Lx.x_channel.iat[i] == "store":
            same_pack = [v for (f, pk), v in kor_by.items() if pk == key[1]]
            store_kor = S[S.product_id.isin(kor.product_id) & S.sold_in_stores].sku_id.tolist()
            new = (same_pack or store_kor or [None])[0]
        if Lx.x_channel.iat[i] == "store":
            if new:
                Lx.iat[i, Lx.columns.get_loc("sku_id")] = new          # the shopper picked another cinnamon
            continue
        if new and r.random() < 0.6:
            Lx.iat[i, Lx.columns.get_loc("substituted_from_sku")] = old
            Lx.iat[i, Lx.columns.get_loc("sku_id")] = new
            Lx.iat[i, Lx.columns.get_loc("line_status")] = "substituted"
            subst += 1
        else:
            Lx.iat[i, Lx.columns.get_loc("line_status")] = "backordered"
            back += 1
    sidx = {s: k for k, s in enumerate(sku_ids)}
    Lx["x_sku"] = Lx.sku_id.map(sidx).to_numpy()
    return dict(stockout_lines=int(len(hit)), substituted=subst, backordered=back)


def build(c: dict, cat: dict, O: pd.DataFrame, Lx: pd.DataFrame, suppliers: pd.DataFrame, sup_ing: pd.DataFrame) -> dict:
    P, S = cat["products"], cat["skus"].reset_index(drop=True)
    ING = cat["ingredients"].set_index("ingredient_id")
    pid_idx = {p: i for i, p in enumerate(P.product_id)}
    sku_prod = S.product_id.map(pid_idx).to_numpy()
    r = rng("supply", "batches")
    n_weeks = int(((pd.Timestamp(C.END) - WEEK0).days) // 7) + 2
    day0 = (pd.Timestamp(C.START) - WEEK0).days            # START as days since WEEK0

    # ---- demand per product, DC, week (kg) ------------------------------------------------------------
    shipped = Lx.line_status.isin(["fulfilled", "substituted", "returned"]).to_numpy()
    lp = sku_prod[Lx.x_sku.to_numpy()]
    kg = Lx.quantity.to_numpy() * S.net_oz.to_numpy()[Lx.x_sku.to_numpy()] * OZ_TO_KG
    gift = (P.product_type == "gift_set").to_numpy()[lp]
    kg = np.where(gift, Lx.quantity.to_numpy() * 1.0, kg)           # kits counted in units
    wk = (Lx.x_day.to_numpy() + day0) // 7
    dc_i = pd.Series(Lx.x_dc).map({d: i for i, d in enumerate(DCS)}).to_numpy()
    key = (lp * 4 + dc_i) * n_weeks + wk
    dem = np.bincount(key[shipped], weights=kg[shipped], minlength=len(P) * 4 * n_weeks).reshape(len(P), 4, n_weeks)

    # ---- batches: consecutive blocks of weeks per product and DC --------------------------------------
    cov = P.x_pop.map(C.BATCH_WEEKS).to_numpy()
    pl = C.PLANT
    sto_dc = DCS.index(pl["stockout_dc"])
    sto_wk = ((pd.Timestamp(pl["stockout_from"]) - WEEK0).days) // 7
    sai_prod = set(np.nonzero((P.ingredient_id == pl["stockout_item"]).to_numpy())[0])
    batch_rows = []         # product, dc, block_start_week, block_end_week, produced_day, qty_kg
    block_of = np.full((len(P), 4, n_weeks), -1, dtype=np.int64)
    first_wk = day0 // 7
    for p in range(len(P)):
        for d in range(4):
            series = dem[p, d]
            if series.sum() <= 0:
                continue
            w = first_wk
            while w < n_weeks:
                b0, b1 = w, min(w + int(cov[p]), n_weeks)
                if p in sai_prod and d == sto_dc and b0 < sto_wk < b1:
                    b1 = sto_wk                                     # the last Saigon batch at DC-CE ends at the stockout
                q = series[b0:b1].sum()
                if q > 0:
                    prod_day = b0 * 7 - int(r.integers(2, 8))
                    over = r.uniform(*C.BATCH_OVERAGE)
                    if p in sai_prod and d == sto_dc and b1 == sto_wk:
                        over = 1.0
                    block_of[p, d, b0:b1] = len(batch_rows)
                    batch_rows.append((p, d, b0, b1, prod_day, q * over))
                w = b1
                if p in sai_prod and d == sto_dc and w >= sto_wk:
                    break
    B = pd.DataFrame(batch_rows, columns=["x_prod", "x_dc", "x_w0", "x_w1", "x_day", "quantity_kg"])
    B["batch_type"] = P.x_batch.to_numpy()[B.x_prod]
    log(f"  batches {len(B):,}")

    # order lines → batch
    lb = block_of[lp, dc_i, np.minimum(wk, n_weeks - 1)]
    lb = np.where(shipped, lb, -1)

    # ---- batch inputs (needs per ingredient) -----------------------------------------------------------
    recipes = P.x_recipe.tolist()
    need = []           # batch, ingredient, kg
    for bi, (p, q, bt) in enumerate(zip(B.x_prod, B.quantity_kg, B.batch_type)):
        if bt == "kit":
            continue
        for ing, pct in recipes[p]:
            need.append((bi, ing, q * pct / 100.0))
    N = pd.DataFrame(need, columns=["x_batch", "ingredient_id", "kg"])
    N["x_dc"] = B.x_dc.to_numpy()[N.x_batch]
    N["x_day"] = B.x_day.to_numpy()[N.x_batch]
    N = N.sort_values(["ingredient_id", "x_dc", "x_day", "x_batch"], kind="stable").reset_index(drop=True)

    # ---- lots: windows of need per ingredient and DC; window length solved to hit the lot target ----------
    pop_ing = ING["x_popularity"].to_dict()
    base_weeks = {5: 2.0, 4: 2.5, 3: 3.5, 2: 5.0, 1: 7.0}
    groups = {k: g for k, g in N.groupby(["ingredient_id", "x_dc"], sort=True)}
    target = C.TARGET_LOTS * C.SCALE

    def windows(mult: float, g: pd.DataFrame, ing: str) -> np.ndarray:
        wl = base_weeks[pop_ing.get(ing, 3)] * mult * 7
        return ((g.x_day.to_numpy() - g.x_day.to_numpy().min()) // max(wl, 3)).astype(np.int64)

    lo, hi = 0.2, 20.0
    for _ in range(30):
        mid = math.sqrt(lo * hi)
        n_lines = sum(len(np.unique(windows(mid, g, k[0]))) for k, g in groups.items())
        if n_lines * C.LOTS_PER_LINE > target:
            lo = mid
        else:
            hi = mid
    mult = math.sqrt(lo * hi)

    r = rng("supply", "lots")
    fl_item, fl_dc = pl["failed_lot_item"], pl["failed_lot_dc"]
    fl_recv = (pd.Timestamp(pl["failed_lot_received"]) - WEEK0).days
    fl_use0 = (pd.Timestamp(pl["failed_lot_use"][0]) - WEEK0).days
    fl_use1 = (pd.Timestamp(pl["failed_lot_use"][1]) - WEEK0).days
    lot_rows, line_rows, inp_rows = [], [], []
    si_by_ing = {k: g for k, g in sup_ing.groupby("ingredient_id")}
    sup_lead = suppliers.set_index("supplier_id")[["x_lead", "x_lead_sd", "country"]]
    for (ing, dc), g in groups.items():
        win = windows(mult, g, ing)
        days = g.x_day.to_numpy()
        if ing == fl_item and dc == DCS.index(fl_dc):
            win = np.where(days < fl_use0, win, np.where(days <= fl_use1, 10_000, 10_001 + win))
        if ing == pl["stockout_item"] and dc == DCS.index(pl["stockout_dc"]):
            sday = (pd.Timestamp(pl["stockout_from"]) - WEEK0).days
            before = win[days < sday]
            if len(before):
                win = np.where(days < sday, win, before.max())     # no new Saigon lots reach DC-CE after the stockout
        opts = si_by_ing[ing]
        prim = opts[opts.is_primary]
        for wv in np.unique(win):
            rows = g[win == wv]
            first_day = int(rows.x_day.min())
            tot = float(rows.kg.sum())
            planted = ing == fl_item and dc == DCS.index(fl_dc) and wv == 10_000
            pool = prim if (len(prim) and r.random() < 0.7) else opts
            o = pool.iloc[int(r.integers(0, len(pool)))]
            if planted:
                es = opts[opts.origin_country == "Spain"]
                o = es.iloc[0] if len(es) else o
                recv = fl_recv
            else:
                recv = first_day - int(r.integers(3, 12))
            lead = max(5, int(r.normal(sup_lead.loc[o.supplier_id, "x_lead"], sup_lead.loc[o.supplier_id, "x_lead_sd"])))
            line_id = len(line_rows)
            line_rows.append((o.supplier_id, dc, ing, o.origin_country, recv - lead, recv, tot * r.uniform(1.0, 1.08)))
            nl = 1 if planted else 1 + int(r.random() < (C.LOTS_PER_LINE - 1))
            split_at = r.uniform(0.35, 0.65) if nl == 2 else 1.0
            lot_ids = []
            for li in range(nl):
                share = split_at if li == 0 else 1 - split_at
                lot_ids.append(len(lot_rows))
                lot_rows.append((line_id, ing, o.supplier_id, o.origin_country, dc, recv + (li * int(r.integers(0, 3))),
                                 tot * share * r.uniform(1.0, 1.04), "planted" if planted else "released"))
            if r.random() < C.QC["rejected"]:
                lot_rows.append((line_id, ing, o.supplier_id, o.origin_country, dc, recv, tot * 0.3, "rejected"))
            # draw inputs FIFO across the window's lots
            cap = [lot_rows[k][6] for k in lot_ids]
            used = [0.0] * len(lot_ids)
            cur = 0
            for bi, need_kg in zip(rows.x_batch.to_numpy(), rows.kg.to_numpy()):
                left = need_kg
                while left > 1e-9:
                    take = min(left, cap[cur]) if cur < len(cap) - 1 else left
                    inp_rows.append((bi, lot_ids[cur], take))
                    used[cur] += take
                    cap[cur] -= take
                    left -= take
                    if cap[cur] <= 1e-9 and cur < len(cap) - 1:
                        cur += 1
            for k, u_ in zip(lot_ids, used):
                if u_ > lot_rows[k][6]:
                    lot_rows[k] = lot_rows[k][:6] + (u_ * r.uniform(1.0, 1.03),) + lot_rows[k][7:]
    # open POs at year end: a share of ingredient-DC pairs have an order on the water for January
    end_d = (pd.Timestamp(C.END) - WEEK0).days
    for (ing, dc), g in groups.items():
        if r.random() < 0.3 and g.x_day.max() > end_d - 120:
            opts = si_by_ing[ing]
            o = opts.iloc[int(r.integers(0, len(opts)))]
            lead = max(7, int(r.normal(sup_lead.loc[o.supplier_id, "x_lead"], sup_lead.loc[o.supplier_id, "x_lead_sd"])))
            due = end_d + int(r.integers(3, 40))
            if due - lead <= end_d:
                line_rows.append((o.supplier_id, dc, ing, o.origin_country, due - lead, None,
                                  float(g.kg.tail(8).sum()) * r.uniform(0.8, 1.2)))
    log(f"  po lines {len(line_rows):,}  lots {len(lot_rows):,}  batch inputs {len(inp_rows):,}  (window x{mult:.2f})")

    # ---- the in-transit Saigon PO for DC-CE (P13) -------------------------------------------------------
    sai_sup = sup_ing[(sup_ing.ingredient_id == pl["stockout_item"])].iloc[0]
    due = (pd.Timestamp(pl["stockout_po_due"]) - WEEK0).days
    line_rows.append((sai_sup.supplier_id, DCS.index(pl["stockout_dc"]), pl["stockout_item"], sai_sup.origin_country,
                      due - 52, None, 1800.0))

    # ---- POs: group lines by supplier, DC and ordering week ----------------------------------------------
    PL = pd.DataFrame(line_rows, columns=["supplier_id", "x_dc", "ingredient_id", "origin_country", "x_order_day", "x_recv_day", "quantity_kg"])
    PL["x_week"] = PL.x_order_day // 7
    keys = PL.groupby(["supplier_id", "x_dc", "x_week"], sort=True).ngroup()
    PL["x_po"] = keys
    r = rng("supply", "po")
    n_po = int(keys.max()) + 1
    po_ids = uuids(r, n_po)
    PL["po_id"] = po_ids[PL.x_po]
    PL = PL.sort_values(["x_po", "ingredient_id"], kind="stable")
    PL["line_no"] = PL.groupby("x_po").cumcount() + 1
    band = ING["x_price_band"].to_dict()
    cost_kg = {1: 4.5, 2: 9.0, 3: 18.0, 4: 45.0, 5: 120.0}
    def _cost(i: str) -> float:
        if i.startswith("saffron_"):
            return 2600.0
        if i.startswith("vanilla_") and "__" not in i:
            return 380.0
        return cost_kg[band.get(i, 2)]
    PL["unit_cost_per_kg"] = [round(_cost(i) * float(np.exp(x)), 4) for i, x in zip(PL.ingredient_id, r.normal(0, 0.12, len(PL)))]
    PL["quantity_kg"] = PL.quantity_kg.round(2)
    POh = PL.groupby("x_po").agg(supplier_id=("supplier_id", "first"), x_dc=("x_dc", "first"),
                                 x_order_day=("x_order_day", "min"), x_recv_day=("x_recv_day", "max")).reset_index()
    tot = (PL.quantity_kg * PL.unit_cost_per_kg).groupby(PL.x_po).sum()
    POh["total_cost"] = POh.x_po.map(tot).round(2)
    POh["po_id"] = po_ids[POh.x_po]
    sup_country = suppliers.set_index("supplier_id").country
    freight = POh.supplier_id.map(sup_country).map(
        {sc["country"]: sc["freight"] for sc in c["supplier_countries"]}).fillna("sea")
    POh["freight"] = freight.to_numpy()
    end_day = (pd.Timestamp(C.END) - WEEK0).days
    lead_days = (POh.x_recv_day.fillna(POh.x_order_day + POh.supplier_id.map(sup_lead["x_lead"]).round()) - POh.x_order_day).to_numpy()
    POh["ordered_on"] = (WEEK0 + pd.to_timedelta(POh.x_order_day, "D")).dt.date
    POh["expected_on"] = (WEEK0 + pd.to_timedelta(POh.x_order_day + lead_days + r.integers(-3, 4, len(POh)), "D")).dt.date
    recv = POh.x_recv_day.to_numpy(dtype=float)
    POh["received_on"] = [(WEEK0 + pd.Timedelta(days=int(x))).date() if not np.isnan(x) and x <= end_day else None for x in recv]
    POh["status"] = np.where(pd.isna(POh.received_on), "in_transit", "received")
    yy = pd.to_datetime(POh.ordered_on).dt.year.to_numpy() % 100
    POh = POh.sort_values(["ordered_on", "x_po"], kind="stable").reset_index(drop=True)
    yy = pd.to_datetime(POh.ordered_on).dt.year.to_numpy() % 100
    seq = POh.groupby(yy).cumcount().to_numpy() + 1
    POh["po_number"] = [f"PO-{y:02d}-{s:06d}" for y, s in zip(yy, seq)]
    POh["destination_dc_id"] = [DCS[d] for d in POh.x_dc]

    # ---- lots table ------------------------------------------------------------------------------------
    r = rng("supply", "lot_attrs")
    LT = pd.DataFrame(lot_rows, columns=["x_line", "ingredient_id", "supplier_id", "origin_country", "x_dc", "x_recv_day", "quantity_kg", "x_kind"])
    LT["lot_id"] = uuids(r, len(LT))
    line_po = PL.sort_index()
    LT["po_id"] = PL.loc[LT.x_line, "po_id"].to_numpy()
    LT["po_line_no"] = PL.loc[LT.x_line, "line_no"].to_numpy()
    received = WEEK0 + pd.to_timedelta(LT.x_recv_day, "D") + pd.to_timedelta(r.integers(7 * 3600, 16 * 3600, len(LT)), "s")
    LT["received_at"] = received.dt.tz_localize("UTC")
    LT["dc_id"] = [DCS[d] for d in LT.x_dc]
    LT["harvest_year"] = (received.dt.year - (r.random(len(LT)) < 0.45)).astype(int)
    itype = LT.ingredient_id.map(ING["ingredient_type"]).to_numpy()
    spice_like = np.isin(itype, ["chile", "peppercorn", "spice", "seed", "herb", "aromatic", "flower", "citrus", "specialty"])
    herb = itype == "herb"
    LT["moisture_pct"] = np.where(itype == "salt", np.round(r.uniform(0.05, 0.6, len(LT)), 2),
                                  np.where(spice_like, np.round(np.where(herb, r.uniform(5.5, 10.5, len(LT)), r.uniform(6.5, 11.5, len(LT))), 2), np.nan))
    LT["volatile_oil_pct"] = np.where(spice_like & ~herb, np.round(r.uniform(0.6, 5.5, len(LT)), 2),
                                      np.where(herb, np.round(r.uniform(0.4, 2.8, len(LT)), 2), np.nan))
    u = r.random(len(LT))
    qc = np.where(u < C.QC["conditional"], "conditional", "released").astype(object)
    qc[LT.x_kind.to_numpy() == "rejected"] = "rejected"
    qc[LT.x_kind.to_numpy() == "planted"] = "hold"
    LT["qc_status"] = qc
    LT["qc_released_at"] = np.where(qc == "rejected", pd.NaT, LT.received_at + pd.to_timedelta(r.integers(20, 72, len(LT)), "h"))
    LT["qc_released_at"] = pd.to_datetime(LT["qc_released_at"], utc=True)
    LT["best_by"] = (received + pd.to_timedelta(r.integers(24 * 30, 36 * 30, len(LT)), "D")).dt.date
    # lot codes: L-<origin>-<yymm>-<seq>
    yymm = received.dt.strftime("%y%m").to_numpy()
    org = LT.origin_country.map(ISO2).fillna("XX").to_numpy()
    LT["x_key"] = [f"{o}-{y}" for o, y in zip(org, yymm)]
    LT = LT.sort_values(["received_at", "ingredient_id"], kind="stable")
    LT["x_seq"] = LT.groupby("x_key").cumcount() + 1
    planted_mask = LT.x_kind == "planted"
    LT["x_seq"] = LT["x_seq"] + np.where(LT.x_key.to_numpy() == "ES-2608", 200, 0)   # keep 0117 free for the plant
    LT["lot_code"] = [f"L-{k}-{s:04d}" for k, s in zip(LT.x_key, LT.x_seq)]
    LT.loc[planted_mask, "lot_code"] = pl["failed_lot_code"]
    LT = LT.sort_index()
    LT["quantity_kg"] = LT.quantity_kg.round(2)

    # ---- batches table ---------------------------------------------------------------------------------
    r = rng("supply", "batch_attrs")
    B["batch_id"] = uuids(r, len(B))
    B["product_id"] = P.product_id.to_numpy()[B.x_prod]
    B["dc_id"] = [DCS[d] for d in B.x_dc]
    prod_at = WEEK0 + pd.to_timedelta(B.x_day, "D") + pd.to_timedelta(r.integers(6 * 3600, 18 * 3600, len(B)), "s")
    B["produced_at"] = prod_at.dt.tz_localize("UTC")
    B["quantity_kg"] = B.quantity_kg.round(3)
    B["best_by"] = (prod_at + pd.to_timedelta(r.integers(18 * 30, 30 * 30, len(B)), "D")).dt.date
    B["qc_status"] = "released"
    B = B.sort_values(["produced_at", "product_id"], kind="stable")
    seqd = B.groupby([B.dc_id, B.produced_at.dt.strftime("%y%m%d")]).cumcount().to_numpy() + 1
    B["batch_code"] = [f"B-{d[3:]}-{t}-{s:03d}" for d, t, s in zip(B.dc_id, B.produced_at.dt.strftime("%y%m%d"), seqd)]
    B = B.sort_index()

    BI = pd.DataFrame(inp_rows, columns=["x_batch", "x_lot", "quantity_kg"])
    BI = BI.groupby(["x_batch", "x_lot"], as_index=False, sort=True)["quantity_kg"].sum()
    BI["input_no"] = BI.groupby("x_batch").cumcount() + 1
    BI["batch_id"] = B.batch_id.to_numpy()[BI.x_batch]
    BI["lot_id"] = LT.lot_id.to_numpy()[BI.x_lot]
    BI["quantity_kg"] = BI.quantity_kg.round(3)

    # P8: batches that used the failed lot go on hold
    fl = LT.index[LT.lot_code == pl["failed_lot_code"]]
    hold_batches = BI[BI.x_lot.isin(fl)].x_batch.unique()
    B.loc[hold_batches, "qc_status"] = "hold"

    Lx["batch_id"] = np.where(lb >= 0, B.batch_id.to_numpy()[np.maximum(lb, 0)], None)
    Lx["x_batch"] = lb
    return dict(batches=B, batch_inputs=BI, lots=LT, po_lines=PL, purchase_orders=POh, block_of=block_of,
                dem=dem, n_weeks=n_weeks, day0=day0, hold_batches=hold_batches)
