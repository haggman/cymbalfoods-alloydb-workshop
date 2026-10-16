"""Demand curves: product weight by day and channel (popularity × season × events), and channel volume by day."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config as C
from .content import pairs, split

CHANNELS = ["store", "online", "wholesale"]
RAMADAN_TAGS = {"middle_eastern", "north_african", "indian"}


class Demand:
    """W[c][p, d]: relative demand for product p on day d in channel c. vol[c][d]: relative order volume."""

    def __init__(self, c: dict, cat: dict):
        P = cat["products"]
        self.P = P
        self.n_days = (C.END - C.START).days + 1
        self.dates = pd.date_range(C.START, C.END, freq="D")
        months = self.dates.month.to_numpy() - 1
        prof = pd.DataFrame(c["seasonality"]).set_index("profile")
        mcols = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        M = prof[mcols].astype(float)
        pidx = P["seasonality_profile"].map({k: i for i, k in enumerate(M.index)}).to_numpy()
        pop = P["x_pop"].map(C.ZIPF_BY_POPULARITY).to_numpy(dtype=float)
        base = pop[:, None] * M.to_numpy()[pidx][:, months]            # products × days
        # availability: launched, not discontinued, gift editions in season, the sell-out plant
        avail = np.ones_like(base, dtype=bool)
        launched = pd.to_datetime(P["launched_on"]).to_numpy()
        disc = pd.to_datetime(P["discontinued_on"]).to_numpy()
        dd = self.dates.to_numpy()
        avail &= launched[:, None] <= dd[None, :]
        avail &= ~(pd.notna(disc)[:, None] & (dd[None, :] > disc[:, None]))
        so = P.index[(P["x_slug"] == C.PLANT["sellout_gift"]) & (P["x_variant"] == str(C.PLANT["sellout_edition"]))]
        avail[so, :] &= dd < np.datetime64(C.PLANT["sellout_date"])
        self.avail = avail
        tags = P["cuisine_tags"].tolist()
        ram = np.array([bool(set(t) & RAMADAN_TAGS) for t in tags])
        profs = P["seasonality_profile"].to_numpy()
        self.W, self.vol = {}, {}
        self.store_closed = np.zeros(self.n_days, dtype=bool)
        ev = pd.DataFrame(c["events"])
        for ch in CHANNELS:
            E = np.ones_like(base)
            for e in ev.itertuples():
                if ch not in split(e.channels):
                    continue
                d0 = (dt.date.fromisoformat(e.date) - C.START).days
                lead, lag, peak = int(e.lead_days), int(e.lag_days), float(e.peak_multiplier)
                f = np.ones(self.n_days)
                for t in range(max(0, d0 - lead), min(self.n_days, d0 + lag + 1)):
                    if e.event == "Ramadan begins":
                        f[t] = peak
                    elif t <= d0:
                        f[t] = 1 + (peak - 1) * ((t - (d0 - lead) + 1) / (lead + 1)) ** 2
                    else:
                        f[t] = 1 + (peak - 1) * 0.5 * (1 - (t - d0) / (lag + 1))
                if e.event == "Ramadan begins":
                    hit = ram
                else:
                    hit = np.isin(profs, split(e.profiles))
                E[hit] *= f[None, :]
            W = base * E * avail
            self.W[ch] = W
            wk = np.array(C.WEEKDAY[ch])[self.dates.dayofweek.to_numpy()]
            v = W.sum(axis=0) / base.sum(axis=0) * wk
            if ch == "store":
                closed = np.isin(self.dates.strftime("%m-%d"), ["12-25", "01-01"]) | self._thanksgiving()
                v[closed] = 0
                self.store_closed = closed
            if ch == "online":
                v[self.dates == pd.Timestamp("2026-12-25")] *= 0.35
            self.vol[ch] = v

    def _thanksgiving(self) -> np.ndarray:
        out = np.zeros(self.n_days, dtype=bool)
        for y in C.YEARS:
            nov1 = dt.date(y, 11, 1)
            first_thu = nov1 + dt.timedelta((3 - nov1.weekday()) % 7)
            out[(first_thu + dt.timedelta(21) - C.START).days] = True
        return out

    def daily_counts(self, r: np.random.Generator, ch: str) -> np.ndarray:
        """Expected orders per day for a channel, scaled so each year hits its target."""
        lam = np.zeros(self.n_days)
        for y in C.YEARS:
            m = self.dates.year == y
            target = C.ORDERS_PER_YEAR[ch][y] * C.SCALE
            lam[m] = self.vol[ch][m] / self.vol[ch][m].sum() * target
        return lam
