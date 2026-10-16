"""Orders and order lines for all three channels (batches are attached later by supply.py)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config as C
from .content import pairs
from .demand import Demand
from .util import log, rng, stable_hash, uuids, weighted_choice

REGION_TZ = {"NE": "America/New_York", "SE": "America/New_York", "CE": "America/Chicago", "WE": "America/Los_Angeles"}
SEG_SEASON = {"gift_giver": {10: 1.6, 11: 3.0, 12: 4.0}, "home_baker": {9: 1.2, 10: 1.5, 11: 2.0, 12: 2.0},
              "grill_master": {5: 1.5, 6: 1.7, 7: 1.7, 8: 1.4}, "tea_and_coffee": {1: 1.3, 11: 1.2, 12: 1.4}}
BULK_PACKS = {"B80", "P400", "O1G", "O3L", "S1G", "N1G", "C80", "K80", "D400", "V100", "Y1G", "E32"}


def _month_index(days: np.ndarray) -> np.ndarray:
    d = (pd.Timestamp(C.START) + pd.to_timedelta(days, "D"))
    return ((d.year - C.START.year) * 12 + d.month - 1).to_numpy()


def build(c: dict, cat: dict, places: dict, consumers: pd.DataFrame, accounts: pd.DataFrame, acc_extra: dict,
          contract_prices: pd.DataFrame, dem: Demand, ring: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    P, S = cat["products"], cat["skus"].reset_index(drop=True)
    pid_idx = {p: i for i, p in enumerate(P.product_id)}
    sku_prod = S.product_id.map(pid_idx).to_numpy()
    sku_idx = {s: i for i, s in enumerate(S.sku_id)}
    n_days = dem.n_days
    month_of_day = _month_index(np.arange(n_days))
    cal_month = dem.dates.month.to_numpy()

    segs = pd.DataFrame(c["consumer_segments"]).set_index("segment")
    seg_names = list(segs.index)
    # segment affinity per product: cuisine tags plus the product's seasonality profile as a pseudo-tag
    seg_aff = {}
    for sg in seg_names:
        aff = dict(pairs(segs.loc[sg, "tag_affinity"]))
        prem = float(segs.loc[sg, "premium_affinity"])
        w = np.array([0.35 + sum(aff.get(t, 0) for t in tags) + aff.get(pr, 0)
                      for tags, pr in zip(P.cuisine_tags, P.seasonality_profile)])
        w *= (P["x_band"].to_numpy() / 3.0) ** (prem - 1)
        if sg == "salt_free_shopper":
            w *= np.where(P.is_salt_free & (P.product_type == "blend"), 3.0, 1.0)
        if sg == "gift_giver":
            w *= np.where(P.product_type == "gift_set", 6.0, 1.0)
        else:
            w *= np.where(P.product_type == "gift_set", 0.5, 1.0)
        seg_aff[sg] = w

    def pack_w(ch: str) -> np.ndarray:
        pref = C.CHANNEL_PACK_PREF[ch]
        m = {"store": S.sold_in_stores, "online": S.sold_online, "wholesale": S.sold_wholesale}[ch].to_numpy()
        return S.pack_code.map(pref).fillna(0.03).to_numpy() * m

    PW = {ch: pack_w(ch) for ch in ("store", "online", "wholesale")}

    # ------------------------------------------------------------------ order headers -------------------
    L = places["locations"]
    stores = L[L.location_type == "store"].reset_index(drop=True)
    cons_seg = consumers.marketing_segment.to_numpy()
    cons_act = consumers.x_activity.to_numpy()
    cons_start, cons_end = consumers.x_start.to_numpy(), consumers.x_end.to_numpy()
    seg_online = segs.online_share.astype(float).to_dict()
    on_share = np.where(consumers.x_online_only.to_numpy(), 1.0, pd.Series(cons_seg).map(seg_online).to_numpy())
    heads = []   # (channel, day, store_idx, cust_idx, acct_idx, seg)

    # store sales
    r = rng("orders", "store")
    lam = dem.daily_counts(r, "store")
    opened = pd.to_datetime(stores.opened_on).to_numpy()
    sw = np.zeros((len(stores), n_days))
    for i, s in stores.iterrows():
        o = (pd.Timestamp(s.opened_on) - pd.Timestamp(C.START)).days
        ramp = np.clip((np.arange(n_days) - o) / 180, 0.6, 1.0)
        sw[i] = s.sq_ft * np.where(np.arange(n_days) >= o, ramp, 0)
    sw = sw / sw.sum(axis=0, keepdims=True)
    counts = r.poisson(lam[None, :] * sw)
    st_idx, st_day = np.nonzero(counts)
    reps = counts[st_idx, st_day]
    st_idx, st_day = np.repeat(st_idx, reps), np.repeat(st_day, reps)
    ident = r.random(len(st_idx)) < C.STORE_IDENTIFIED_SHARE
    st_cust = np.full(len(st_idx), -1)
    home = consumers.home_store_id.to_numpy()
    store_ids = stores.location_id.to_numpy()
    mo = month_of_day[st_day]
    for si, sid in enumerate(store_ids):
        members = np.nonzero(home == sid)[0]
        if len(members) == 0:
            continue
        base_w = cons_act[members] * (1 - 0.6 * on_share[members])
        sel = np.nonzero((st_idx == si) & ident)[0]
        for m in np.unique(mo[sel]):
            rows = sel[mo[sel] == m]
            m_start, m_end = m * 30.44, (m + 1) * 30.44
            alive = (cons_start[members] <= m_end) & (cons_end[members] >= m_start)
            w = base_w * alive
            if w.sum() == 0:
                continue
            st_cust[rows] = members[weighted_choice(r, w, len(rows))]
    st_seg = np.where(st_cust >= 0, cons_seg[np.maximum(st_cust, 0)],
                      np.array(seg_names)[weighted_choice(r, segs.share.astype(float).to_numpy(), len(st_cust))])
    log(f"  store orders {len(st_idx):,} (identified {int((st_cust >= 0).sum()):,})")

    # online orders
    r = rng("orders", "online")
    lam = dem.daily_counts(r, "online")
    on_day = np.repeat(np.arange(n_days), r.poisson(lam))
    on_cust = np.empty(len(on_day), dtype=np.int64)
    mo = month_of_day[on_day]
    for m in np.unique(mo):
        rows = np.nonzero(mo == m)[0]
        cm = (m % 12) + 1
        segf = pd.Series(cons_seg).map(lambda s: SEG_SEASON.get(s, {}).get(cm, 1.0)).to_numpy()
        alive = (cons_start <= (m + 1) * 30.44) & (cons_end >= m * 30.44)
        w = cons_act * on_share * segf * alive
        on_cust[rows] = weighted_choice(r, w, len(rows))
    on_day = np.maximum(on_day, np.minimum(cons_start[on_cust], n_days - 1))      # never before the account existed
    log(f"  online orders {len(on_day):,}")

    # wholesale orders
    r = rng("orders", "wholesale")
    lam = dem.daily_counts(r, "wholesale")
    wh_day = np.repeat(np.arange(n_days), r.poisson(lam))
    wh_acct = np.empty(len(wh_day), dtype=np.int64)
    cu = pd.DataFrame(c["cuisines"]).set_index("cuisine")
    prof = pd.DataFrame(c["seasonality"]).set_index("profile")
    mcols = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    acc_prof = cu.loc[accounts.cuisine, "seasonality"].to_numpy()
    acc_season = prof.loc[acc_prof, mcols].astype(float).to_numpy()
    acc_w = accounts.x_volume.to_numpy() / accounts.x_cadence.to_numpy()
    a_start, a_end = accounts.x_start.to_numpy(), accounts.x_end.to_numpy()
    mo = month_of_day[wh_day]
    for m in np.unique(mo):
        rows = np.nonzero(mo == m)[0]
        alive = (a_start <= (m + 1) * 30.44) & (a_end >= m * 30.44)
        w = acc_w * acc_season[:, m % 12] ** 0.5 * alive
        wh_acct[rows] = weighted_choice(r, w, len(rows))
    wh_day = np.clip(wh_day, a_start[wh_acct], np.minimum(a_end[wh_acct], n_days - 1))
    log(f"  wholesale orders {len(wh_day):,}")

    O = pd.DataFrame({
        "x_channel": np.concatenate([np.full(len(st_day), "store"), np.full(len(on_day), "online"), np.full(len(wh_day), "wholesale")]),
        "x_day": np.concatenate([st_day, on_day, wh_day]),
        "x_store": np.concatenate([st_idx, np.full(len(on_day), -1), np.full(len(wh_day), -1)]),
        "x_cust": np.concatenate([st_cust, on_cust, np.full(len(wh_day), -1)]),
        "x_acct": np.concatenate([np.full(len(st_day) + len(on_day), -1), wh_acct]),
        "x_seg": np.concatenate([st_seg, cons_seg[on_cust], np.full(len(wh_day), "wholesale")]),
    })
    O["x_ring"] = -1
    if ring:
        rh = pd.DataFrame({"x_channel": "online", "x_day": [x["day"] for x in ring["plan"]], "x_store": -1,
                           "x_cust": [x["cust"] for x in ring["plan"]], "x_acct": -1, "x_seg": "gift_giver",
                           "x_ring": np.arange(len(ring["plan"]))})
        O = pd.concat([O, rh], ignore_index=True)
    n = len(O)

    # ------------------------------------------------------------------ lines ---------------------------
    r = rng("orders", "lines")
    line_order, line_sku, line_qty = [], [], []
    for ch in ("store", "online"):
        sel = np.nonzero((O.x_channel.to_numpy() == ch) & (O.x_ring.to_numpy() < 0))[0]
        k = 1 + r.poisson(C.EXTRA_LINES_MEAN[ch], len(sel))
        keys = pd.DataFrame({"i": sel, "day": O.x_day.to_numpy()[sel], "seg": O.x_seg.to_numpy()[sel], "k": k})
        W = dem.W[ch]
        for (day, sg), g in keys.groupby(["day", "seg"], sort=True):
            tot = int(g.k.sum())
            w = W[sku_prod, day] * seg_aff[sg][sku_prod] * PW[ch]
            picks = weighted_choice(r, w, tot)
            line_order.append(np.repeat(g.i.to_numpy(), g.k.to_numpy()))
            line_sku.append(picks)
        log(f"  {ch} lines drawn")
    q_store = np.array([1, 2, 3]), np.array([.86, .11, .03])
    q_online = np.array([1, 2, 3, 4]), np.array([.72, .20, .05, .03])
    lo = np.concatenate(line_order)
    ls = np.concatenate(line_sku)
    chs = O.x_channel.to_numpy()[lo]
    qty = np.where(chs == "store", q_store[0][weighted_choice(r, q_store[1], len(lo))],
                   q_online[0][weighted_choice(r, q_online[1], len(lo))])
    line_order, line_sku, line_qty = [lo], [ls], [qty]
    if ring:
        for oi in np.nonzero(O.x_ring.to_numpy() >= 0)[0]:
            x = ring["plan"][O.x_ring.iat[oi]]
            line_order.append(np.full(len(x["skus"]), oi))
            line_sku.append(np.array([sku_idx[s_] for s_ in x["skus"]]))
            line_qty.append(np.array(x["qty"]))

    # wholesale baskets from each account's usuals
    Ww = dem.W["wholesale"]
    popv = P["x_pop"].map(C.ZIPF_BY_POPULARITY).to_numpy()
    season_factor = Ww / popv[:, None]
    wsel = np.nonzero(chs_all := (O.x_channel.to_numpy() == "wholesale"))[0]
    acc_ids = accounts.account_id.to_numpy()
    acc_lines = accounts.x_lines.to_numpy()
    acc_vol = accounts.x_volume.to_numpy()
    usual_idx = {a: np.array([sku_idx[s] for s in acc_extra["usuals"][a][0]]) for a in acc_ids}
    usual_w = {a: acc_extra["usuals"][a][1] for a in acc_ids}
    pool_w = PW["wholesale"] * popv[sku_prod]
    lo_w, ls_w, lq_w = [], [], []
    days_w = O.x_day.to_numpy()[wsel]
    accs_w = O.x_acct.to_numpy()[wsel]
    kk = 1 + r.poisson(np.maximum(acc_lines[accs_w] - 1, 0.5))
    extra = r.random(len(wsel)) < 0.08
    bulk = S.pack_code.isin(BULK_PACKS).to_numpy()
    small = S.pack_code.isin({"B08", "B16", "C16", "D16", "E08", "T16", "K32", "R16"}).to_numpy()
    for j, (oi, day, a) in enumerate(zip(wsel, days_w, accs_w)):
        aid = acc_ids[a]
        ui = usual_idx[aid]
        w = usual_w[aid] * season_factor[sku_prod[ui], day] * dem.avail[sku_prod[ui], day]
        if w.sum() <= 0:
            continue
        k = int(min(kk[j], len(ui), int((w > 0).sum())))
        ch_ = r.choice(len(ui), size=k, replace=False, p=w / w.sum())
        picks = ui[ch_]
        if extra[j]:
            picks = np.append(picks, weighted_choice(r, pool_w * Ww[sku_prod, day], 1))
        lo_w.append(np.full(len(picks), oi))
        ls_w.append(picks)
        v = acc_vol[a]
        q = np.where(bulk[picks], 1 + r.poisson(0.25 * v, len(picks)),
                     np.where(small[picks], 1 + r.poisson(2.5 * v, len(picks)), 1 + r.poisson(1.2 * v, len(picks))))
        lq_w.append(q)
    line_order += [np.concatenate(lo_w)]
    line_sku += [np.concatenate(ls_w)]
    line_qty += [np.concatenate(lq_w)]
    log("  wholesale lines drawn")

    Lx = pd.DataFrame({"x_order": np.concatenate(line_order), "x_sku": np.concatenate(line_sku),
                       "quantity": np.concatenate(line_qty).astype(np.int64)})
    Lx = Lx.groupby(["x_order", "x_sku"], as_index=False, sort=True)["quantity"].sum()   # merge duplicate picks
    O = O.loc[np.unique(Lx.x_order)].copy()     # every order has at least one line

    # ------------------------------------------------------------------ timestamps ----------------------
    r = rng("orders", "time")
    tz = np.empty(len(O), dtype=object)
    ch = O.x_channel.to_numpy()
    tz[ch == "store"] = stores.time_zone.to_numpy()[O.x_store.to_numpy()[ch == "store"]]
    cust_region = consumers.x_region.to_numpy()
    tz[ch == "online"] = [REGION_TZ[x] for x in cust_region[O.x_cust.to_numpy()[ch == "online"]]]
    tz[ch == "wholesale"] = [REGION_TZ[x] for x in accounts.region_id.to_numpy()[O.x_acct.to_numpy()[ch == "wholesale"]]]
    hour = np.empty(len(O), dtype=np.int64)
    for c_ in ("store", "online", "wholesale"):
        m = ch == c_
        hour[m] = weighted_choice(r, np.array(C.HOURS[c_], float), int(m.sum()))
    secs = hour * 3600 + r.integers(0, 3600, len(O))
    local = pd.Timestamp(C.START) + pd.to_timedelta(O.x_day.to_numpy(), "D") + pd.to_timedelta(secs, "s")
    utc = pd.Series(pd.NaT, index=O.index, dtype="datetime64[ns, UTC]")
    for z in np.unique(tz):
        m = tz == z
        utc[m] = pd.DatetimeIndex(local[m]).tz_localize(z, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC")
    utc = utc.fillna(pd.Series(pd.DatetimeIndex(local).tz_localize("UTC"), index=O.index) + pd.Timedelta(hours=5))
    late = utc >= pd.Timestamp(C.NOW_UTC)
    utc[late] = pd.Timestamp(C.NOW_UTC) - pd.to_timedelta(r.integers(60, 4 * 3600, int(late.sum())), "s")
    O["ordered_at"] = utc.dt.floor("s")
    O = O.sort_values(["ordered_at", "x_channel"], kind="stable")
    order_pos = {old: new for new, old in enumerate(O.index)}
    O = O.reset_index(drop=True)
    Lx["x_order"] = Lx.x_order.map(order_pos)
    Lx = Lx.sort_values(["x_order", "x_sku"], kind="stable").reset_index(drop=True)
    Lx["line_no"] = Lx.groupby("x_order").cumcount() + 1

    # ------------------------------------------------------------------ ids, places, people -------------
    r = rng("orders", "ids")
    n = len(O)
    O["order_id"] = uuids(r, n)
    ch = O.x_channel.to_numpy()
    yy = O.ordered_at.dt.tz_convert("America/New_York").dt.year.to_numpy()
    num = np.zeros(n, dtype=np.int64)
    for c_, pre in (("store", "S"), ("online", "O"), ("wholesale", "W")):
        for y in np.unique(yy):
            m = (ch == c_) & (yy == y)
            num[m] = np.arange(1, m.sum() + 1) + int(r.integers(10_000, 90_000))
    prefix = pd.Series(ch).map({"store": "S", "online": "O", "wholesale": "W"}).to_numpy()
    O["order_number"] = [f"{p}-{y % 100:02d}-{k:07d}" for p, y, k in zip(prefix, yy, num)]
    O["channel"] = ch
    cust = O.x_cust.to_numpy()
    acct = O.x_acct.to_numpy()
    O["customer_id"] = np.where(cust >= 0, consumers.customer_id.to_numpy()[np.maximum(cust, 0)], None)
    O["account_id"] = np.where(acct >= 0, accounts.account_id.to_numpy()[np.maximum(acct, 0)], None)
    cons_dc = consumers.serving_dc_id.to_numpy()
    acc_dc = accounts.serving_dc_id.to_numpy()
    store_dc = stores.region_id.map(lambda x: f"DC-{x}").to_numpy()
    O["x_dc"] = np.where(ch == "store", store_dc[np.maximum(O.x_store.to_numpy(), 0)],
                         np.where(ch == "online", cons_dc[np.maximum(cust, 0)], acc_dc[np.maximum(acct, 0)]))
    O["location_id"] = np.where(ch == "store", store_ids[np.maximum(O.x_store.to_numpy(), 0)], O["x_dc"])
    for col, src_c, src_a in (("ship_city", "city", "city"), ("ship_state", "state", "state"), ("ship_postal_code", "postal_code", "postal_code")):
        O[col] = np.where(ch == "online", consumers[src_c].to_numpy()[np.maximum(cust, 0)],
                          np.where(ch == "wholesale", accounts[src_a].to_numpy()[np.maximum(acct, 0)], None))
    street = np.where(ch == "online", consumers.street_address.to_numpy()[np.maximum(cust, 0)],
                      np.where(ch == "wholesale", accounts.street_address.to_numpy()[np.maximum(acct, 0)], None))
    O["ship_address_hash"] = [stable_hash(f"{s}|{z}") if s is not None else None for s, z in zip(street, O.ship_postal_code)]

    # ------------------------------------------------------------------ prices --------------------------
    periods = np.array([np.datetime64(d) for d in cat["price_periods"]])
    od = (pd.Timestamp(C.START) + pd.to_timedelta(O.x_day.to_numpy(), "D")).to_numpy().astype("datetime64[D]")
    per = np.searchsorted(periods, od, side="right") - 1
    l_ord = Lx.x_order.to_numpy()
    l_sku = Lx.x_sku.to_numpy()
    l_ch = ch[l_ord]
    l_per = per[l_ord]
    retail = cat["price_matrix"]["retail"][l_sku, l_per]
    whl = cat["price_matrix"]["wholesale"][l_sku, l_per]
    tiers = accounts.price_tier.map(C.TIER_DISCOUNT).to_numpy()
    tier_p = np.round(whl * (1 - tiers[np.maximum(acct[l_ord], 0)]), 2)
    price = np.where(l_ch == "wholesale", tier_p, retail)
    source = np.where(l_ch == "wholesale", "tier", "list").astype(object)
    # contract prices
    wl = np.nonzero(l_ch == "wholesale")[0]
    cp = contract_prices.copy()
    q = pd.DataFrame({"row": wl, "account_id": O.account_id.to_numpy()[l_ord[wl]], "sku_id": S.sku_id.to_numpy()[l_sku[wl]],
                      "d": od[l_ord[wl]]})
    mrg = q.merge(cp, on=["account_id", "sku_id"], how="inner")
    mrg = mrg[(mrg.d >= pd.to_datetime(mrg.effective_from)) & (mrg.d <= pd.to_datetime(mrg.effective_to))]
    price[mrg.row.to_numpy()] = mrg.contract_price.to_numpy()
    source[mrg.row.to_numpy()] = "contract"
    Lx["unit_price"] = np.round(price, 2)
    Lx["price_source"] = source
    Lx["extended_price"] = np.round(Lx.unit_price * Lx.quantity, 2)
    Lx["order_id"] = O.order_id.to_numpy()[l_ord]
    Lx["sku_id"] = S.sku_id.to_numpy()[l_sku]
    Lx["line_status"] = "fulfilled"
    Lx["substituted_from_sku"] = None
    Lx["return_reason"] = None
    Lx["batch_id"] = None

    # ------------------------------------------------------------------ totals, status, payment ----------
    r = rng("orders", "money")
    sub = np.bincount(l_ord, weights=Lx.extended_price.to_numpy(), minlength=n)
    promo = (ch == "online") & (r.random(n) < C.ONLINE_PROMO_SHARE) & (O.x_ring.to_numpy() < 0)
    pct = np.array([0.10, 0.15, 0.20])[r.integers(0, 3, n)]
    codes = np.array(["WELCOME10", "SPICE15", "HOLIDAY20", "GRILL15", "BAKE10", "FALL15"])[r.integers(0, 6, n)]
    O["subtotal"] = np.round(sub, 2)
    O["discount_total"] = np.where(promo, np.round(sub * pct, 2), 0.0)
    O["promo_code"] = np.where(promo, codes, None)
    net = O.subtotal - O.discount_total
    O["shipping_total"] = np.where((ch == "online") & (net < C.FREE_SHIPPING_OVER), C.SHIPPING_FEE, 0.0)
    st_state = stores.state.to_numpy()[np.maximum(O.x_store.to_numpy(), 0)]
    tax_state = np.where(ch == "store", st_state, O.ship_state.to_numpy())
    rate = np.array([C.SALES_TAX.get(s, 0.0) if s is not None else 0.0 for s in tax_state])
    O["tax_total"] = np.where(ch == "wholesale", 0.0, np.round(net * rate, 2))
    O["order_total"] = np.round(net + O.shipping_total + O.tax_total, 2)
    age_days = (C.NOW_UTC - O.ordered_at).dt.days.to_numpy()
    status = np.where(ch == "store", "completed", np.where(age_days > 5, "delivered", "shipped")).astype(object)
    u = r.random(n)
    status[(ch == "online") & (u < 0.015)] = "cancelled"
    status[(ch == "online") & (u >= 0.015) & (u < 0.027) & (age_days > 10)] = "returned"
    status[(ch == "store") & (u < 0.008)] = "returned"
    status[(ch == "wholesale") & (u < 0.005)] = "cancelled"
    O["status"] = status
    pm = np.where(ch == "wholesale", "invoice",
                  np.where(ch == "online", np.where(r.random(n) < 0.97, "card", "gift_card"),
                           np.array(["card", "cash", "gift_card"])[weighted_choice(r, np.array([.85, .12, .03]), n)])).astype(object)
    O["payment_method"] = pm
    card_no = r.integers(0, 2, n)
    fp_key = np.where(cust >= 0, O.customer_id.astype(str), O.order_id.astype(str))
    O["payment_fingerprint"] = [stable_hash(f"card|{k}|{c_}") if p == "card" else None for k, c_, p in zip(fp_key, card_no, pm)]
    if ring:
        rr = np.nonzero(O.x_ring.to_numpy() >= 0)[0]
        O.loc[rr, "payment_method"] = "card"
        O.loc[rr, "payment_fingerprint"] = [ring["plan"][k]["fingerprint"] for k in O.x_ring.to_numpy()[rr]]
        O.loc[rr, "promo_code"] = None
        O.loc[rr, "device"] = "web"
    O["device"] = np.where(ch == "online", np.where(r.random(n) < 0.55, "web", "app"), None)
    O["risk_score"] = None
    O["risk_flags"] = None
    O["risk_review"] = None
    ret = np.nonzero(np.isin(O.status.to_numpy()[l_ord], ["returned"]))[0]
    Lx.loc[ret, "line_status"] = "returned"
    Lx.loc[ret, "return_reason"] = np.array(["changed my mind", "damaged in transit", "wrong item", "not as expected",
                                             "arrived stale", "too spicy"])[r.integers(0, 6, len(ret))]
    can = np.nonzero(O.status.to_numpy()[l_ord] == "cancelled")[0]
    Lx.loc[can, "line_status"] = "cancelled"
    Lx["x_day"] = O.x_day.to_numpy()[l_ord]
    Lx["x_dc"] = O.x_dc.to_numpy()[l_ord]
    Lx["x_channel"] = l_ch
    return O, Lx
