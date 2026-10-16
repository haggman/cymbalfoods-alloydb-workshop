"""Places and people: locations, staff, consumers, restaurant accounts, their usual baskets and contract prices."""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

from . import config as C
from .content import pairs, split
from .util import rng, stable_hash, uuids, weighted_choice

STREETS = ["Maple", "Cedar", "Elm", "Willow", "Juniper", "Harbor", "Mill", "Lake", "Ridge", "Prospect", "Union", "Bridge",
           "Church", "Market", "Spring", "Grove", "Orchard", "Highland", "Sunset", "Park", "Hillside", "Meadow", "River",
           "Forest", "Chestnut", "Walnut", "Magnolia", "Laurel", "Franklin", "Jefferson", "Madison", "Monroe", "Adams",
           "Front", "Water", "Railroad", "Depot", "Canal", "Bay", "Shore", "Pine", "Oak", "Ash", "Birch", "Aspen",
           "Sycamore", "Hawthorn", "Linden", "Poplar", "Holly", "Fern", "Beacon", "Garden", "Quarry", "Kettle", "Foundry"]
SUFFIX = ["St", "Ave", "Rd", "Blvd", "Ln", "Dr", "Way", "Pl", "Ct", "Ter"]


class Names:
    def __init__(self, c: dict):
        self.last = np.array([x["name"] for x in c["names_surnames"]], dtype=object)
        self.lw = np.array([float(x["frequency_pct"]) for x in c["names_surnames"]])
        self.f = np.array([x["name"] for x in c["names_female"]], dtype=object)
        self.fw = np.array([float(x["frequency_pct"]) for x in c["names_female"]])
        self.m = np.array([x["name"] for x in c["names_male"]], dtype=object)
        self.mw = np.array([float(x["frequency_pct"]) for x in c["names_male"]])
        # flatten the 1990 frequencies a little so the long tail shows up
        self.lw, self.fw, self.mw = self.lw ** 0.6, self.fw ** 0.6, self.mw ** 0.6

    def people(self, r: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
        fem = r.random(n) < 0.52
        first = np.where(fem, self.f[weighted_choice(r, self.fw, n)], self.m[weighted_choice(r, self.mw, n)])
        last = self.last[weighted_choice(r, self.lw, n)]
        return first, last


def addresses(r: np.random.Generator, n: int, zip3: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    num = r.integers(10, 9900, n)
    st = r.integers(0, len(STREETS), n)
    sf = r.integers(0, len(SUFFIX), n)
    street = np.array([f"{a} {STREETS[b]} {SUFFIX[c_]}" for a, b, c_ in zip(num, st, sf)], dtype=object)
    z = np.array([f"{z3}{d:02d}" for z3, d in zip(zip3, r.integers(1, 99, n))], dtype=object)
    return street, z


def phones(r: np.random.Generator, area: np.ndarray) -> np.ndarray:
    """NXX-555-01XX (the range reserved for fiction), in one of three legacy formats for a few rows."""
    nn = r.integers(0, 100, len(area))
    fmt = r.random(len(area))
    out = []
    for a, x, f in zip(area, nn, fmt):
        if f < 0.97:
            out.append(f"{a}-555-01{x:02d}")
        elif f < 0.985:
            out.append(f"({a}) 555-01{x:02d}")
        else:
            out.append(f"{a}.555.01{x:02d}")
    return np.array(out, dtype=object)


def build_places(c: dict, names: Names) -> dict[str, pd.DataFrame]:
    r = rng("places")
    L = pd.DataFrame(c["locations"])
    cities = pd.DataFrame(c["us_cities"])
    zmap = {(x.city, x.state): x.zip3 for x in cities.itertuples()}
    street, z = addresses(r, len(L), np.array([zmap[(a, b)] if (a, b) in zmap else
                                                {"Edison": "088", "McDonough": "302", "Ontario": "917", "Houston": "770"}[a]
                                                for a, b in zip(L.city, L.state)]))
    L["street_address"] = street
    L["postal_code"] = z
    L["opened_on"] = [dt.date(int(y), 3 if s == "ST-027" else 8 if s == "ST-040" else 1 + int(stable_hash(s, 2), 16) % 12, 1)
                      for s, y in zip(L.location_id, L.opened_year)]
    L["latitude"] = L["latitude"].astype(float).round(5)
    L["longitude"] = L["longitude"].astype(float).round(5)
    L["sq_ft"] = L["sq_ft"].astype(int)
    locations = L[["location_id", "location_type", "name", "region_id", "street_address", "city", "state", "postal_code",
                   "latitude", "longitude", "time_zone", "opened_on", "sq_ft"]].copy()
    regions = pd.DataFrame([("NE", "Northeast", "DC-NE"), ("SE", "Southeast", "DC-SE"), ("CE", "Central", "DC-CE"),
                            ("WE", "West", "DC-WE")], columns=["region_id", "name", "dc_location_id"])

    # staff
    rows = []
    for s in L[L.location_type == "store"].itertuples():
        rows += [("store_manager", s.location_id, s.region_id)] + [("assistant_manager", s.location_id, s.region_id)] * 2
    for reg in ["NE", "SE", "CE", "WE"]:
        dc = f"DC-{reg}"
        rows += [("regional_manager", dc, reg), ("dc_manager", dc, reg), ("buyer", dc, reg)]
        rows += [("quality_analyst", dc, reg)] * 2
        rows += [("account_rep", dc, reg)] * C.N_STAFF_REPS_PER_REGION
    rows += [("merchandiser", "DC-NE", None)] * 4 + [("quality_analyst", "DC-NE", None)]
    first, last = names.people(r, len(rows))
    staff = pd.DataFrame(rows, columns=["role", "location_id", "region_id"])
    staff.insert(0, "staff_id", [f"E{10001 + i}" for i in range(len(staff))])
    staff.insert(1, "full_name", [f"{a} {b}" for a, b in zip(first, last)])
    staff.insert(2, "email", [f"{a.lower()}.{re.sub('[^a-z]', '', b.lower())}{i % 7 or ''}@cymbalfoods.example"
                              for i, (a, b) in enumerate(zip(first, last))])
    staff["hired_on"] = [dt.date(int(y), int(m), 1) for y, m in zip(r.integers(1996, 2026, len(staff)), r.integers(1, 13, len(staff)))]
    return dict(regions=regions, locations=locations, staff=staff, cities=cities)


def build_consumers(c: dict, names: Names, places: dict) -> pd.DataFrame:
    r = rng("consumers")
    n = int(round(C.N_CONSUMERS * C.SCALE))
    cities = places["cities"].copy()
    cities["weight"] = cities["weight"].astype(float)
    stores = places["locations"][places["locations"].location_type == "store"]
    store_cities = cities[cities.store_metro == "Y"].reset_index(drop=True)
    other = cities[cities.store_metro == "N"].reset_index(drop=True)
    online_only = r.random(n) < C.ONLINE_ONLY_SHARE
    ci_s = weighted_choice(r, store_cities.weight.to_numpy(), n)
    ci_o = weighted_choice(r, other.weight.to_numpy(), n)
    city = np.where(online_only, other.city.to_numpy()[ci_o], store_cities.city.to_numpy()[ci_s])
    state = np.where(online_only, other.state.to_numpy()[ci_o], store_cities.state.to_numpy()[ci_s])
    region = np.where(online_only, other.region_id.to_numpy()[ci_o], store_cities.region_id.to_numpy()[ci_s])
    zip3 = np.where(online_only, other.zip3.to_numpy()[ci_o], store_cities.zip3.to_numpy()[ci_s])
    area = np.where(online_only, other.area_code.to_numpy()[ci_o], store_cities.area_code.to_numpy()[ci_s])
    # home store: a store in the same city, else none
    by_city = {}
    for s in stores.itertuples():
        by_city.setdefault((s.city, s.state), []).append((s.location_id, s.sq_ft))
    home = np.empty(n, dtype=object)
    pick = r.random(n)
    for i in range(n):
        opts = None if online_only[i] else by_city.get((city[i], state[i]))
        if not opts:
            home[i] = None
            continue
        w = np.array([o[1] for o in opts], float)
        home[i] = opts[int(np.searchsorted(np.cumsum(w / w.sum()), pick[i]))] [0] if len(opts) > 1 else opts[0][0]
    home[r.random(n) < 0.002] = None                     # a few records missing a home store (dirty edge)
    segs = pd.DataFrame(c["consumer_segments"])
    seg = segs.segment.to_numpy()[weighted_choice(r, segs.share.astype(float).to_numpy(), n)]
    first, last = names.people(r, n)
    street, z = addresses(r, n, zip3)
    guest = r.random(n) < C.GUEST_SHARE
    loy = np.array(list(C.LOYALTY))[weighted_choice(r, np.array(list(C.LOYALTY.values())), n)]
    loy = np.where(guest, "none", loy)
    dom = np.array(C.EMAIL_DOMAINS)[r.integers(0, 3, n)]
    num = r.integers(0, 1000, n)
    email = np.array([f"{a.lower()}.{re.sub('[^a-z]', '', b.lower())}{k if k > 99 else ''}@{d}"
                      for a, b, k, d in zip(first, last, num, dom)], dtype=object)
    mixed = r.random(n) < C.MIXED_CASE_EMAIL_SHARE
    email[mixed] = [e[0].upper() + e[1:e.index("@")].title() + e[e.index("@"):] for e in email[mixed]]
    # created: 55% before the window, the rest across it (growing)
    before = r.random(n) < 0.55
    c_old = pd.Timestamp("2012-01-01") + pd.to_timedelta(r.integers(0, (pd.Timestamp("2023-12-31") - pd.Timestamp("2012-01-01")).days, n), "D")
    u = r.random(n) ** 0.8
    c_new = pd.Timestamp(C.START) + pd.to_timedelta((u * (pd.Timestamp(C.END) - pd.Timestamp(C.START)).days).astype(int), "D")
    created = np.where(before, c_old, c_new)
    secs = r.integers(8 * 3600, 23 * 3600, n)
    created_at = pd.to_datetime(created) + pd.to_timedelta(secs, "s")
    df = pd.DataFrame(dict(
        customer_id=uuids(r, n), customer_number=[f"C{x:08d}" for x in r.permutation(np.arange(10_000_000, 10_000_000 + n))],
        first_name=first, last_name=last, email=email, phone=phones(r, area), street_address=street, city=city, state=state,
        postal_code=z, home_store_id=home, serving_dc_id=np.array([f"DC-{x}" for x in region], dtype=object),
        created_at=created_at.tz_localize("UTC"), account_type=np.where(guest, "guest", "registered"), loyalty_tier=loy,
        marketing_segment=seg, email_opt_in=(~guest) & (r.random(n) < 0.62)))
    # behavior (internal, never written)
    df["x_region"] = region
    df["x_activity"] = r.lognormal(0, C.CONSUMER_ACTIVITY_SIGMA, n) * np.where(guest, 0.25, 1.0)
    df["x_online_only"] = online_only | guest
    life = r.exponential(1 / C.ANNUAL_CHURN, n) * 365
    start_d = np.maximum((df.created_at.dt.tz_localize(None) - pd.Timestamp(C.START)).dt.days.to_numpy(), 0)
    df["x_start"] = start_d
    df["x_end"] = (start_d + life).astype(int)
    df["x_cards"] = r.integers(1, 3, n)
    return df


def _business_names(c: dict, r: np.random.Generator, cuisines: np.ndarray, last: np.ndarray) -> np.ndarray:
    parts = {}
    for row in c["restaurant_names"]:
        parts.setdefault((row["part"], row["cuisine"]), []).extend(split(row["words"].replace("|", ";")))
    pats = parts[("pattern", "")]
    out, seen = [], set()
    for cu, sn in zip(cuisines, last):
        kinds = parts.get(("kind", cu)) or parts[("kind", "")]
        for attempt in range(40):
            p = pats[r.integers(0, len(pats))]
            nm = (p.replace("{adj}", parts[("adj", "")][r.integers(0, len(parts[("adj", "")]))])
                  .replace("{noun2}", parts[("noun2", "")][r.integers(0, len(parts[("noun2", "")]))])
                  .replace("{noun}", parts[("noun", "")][r.integers(0, len(parts[("noun", "")]))])
                  .replace("{kind}", kinds[r.integers(0, len(kinds))])
                  .replace("{surname}", sn).replace("{place_word}", parts[("place_word", "")][r.integers(0, len(parts[("place_word", "")]))])
                  .replace("{n}", str(r.integers(2, 40))))
            if nm not in seen:
                break
        else:
            nm = f"{nm} {parts[('place_word', '')][r.integers(0, len(parts[('place_word', '')]))]}"
        seen.add(nm)
        out.append(nm)
    return np.array(out, dtype=object)


def build_accounts(c: dict, names: Names, places: dict, cat: dict) -> tuple[pd.DataFrame, dict]:
    r = rng("accounts")
    n = int(round(C.N_ACCOUNTS * C.SCALE))
    cu = pd.DataFrame(c["cuisines"])
    ci = weighted_choice(r, cu.account_share.astype(float).to_numpy(), n)
    cuisine = cu.cuisine.to_numpy()[ci]
    cities = places["cities"]
    cities_w = cities.weight.astype(float).to_numpy() * np.where(cities.store_metro == "Y", 1.0, 0.25)
    k = weighted_choice(r, cities_w, n)
    first, last = names.people(r, n)
    bname = _business_names(c, r, cuisine, names.last[weighted_choice(r, names.lw, n)])
    # P11: look-alike accounts, the same name in two or three cities
    base_idx = r.choice(n, C.SHARED_BUSINESS_NAMES, replace=False)
    shared = []
    pool = list(r.permutation(np.setdiff1d(np.arange(n), base_idx)))
    for bi in base_idx:
        m = int(r.integers(1, 3))
        while m > 0 and pool:
            o = int(pool.pop())
            if cities.city.iloc[k[o]] != cities.city.iloc[k[bi]]:
                bname[o] = bname[bi]
                cuisine[o] = cuisine[bi]
                shared.append((bname[bi], int(bi), o))
                m -= 1
    btypes = np.array([split(cu.business_types.iloc[j])[r.integers(0, len(split(cu.business_types.iloc[j])))] for j in ci], dtype=object)
    street, z = addresses(r, n, cities.zip3.to_numpy()[k])
    tier = np.array(list(C.TIER_SHARE))[weighted_choice(r, np.array(list(C.TIER_SHARE.values())), n)]
    before = r.random(n) < 0.72
    created = np.where(before, pd.Timestamp("2008-01-01") + pd.to_timedelta(r.integers(0, 5800, n), "D"),
                       pd.Timestamp(C.START) + pd.to_timedelta(r.integers(0, 1000, n), "D"))
    created = pd.to_datetime(created) + pd.to_timedelta(r.integers(8 * 3600, 17 * 3600, n), "s")
    closes = r.random(n) < 0.08 * 3 * 0.5
    end_d = np.where(closes, r.integers(200, 1096, n), 10_000)
    start_d = np.maximum((created - pd.Timestamp(C.START)).days.to_numpy(), 0)
    end_d = np.maximum(end_d, start_d + 60)
    status = np.where(end_d < 1096, "closed", "active")
    region = cities.region_id.to_numpy()[k]
    staff = places["staff"]
    reps = {reg: staff[(staff.role == "account_rep") & (staff.region_id == reg)].staff_id.tolist() for reg in ["NE", "SE", "CE", "WE"]}
    rep = np.array([reps[reg][int(r.integers(0, len(reps[reg])))] for reg in region], dtype=object)
    cstart = pd.to_datetime([dt.date(max(cr.year, 2016), cr.month, 1) for cr in created])
    ids = uuids(r, n)
    df = pd.DataFrame(dict(
        account_id=ids, account_number=[f"W{x:06d}" for x in r.permutation(np.arange(100_000, 100_000 + n))],
        business_name=bname, business_type=btypes, cuisine=cuisine,
        contact_name=[f"{a} {b}" for a, b in zip(first, last)],
        contact_email=[f"{a.lower()}@{re.sub('[^a-z0-9]+', '', bn.lower())[:24]}.example.com" for a, bn in zip(first, bname)],
        phone=phones(r, cities.area_code.to_numpy()[k]), street_address=street, city=cities.city.to_numpy()[k],
        state=cities.state.to_numpy()[k], postal_code=z, region_id=region,
        serving_dc_id=np.array([f"DC-{x}" for x in region], dtype=object), account_rep_id=rep, price_tier=tier,
        credit_terms=np.where(tier == "T3", "NET45", np.where(r.random(n) < 0.15, "NET15", "NET30")),
        contract_start=cstart.date, contract_end=None, status=status, created_at=created.tz_localize("UTC")))
    df["x_start"] = start_d
    df["x_end"] = end_d
    df["x_volume"] = r.lognormal(0, 0.6, n) * pd.Series(tier).map({"T1": 0.7, "T2": 1.2, "T3": 2.4}).to_numpy()
    df["x_cadence"] = np.clip(r.lognormal(np.log(cu.set_index("cuisine").loc[cuisine, "order_cadence_days_median"].astype(float).to_numpy()), 0.35), 4, 60)
    df["x_lines"] = cu.set_index("cuisine").loc[cuisine, "lines_per_order_mean"].astype(float).to_numpy()
    # contract end = the last anniversary on or after Dec 31, 2026 (or closing)
    ends = []
    for cs, e in zip(cstart, end_d):
        stop = pd.Timestamp(C.START) + pd.Timedelta(days=int(min(e, 1095)))
        y = stop.year if (stop.month, stop.day) <= (cs.month, cs.day) else stop.year + 1
        ends.append((pd.Timestamp(year=y, month=cs.month, day=1) - pd.Timedelta(days=1)).date())
    df["contract_end"] = ends

    # usual baskets: products by cuisine tag affinity, then a wholesale SKU per product
    P, S = cat["products"], cat["skus"]
    Sw = S[S.sold_wholesale].reset_index(drop=True)
    prod_tags = P.set_index("product_id")["cuisine_tags"].to_dict()
    pop = P.set_index("product_id")["x_pop"].map(C.ZIPF_BY_POPULARITY).to_dict()
    sku_prod = Sw.product_id.to_numpy()
    pack_w = Sw.pack_code.map(C.CHANNEL_PACK_PREF["wholesale"]).fillna(0.05).to_numpy()
    usuals = {}
    aff_cache = {}
    for i, row in enumerate(df.itertuples()):
        cr = cu.set_index("cuisine").loc[row.cuisine]
        if row.cuisine not in aff_cache:
            aff = dict(pairs(cr.tag_affinity))
            w = np.array([pop[p] * (0.15 + sum(aff.get(t, 0) for t in prod_tags[p])) for p in sku_prod]) * pack_w
            aff_cache[row.cuisine] = w
        w = aff_cache[row.cuisine].copy()
        if row.price_tier == "T3":
            w *= np.where(np.isin(Sw.pack_code.to_numpy(), ["B80", "P400", "O1G", "S1G", "N1G", "C80", "K80", "D400", "V100"]), 2.0, 1.0)
        nu = int(r.integers(int(cr.usual_skus_min), int(cr.usual_skus_max) + 1))
        idx = r.choice(len(Sw), size=min(nu, len(Sw)), replace=False, p=w / w.sum())
        uw = r.lognormal(0, 0.9, len(idx))
        usuals[row.account_id] = (Sw.sku_id.to_numpy()[idx], uw / uw.sum())
    return df, dict(usuals=usuals, shared_names=shared)


def build_contract_prices(accounts: pd.DataFrame, extra: dict, cat: dict) -> pd.DataFrame:
    r = rng("contract_prices")
    S = cat["skus"].reset_index(drop=True)
    sidx = {s: i for i, s in enumerate(S.sku_id)}
    periods, mat = cat["price_periods"], cat["price_matrix"]["wholesale"]
    rows = []
    for a in accounts.itertuples():
        skus, w = extra["usuals"][a.account_id]
        k = min(len(skus), int(r.integers(8, 26)))
        top = skus[np.argsort(-w)[:k]]
        disc = r.uniform(*C.CONTRACT_DISCOUNT, k)
        tier = C.TIER_DISCOUNT[a.price_tier]
        cs = pd.Timestamp(a.contract_start)
        first_y = max(cs.year, 2023)
        last_end = pd.Timestamp(a.contract_end)
        y = first_y
        while True:
            start = pd.Timestamp(year=y, month=cs.month, day=1)
            if start > last_end:
                break
            end = min(pd.Timestamp(year=y + 1, month=cs.month, day=1) - pd.Timedelta(days=1), last_end)
            if end.date() >= C.START:
                pi = max(0, int(np.searchsorted(np.array(periods), start.date(), side="right")) - 1)
                for s, d in zip(top, disc):
                    price = round(float(mat[sidx[s], pi]) * (1 - tier) * (1 - d), 2)
                    rows.append((a.account_id, s, start.date(), end.date(), price))
            y += 1
    return pd.DataFrame(rows, columns=["account_id", "sku_id", "effective_from", "effective_to", "contract_price"])
