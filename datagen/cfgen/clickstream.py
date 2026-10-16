"""GA4 export-shaped clickstream for calendar 2026: one Parquet file per day, events_YYYYMMDD.parquet, which loads into
BigQuery as the daily tables events_YYYYMMDD exactly as a GA4 export writes them (query them with events_* and _TABLE_SUFFIX).

Every 2026 online order has a converting session with a purchase event (transaction_id = order_number), except
GA4_PURCHASE_LOSS of them, as in real GA4 data. Views keep following demand for items that are out of stock or
sold out, which is what Module 4's "viewed but can't ship" join finds.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import config as C
from .demand import Demand
from .util import log, rng, stable_hash, weighted_choice

SITE = "https://shop.cymbalfoods.example"
DEVICES = [("mobile", "iOS", "Safari", .32), ("mobile", "Android", "Chrome", .26), ("desktop", "Windows", "Chrome", .17),
           ("desktop", "Macintosh", "Safari", .11), ("desktop", "Windows", "Edge", .05), ("desktop", "Macintosh", "Chrome", .03),
           ("tablet", "iOS", "Safari", .06)]
TRAFFIC = [("(organic)", "organic", "google", .30), ("(direct)", "(none)", "(direct)", .24), ("spring_promo", "email", "newsletter", .10),
           ("brand_search", "cpc", "google", .14), ("holiday_social", "social", "instagram", .08), ("recipes", "referral", "partner_blog", .06),
           ("(organic)", "organic", "bing", .04), ("shopping", "cpc", "google", .04)]
GENERIC_SEARCH = ["smoky", "cinnamon", "chili powder", "chile powder", "saffron", "vanilla beans", "gift set", "taco seasoning",
                  "everything seasoning", "brisket rub", "salt free", "paprika", "curry powder", "garam masala", "pumpkin spice",
                  "black pepper", "chai", "hot sauce", "olive oil", "balsamic", "za'atar", "smoked paprika", "cardamom", "ghost pepper",
                  "something smoky for brisket", "not too spicy", "holiday baking", "stocking stuffer", "korean chili flakes", "sumac"]

EP_TYPE = pa.list_(pa.struct([("key", pa.string()), ("value", pa.struct([("string_value", pa.string()),
                                                                           ("int_value", pa.int64()), ("double_value", pa.float64())]))]))
ITEM_TYPE = pa.list_(pa.struct([("item_id", pa.string()), ("item_name", pa.string()), ("item_category", pa.string()),
                                ("price", pa.float64()), ("quantity", pa.int64())]))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build(cat: dict, consumers: pd.DataFrame, O: pd.DataFrame, Lx: pd.DataFrame, dem: Demand, out_dir: Path) -> dict:
    P, S = cat["products"], cat["skus"].reset_index(drop=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    pid_idx = {p: i for i, p in enumerate(P.product_id)}
    sku_prod = S.product_id.map(pid_idx).to_numpy()
    sku_name = S.product_id.map(P.set_index("product_id").name).to_numpy() + " - " + S.pack_description.to_numpy()
    sku_cat = S.product_id.map(P.set_index("product_id").category_id).to_numpy()
    sku_price = cat["price_matrix"]["retail"][:, -1]
    online_skus = np.nonzero(S.sold_online.to_numpy())[0]
    pref = S.pack_code.map(C.CHANNEL_PACK_PREF["online"]).fillna(0.03).to_numpy()[online_skus]
    prod_path = np.array([f"{SITE}/products/{p.lower()}-{_slug(n)}" for p, n in zip(P.product_id, P.name)], dtype=object)
    so = np.nonzero(((P.x_slug == C.PLANT["sellout_gift"]) & (P.x_variant == str(C.PLANT["sellout_edition"]))).to_numpy())[0]
    so_day = (C.PLANT["sellout_date"] - C.START).days
    search_vocab = np.array(GENERIC_SEARCH + [n.lower().split(",")[0] for n in P.name[P.x_pop >= 4]], dtype=object)

    first_day = (C.GA4_START - C.START).days
    days = np.arange(first_day, dem.n_days)
    vol = dem.vol["online"][days]
    n_sess_total = int(C.GA4_SESSIONS_2026 * C.SCALE)
    lam = vol / vol.sum() * n_sess_total
    r = rng("ga4", "plan")
    n_sess = r.poisson(lam)

    # online orders in 2026 that GA4 recorded
    on = O[(O.channel == "online") & (O.x_day >= first_day)].copy()
    on["lost"] = r.random(len(on)) < C.GA4_PURCHASE_LOSS
    lines = Lx[Lx.x_order.isin(on.index)]
    lines_by_order = {k: g for k, g in lines.groupby("x_order")}
    cust_ok = (consumers.x_end.to_numpy() >= first_day) & (consumers.x_activity.to_numpy() > 0)
    cust_w = consumers.x_activity.to_numpy() * cust_ok
    city_tab = consumers[["city", "state"]].to_numpy()
    anon_pool = int(1_500_000 * C.SCALE)
    stats = dict(events=0, sessions=0, purchases=0, lost_purchases=int(on.lost.sum()), days=0)
    dw = np.array([d[3] for d in DEVICES])
    tw = np.array([t[3] for t in TRAFFIC])

    for d in days:
        r = rng("ga4", int(d))
        date = C.START + dt.timedelta(int(d))
        conv = on[(on.x_day == d) & ~on.lost]
        ns = max(int(n_sess[d - first_day]), len(conv) + 1)
        is_conv = np.zeros(ns, dtype=bool)
        is_conv[: len(conv)] = True
        signed = is_conv.copy()
        signed[len(conv):] = r.random(ns - len(conv)) < 0.40
        cust = np.full(ns, -1)
        cust[: len(conv)] = conv.x_cust.to_numpy()
        k = int(signed[len(conv):].sum())
        cust[len(conv):][signed[len(conv):]] = weighted_choice(r, cust_w, k)
        guest = np.zeros(ns, dtype=bool)
        guest[cust >= 0] = consumers.account_type.to_numpy()[cust[cust >= 0]] == "guest"
        anon_id = r.zipf(1.3, ns) % anon_pool
        pseudo = np.array([f"{int(stable_hash(f'c{c}', 9), 16) % 10**9}.{1600000000 + (c % 9_000_000)}" if c >= 0 else
                           f"{int(stable_hash(f'a{a}', 9), 16) % 10**9}.{1650000000 + int(a)}" for c, a in zip(cust, anon_id)], dtype=object)
        user_id = np.where((cust >= 0) & ~guest, consumers.customer_number.to_numpy()[np.maximum(cust, 0)], None)
        dev = weighted_choice(r, dw, ns)
        trf = weighted_choice(r, tw, ns)
        hrs = weighted_choice(r, np.array(C.HOURS["online"], float), ns)
        start = (pd.Timestamp(date).value // 1000 + (hrs * 3600 + r.integers(0, 3600, ns)) * 1_000_000 + 5 * 3600 * 1_000_000)
        if len(conv):
            ous = ((conv.ordered_at - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(microseconds=1)).to_numpy()
            start[: len(conv)] = ous - r.integers(120, 1500, len(conv)) * 1_000_000
        sid = r.integers(1_600_000_000, 2_000_000_000, ns)
        geo_i = np.where(cust >= 0, cust, r.integers(0, len(consumers), ns))
        city, state = city_tab[geo_i, 0], city_tab[geo_i, 1]

        # what gets viewed today
        wv = dem.W["online"][:, d].copy()
        if d >= so_day and len(so):
            wv[so] = dem.W["online"][so, so_day - 1] * (1.0 + 0.04 * (d - so_day))
        item_w = wv[sku_prod[online_skus]] * pref

        ev_s, ev_name, ev_items_off, ev_search, ev_page = [], [], [], [], []
        items_sku, items_qty = [], []

        def add(sess, name, item_lists=None, search=None, page=None):
            ev_s.append(sess)
            ev_name.append(np.full(len(sess), name, dtype=object))
            ev_search.append(search if search is not None else np.full(len(sess), None, dtype=object))
            ev_page.append(page if page is not None else np.full(len(sess), SITE + "/", dtype=object))
            if item_lists is None:
                ev_items_off.append(np.zeros(len(sess), dtype=np.int64))
            else:
                cnt, skus, qtys = item_lists
                ev_items_off.append(cnt)
                items_sku.append(skus)
                items_qty.append(qtys)

        allsess = np.arange(ns)
        add(allsess, "session_start")
        npv = 1 + r.poisson(0.8, ns)
        add(np.repeat(allsess, npv), "page_view")
        srch = np.nonzero(r.random(ns) < 0.25)[0]
        add(srch, "view_search_results", search=search_vocab[r.integers(0, len(search_vocab), len(srch))],
            page=np.array([f"{SITE}/search" for _ in srch], dtype=object))
        lst = np.nonzero(r.random(ns) < 0.6)[0]
        add(lst, "view_item_list", page=np.full(len(lst), f"{SITE}/collections", dtype=object))
        nv = r.poisson(1.0, ns) + 1
        vs = np.repeat(allsess, nv)
        vsku = online_skus[weighted_choice(r, item_w, len(vs))]
        add(vs, "view_item", (np.ones(len(vs), dtype=np.int64), vsku, np.ones(len(vs), dtype=np.int64)),
            page=prod_path[sku_prod[vsku]])
        atc = np.nonzero((~is_conv) & (r.random(ns) < 0.14))[0]
        first_view = {}
        for s_, k_ in zip(vs[::-1], vsku[::-1]):
            first_view[s_] = k_
        asku = np.array([first_view[s_] for s_ in atc], dtype=np.int64)
        add(atc, "add_to_cart", (np.ones(len(atc), dtype=np.int64), asku, np.ones(len(atc), dtype=np.int64)))
        bc = atc[r.random(len(atc)) < 0.3]
        add(bc, "begin_checkout", (np.ones(len(bc), dtype=np.int64), np.array([first_view[s_] for s_ in bc], dtype=np.int64),
                                   np.ones(len(bc), dtype=np.int64)))
        # converting sessions: view, cart and buy the order's lines
        if len(conv):
            cs, cnt, ck, cq = [], [], [], []
            for j, oi in enumerate(conv.index):
                g = lines_by_order.get(oi)
                if g is None:
                    continue
                cs.append(j)
                cnt.append(len(g))
                ck.append(g.x_sku.to_numpy())
                cq.append(g.quantity.to_numpy())
            cs = np.array(cs, dtype=np.int64)
            cnt = np.array(cnt, dtype=np.int64)
            ck_all = np.concatenate(ck) if ck else np.zeros(0, dtype=np.int64)
            cq_all = np.concatenate(cq) if cq else np.zeros(0, dtype=np.int64)
            rep_s = np.repeat(cs, cnt)
            add(rep_s, "view_item", (np.ones(len(rep_s), dtype=np.int64), ck_all, np.ones(len(rep_s), dtype=np.int64)),
                page=prod_path[sku_prod[ck_all]])
            add(rep_s, "add_to_cart", (np.ones(len(rep_s), dtype=np.int64), ck_all, cq_all))
            for nm in ("begin_checkout", "add_shipping_info", "add_payment_info", "purchase"):
                add(cs, nm, (cnt, ck_all, cq_all), page=np.full(len(cs), f"{SITE}/checkout", dtype=object))
            conv_orders = conv.index.to_numpy()[cs]
        else:
            conv_orders = np.zeros(0, dtype=np.int64)

        order_rank = {"session_start": 0, "page_view": 1, "view_search_results": 2, "view_item_list": 3, "view_item": 4,
                      "add_to_cart": 5, "begin_checkout": 6, "add_shipping_info": 7, "add_payment_info": 8, "purchase": 9}
        s_all = np.concatenate(ev_s)
        n_all = np.concatenate(ev_name)
        srch_all = np.concatenate(ev_search)
        page_all = np.concatenate(ev_page)
        ic = np.concatenate(ev_items_off)
        # items: per event, a slice of the flat item arrays in the same order events were added
        isku = np.concatenate(items_sku) if items_sku else np.zeros(0, dtype=np.int64)
        iqty = np.concatenate(items_qty) if items_qty else np.zeros(0, dtype=np.int64)
        item_start = np.concatenate([[0], np.cumsum(ic)[:-1]])
        rank = pd.Series(n_all).map(order_rank).to_numpy()
        order = np.lexsort((np.arange(len(s_all)), rank, s_all))
        s_all, n_all, srch_all, page_all, ic, item_start, rank = (x[order] for x in (s_all, n_all, srch_all, page_all, ic, item_start, rank))
        step = r.integers(2, 90, len(s_all)) * 1_000_000
        seq_in_sess = pd.Series(s_all).groupby(s_all).cumcount().to_numpy()
        csum = pd.Series(step).groupby(s_all).cumsum().to_numpy() - step
        ts = start[s_all] + np.where(seq_in_sess == 0, 0, csum)
        # a GA4 daily table holds only its own day (property time zone: US Eastern)
        lo = pd.Timestamp(date).tz_localize("America/New_York").value // 1000
        hi = (pd.Timestamp(date) + pd.Timedelta(days=1)).tz_localize("America/New_York").value // 1000 - 1
        ts = np.clip(ts, lo, hi)
        # flat item arrays reordered to follow the events
        idx = np.concatenate([np.arange(a, a + b) for a, b in zip(item_start, ic)]) if ic.sum() else np.zeros(0, dtype=np.int64)
        isku_o, iqty_o = isku[idx], iqty[idx]
        offs = np.concatenate([[0], np.cumsum(ic)]).astype(np.int32)
        items = pa.ListArray.from_arrays(pa.array(offs), pa.StructArray.from_arrays(
            [pa.array(S.sku_id.to_numpy()[isku_o].astype(str)), pa.array(sku_name[isku_o].astype(str)),
             pa.array(sku_cat[isku_o].astype(str)), pa.array(np.round(sku_price[isku_o], 2)), pa.array(iqty_o.astype(np.int64))],
            fields=list(ITEM_TYPE.value_type)))
        # event_params: ga_session_id, page_location, and search_term when present
        has_s = pd.notna(srch_all)
        npar = 2 + has_s.astype(np.int32)
        poffs = np.concatenate([[0], np.cumsum(npar)]).astype(np.int32)
        m = len(s_all)
        keys = np.empty(int(npar.sum()), dtype=object)
        sval = np.empty(int(npar.sum()), dtype=object)
        ival = np.full(int(npar.sum()), None, dtype=object)
        keys[poffs[:-1]] = "ga_session_id"
        ival[poffs[:-1]] = sid[s_all]
        sval[poffs[:-1]] = None
        keys[poffs[:-1] + 1] = "page_location"
        sval[poffs[:-1] + 1] = page_all
        sp = poffs[:-1][has_s] + 2
        keys[sp] = "search_term"
        sval[sp] = srch_all[has_s]
        params = pa.ListArray.from_arrays(pa.array(poffs), pa.StructArray.from_arrays(
            [pa.array(keys.astype(str)), pa.StructArray.from_arrays(
                [pa.array(sval, type=pa.string()), pa.array(ival, type=pa.int64()), pa.array(np.full(len(keys), None), type=pa.float64())],
                fields=list(EP_TYPE.value_type[1].type))],
            fields=list(EP_TYPE.value_type)))
        # ecommerce on purchase events
        is_p = n_all == "purchase"
        # purchase sessions are 0..len(conv)-1 but only those with lines are in cs order; map session -> order row
        sess_order = {}
        if len(conv):
            for j, oi in enumerate(conv.index):
                sess_order[j] = oi
        tid = np.array([O.order_number.iat[sess_order[s_]] if p else None for s_, p in zip(s_all, is_p)], dtype=object)
        rev = np.array([float(O.subtotal.iat[sess_order[s_]] - O.discount_total.iat[sess_order[s_]]) if p else None
                        for s_, p in zip(s_all, is_p)], dtype=object)
        shp = np.array([float(O.shipping_total.iat[sess_order[s_]]) if p else None for s_, p in zip(s_all, is_p)], dtype=object)
        tax = np.array([float(O.tax_total.iat[sess_order[s_]]) if p else None for s_, p in zip(s_all, is_p)], dtype=object)
        tq = np.where(is_p, ic, 0)
        ecommerce = pa.StructArray.from_arrays(
            [pa.array(tid, type=pa.string()), pa.array(rev, type=pa.float64()), pa.array(shp, type=pa.float64()),
             pa.array(tax, type=pa.float64()), pa.array(np.where(is_p, tq, None), type=pa.int64())],
            names=["transaction_id", "purchase_revenue", "shipping_value", "tax_value", "total_item_quantity"],
            mask=pa.array(~is_p))
        dv = dev[s_all]
        tf = trf[s_all]
        local = pd.to_datetime(ts, unit="us", utc=True).tz_convert("America/New_York")
        tbl = pa.table({
            "event_date": pa.array(local.strftime("%Y%m%d").to_numpy().astype(str)),
            "event_timestamp": pa.array(ts.astype(np.int64)),
            "event_name": pa.array(n_all.astype(str)),
            "event_params": params,
            "user_id": pa.array(user_id[s_all], type=pa.string()),
            "user_pseudo_id": pa.array(pseudo[s_all].astype(str)),
            "device": pa.StructArray.from_arrays(
                [pa.array([DEVICES[i][0] for i in dv]), pa.array([DEVICES[i][1] for i in dv]),
                 pa.StructArray.from_arrays([pa.array([DEVICES[i][2] for i in dv])], names=["browser"])],
                names=["category", "operating_system", "web_info"]),
            "geo": pa.StructArray.from_arrays([pa.array(np.full(m, "United States")), pa.array(state[s_all].astype(str)),
                                               pa.array(city[s_all].astype(str))], names=["country", "region", "city"]),
            "traffic_source": pa.StructArray.from_arrays([pa.array([TRAFFIC[i][0] for i in tf]), pa.array([TRAFFIC[i][1] for i in tf]),
                                                          pa.array([TRAFFIC[i][2] for i in tf])], names=["name", "medium", "source"]),
            "ecommerce": ecommerce,
            "items": items,
            "platform": pa.array(np.full(m, "WEB")),
        })
        pq.write_table(tbl, out_dir / f"events_{date:%Y%m%d}.parquet", compression="zstd", row_group_size=200_000)
        stats["events"] += m
        stats["sessions"] += ns
        stats["purchases"] += int(is_p.sum())
        stats["days"] += 1
        if date.day == 1:
            log(f"  ga4 {date:%b}: {stats['events']:,} events so far")
    return stats
