"""Online-order risk scoring and the planted fraud ring (F1)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config as C
from .util import rng, stable_hash, uuids


def make_ring(consumers: pd.DataFrame, cat: dict) -> tuple[pd.DataFrame, dict]:
    """Append the ring's guest accounts and plan their orders (forced SKUs, days, shared addresses and cards)."""
    r = rng("plant", "fraud_ring")
    pl = C.PLANT
    n = pl["fraud_ring_size"]
    d0, d1 = pl["fraud_ring_window"]
    P, S = cat["products"], cat["skus"]
    targets = []
    for slug, pack in (("saffron_spain", "G28"), ("saffron_kashmir", "G28"), ("vanilla_madagascar", "V25"),
                       ("vanilla_tahitian", "V25")):
        pid = P[(P.ingredient_id == slug) & (P.is_organic == False)].product_id.iloc[0]   # noqa: E712
        s = S[(S.product_id == pid) & (S.pack_code == pack)]
        if len(s):
            targets.append(s.sku_id.iloc[0])
    gift = P[(P.x_slug == "saffron_and_vanilla") & (P.x_variant == "2026")].product_id.iloc[0]
    targets.append(S[(S.product_id == gift) & (S.pack_code == "GIFT")].sku_id.iloc[0])
    addr = [("418 Quarry Ct", "Houston", "TX", "77041", "713"), ("2290 Depot Way", "Newark", "NJ", "07105", "973")]
    created = [d0 + dt.timedelta(int(x)) for x in r.integers(0, (d1 - d0).days + 1, n)]
    a_idx = r.integers(0, len(addr), n)
    first = np.array(["Jordan", "Casey", "Morgan", "Riley", "Avery", "Quinn", "Taylor", "Jamie", "Drew", "Reese",
                      "Skyler", "Rowan", "Parker", "Emerson"])[:n]
    last = np.array(["Hale", "Pryor", "Vance", "Lowe", "Marsh", "Dunn", "Shaw", "Keane", "Foss", "Rook", "Sloan",
                     "Pike", "Crane", "Holt"])[:n]
    rows = pd.DataFrame(dict(
        customer_id=uuids(r, n), customer_number=[f"C{19_900_000 + i:08d}" for i in range(n)],
        first_name=first, last_name=last,
        email=[f"{a.lower()}{b.lower()}{int(k)}@example.net" for a, b, k in zip(first, last, r.integers(100, 999, n))],
        phone=[f"{addr[i][4]}-555-01{int(x):02d}" for i, x in zip(a_idx, r.integers(0, 100, n))],
        street_address=[addr[i][0] for i in a_idx], city=[addr[i][1] for i in a_idx], state=[addr[i][2] for i in a_idx],
        postal_code=[addr[i][3] for i in a_idx], home_store_id=None,
        serving_dc_id=["DC-CE" if addr[i][2] == "TX" else "DC-NE" for i in a_idx],
        created_at=[pd.Timestamp(d, tz="UTC") + pd.Timedelta(seconds=int(s)) for d, s in zip(created, r.integers(3600, 80000, n))],
        account_type="guest", loyalty_tier="none", marketing_segment="gift_giver", email_opt_in=False))
    rows["x_region"] = ["CE" if addr[i][2] == "TX" else "NE" for i in a_idx]
    rows["x_activity"] = 0.0
    rows["x_online_only"] = True
    rows["x_start"] = [(d - C.START).days for d in created]
    rows["x_end"] = rows["x_start"] + 60
    rows["x_cards"] = 1
    base = len(consumers)
    out = pd.concat([consumers, rows], ignore_index=True)
    fps = [stable_hash(f"ring-card-{k}") for k in range(pl["fraud_ring_fingerprints"])]
    plan = []
    for i in range(n):
        k_orders = 1 + int(r.random() < 0.5)
        for _ in range(k_orders):
            day = (created[i] - C.START).days + int(r.integers(0, 6))
            if r.random() < 0.3:
                day = (pl["peak_day"] - C.START).days        # hide in the Cyber Monday crowd
            picks = r.choice(targets, size=int(r.integers(1, 3)), replace=False)
            plan.append(dict(cust=base + i, day=day, skus=list(picks), qty=list(r.integers(2, 5, len(picks))),
                             fingerprint=fps[i % len(fps)], member=i))
    return out, dict(plan=plan, ring_customers=list(range(base, base + n)), fingerprints=fps,
                     addresses=[a[0] for a in addr])


def score(O: pd.DataFrame, Lx: pd.DataFrame, consumers: pd.DataFrame, ring: dict) -> dict:
    r = rng("fraud", "score")
    on = np.nonzero(O.channel.to_numpy() == "online")[0]
    n = len(on)
    base = r.beta(1.3, 16, n)
    cust = O.x_cust.to_numpy()[on]
    created = consumers.created_at.to_numpy()[cust]
    age_d = (O.ordered_at.to_numpy()[on] - created) / np.timedelta64(1, "D")
    sub = O.subtotal.to_numpy()[on]
    flags_new = age_d < 7
    flags_val = sub > 250
    score_ = np.clip(base + 0.12 * flags_new + 0.10 * flags_val, 0, 0.69)
    k = int(round(C.RISK_FLAG_SHARE * n))
    flagged = r.choice(n, size=k, replace=False)
    score_[flagged] = r.uniform(C.RISK_THRESHOLD, 0.97, k)
    review = np.full(n, "none", dtype=object)
    fp = r.random(k) < C.RISK_FALSE_POSITIVE
    review[flagged] = np.where(fp, "cleared", "cancelled_fraud")
    flags = []
    for i in range(n):
        f = []
        if flags_new[i]:
            f.append("new_account")
        if flags_val[i]:
            f.append("high_value")
        if score_[i] >= C.RISK_THRESHOLD:
            f += list(r.choice(["velocity", "address_mismatch", "card_country_mismatch", "proxy_ip"], size=int(r.integers(1, 3)), replace=False))
        flags.append(f or None)
    # the ring: 9 caught and cancelled, 5 cleared by mistake
    ring_set = set(ring["ring_customers"])
    ring_rows = [i for i in range(n) if cust[i] in ring_set]
    members = sorted({cust[i] for i in ring_rows})
    missed = set(members[: C.PLANT["fraud_ring_missed"]])
    for i in ring_rows:
        score_[i] = r.uniform(0.74, 0.96)
        if cust[i] in missed:
            review[i] = "cleared"
        else:
            review[i] = "cancelled_fraud"
        flags[i] = ["new_account", "high_value", "velocity", "shared_ship_to"]
    O.loc[O.index[on], "risk_score"] = np.round(score_, 3)
    O.loc[O.index[on], "risk_review"] = review
    O["risk_flags"] = O["risk_flags"].astype(object)
    rf = O["risk_flags"].to_numpy().copy()
    for j, i in enumerate(on):
        rf[i] = flags[j]
    O["risk_flags"] = rf
    canc = O.index[on][review == "cancelled_fraud"]
    O.loc[canc, "status"] = "cancelled"
    Lx.loc[Lx.x_order.isin(canc), "line_status"] = "cancelled"
    Lx.loc[Lx.x_order.isin(canc), "batch_id"] = None
    ring_orders = O.index[on][[cust[i] in ring_set for i in range(n)]]
    return dict(flagged=int(k), cancelled_fraud=int((review == "cancelled_fraud").sum()),
                ring_orders=O.loc[ring_orders, "order_number"].tolist(),
                ring_customer_numbers=consumers.customer_number.to_numpy()[members].tolist(),
                ring_missed_customer_numbers=consumers.customer_number.to_numpy()[sorted(missed)].tolist())
