"""Inventory on Dec 31, 2026 (what the batches have left) and the DCs' manual reorder points."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .supply import DCS, OZ_TO_KG
from .util import rng


def build(cat: dict, places: dict, O: pd.DataFrame, Lx: pd.DataFrame, sup: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = rng("inventory")
    P, S = cat["products"], cat["skus"].reset_index(drop=True)
    B = sup["batches"]
    n_days = (C.END - C.START).days + 1
    pid_idx = {p: i for i, p in enumerate(P.product_id)}
    sku_prod = S.product_id.map(pid_idx).to_numpy()
    sku_kg = S.net_oz.to_numpy() * OZ_TO_KG
    gift = (P.product_type == "gift_set").to_numpy()
    sku_kg = np.where(gift[sku_prod], 1.0, sku_kg)
    pl = C.PLANT

    # what each batch has left
    ok = Lx.x_batch.to_numpy() >= 0
    used = np.bincount(Lx.x_batch.to_numpy()[ok], weights=(Lx.quantity.to_numpy() * sku_kg[Lx.x_sku.to_numpy()])[ok],
                       minlength=len(B))
    left = np.maximum(B.quantity_kg.to_numpy() - used, 0)
    # excluded from stock: batches on hold (P8), Saigon at DC-CE (P13), the sold-out gift edition
    sai = set(np.nonzero((P.ingredient_id == pl["stockout_item"]).to_numpy())[0])
    sold_out = set(np.nonzero(((P.x_slug == pl["sellout_gift"]) & (P.x_variant == str(pl["sellout_edition"]))).to_numpy())[0])
    bprod, bdc = B.x_prod.to_numpy(), B.x_dc.to_numpy()
    excl = (B.qc_status.to_numpy() == "hold") | np.array([p in sold_out for p in bprod]) | \
           (np.array([p in sai for p in bprod]) & (bdc == DCS.index(pl["stockout_dc"])))
    recent = B.x_w1.to_numpy() >= (sup["n_weeks"] - 30)

    # recent sales mix per (product, DC) by SKU, from the last 90 days
    last = Lx[(Lx.x_day >= n_days - 90) & ok]
    mix = last.assign(kg=last.quantity * sku_kg[last.x_sku.to_numpy()]).groupby(["x_dc", "x_sku"]).kg.sum()
    rows = []
    for bi in np.nonzero((left > 0.05) & recent & ~excl)[0]:
        p, d = bprod[bi], bdc[bi]
        skus = np.nonzero(sku_prod == p)[0]
        w = np.array([mix.get((DCS[d], s), 0.0) for s in skus]) + 1e-3
        share = w / w.sum()
        units = np.floor(left[bi] * 0.6 * share / sku_kg[skus]).astype(int)
        for s, u in zip(skus, units):
            if u > 0:
                rows.append((DCS[d], s, bi, int(u)))
    # stores: what each store sold in the last 120 days, from its region's latest batches
    stores = places["locations"][places["locations"].location_type == "store"].reset_index(drop=True)
    st = Lx.merge(O[["x_store"]], left_on="x_order", right_index=True)
    st = st[(st.x_channel == "store") & (st.x_day >= n_days - 120)]
    sold = st.groupby(["x_store", "x_sku"]).quantity.sum()
    latest = {}
    for bi in np.argsort(B.produced_at.to_numpy()):
        latest.setdefault((bprod[bi], bdc[bi]), []).append(bi)
    region_dc = {i: DCS.index(f"DC-{reg}") for i, reg in enumerate(stores.region_id)}
    for (si, s), q in sold.items():
        p, d = sku_prod[s], region_dc[si]
        if p in sold_out or (p in sai and d == DCS.index(pl["stockout_dc"])):
            continue
        bl = [b for b in latest.get((p, d), []) if not excl[b]]
        if not bl:
            continue
        weekly = q / 17.0
        u = int(r.poisson(max(weekly * 2.5, 0.5))) + 1
        if len(bl) > 1 and r.random() < 0.35:
            u2 = max(1, u // 3)
            rows.append((stores.location_id[si], s, bl[-2], u2))
            u = max(1, u - u2)
        rows.append((stores.location_id[si], s, bl[-1], u))
    I = pd.DataFrame(rows, columns=["location_id", "x_sku", "x_batch", "on_hand"])
    I = I.groupby(["location_id", "x_sku", "x_batch"], as_index=False, sort=True).on_hand.sum()
    I["sku_id"] = S.sku_id.to_numpy()[I.x_sku]
    I["batch_id"] = B.batch_id.to_numpy()[I.x_batch]
    I["reserved"] = np.where(I.location_id.str.startswith("DC"), r.binomial(I.on_hand.to_numpy(), 0.03), 0)
    I["best_by"] = B.best_by.to_numpy()[I.x_batch]
    end = pd.Timestamp(C.NOW_UTC)
    I["last_counted_at"] = end - pd.to_timedelta(r.integers(1, 75 * 24 * 3600, len(I)), "s")
    I["last_counted_at"] = I.last_counted_at.dt.floor("s")

    # reorder points: per DC and SKU, from the last 26 weeks of that DC's demand (stores included)
    rec = Lx[(Lx.x_day >= n_days - 182) & ok]
    wk = rec.groupby(["x_dc", "x_sku"]).quantity.sum() / 26.0
    rp = wk.reset_index().rename(columns={"x_dc": "location_id", "quantity": "weekly"})
    rp = rp[rp.weekly > 0]
    rp["sku_id"] = S.sku_id.to_numpy()[rp.x_sku]
    rp["reorder_point"] = np.ceil(rp.weekly * r.uniform(2.5, 4.0, len(rp))).astype(int).clip(1)
    rp["reorder_qty"] = np.ceil(rp.weekly * r.uniform(4.0, 8.0, len(rp))).astype(int).clip(1)
    rp["source"] = "manual"
    rp["updated_at"] = (pd.Timestamp("2025-03-01", tz="UTC") + pd.to_timedelta(r.integers(0, 640, len(rp)), "D")
                        + pd.to_timedelta(r.integers(8 * 3600, 18 * 3600, len(rp)), "s"))
    return I, rp.reset_index(drop=True)
