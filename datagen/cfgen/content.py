"""Load and check datagen/content/. `python3 -m cfgen.content --check` prints problems and projected counts."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

CONTENT = Path(__file__).resolve().parent.parent / "content"


def read(name: str) -> list[dict]:
    with open(CONTENT / name, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in r.items():
            if v is None:
                raise ValueError(f"{name}: malformed row {r}")
            r[k] = v.strip()
    return rows


def split(v: str) -> list[str]:
    return [x.strip() for x in v.split(";") if x.strip()]


def pairs(v: str) -> list[tuple[str, float]]:
    out = []
    for item in split(v):
        k, _, p = item.partition(":")
        out.append((k.strip(), float(p)))
    return out


def load() -> dict[str, list[dict]]:
    files = ["spices.csv", "blends.csv", "pantry.csv", "gift_sets.csv", "packaging.csv", "locations.csv",
             "supplier_countries.csv", "seasonality.csv", "events.csv", "cuisines.csv", "consumer_segments.csv",
             "us_cities.csv"]
    c = {f.removesuffix(".csv"): read(f) for f in files}
    c["names_surnames"] = read("names/surnames.csv")
    c["names_female"] = read("names/first_names_female.csv")
    c["names_male"] = read("names/first_names_male.csv")
    c["restaurant_names"] = read("restaurant_names.csv")
    return c


def check(c: dict[str, list[dict]]) -> list[str]:
    problems = []
    spice = {r["slug"] for r in c["spices"]}
    pantry = {r["slug"] for r in c["pantry"]}
    blends = {r["slug"] for r in c["blends"]}
    seen = set()
    for s in [r["slug"] for r in c["spices"] + c["blends"] + c["pantry"]]:
        if s in seen:
            problems.append(f"duplicate slug {s}")
        seen.add(s)
    profiles = {r["profile"] for r in c["seasonality"]}
    for r in c["blends"]:
        comps = pairs(r["components"])
        tot = sum(p for _, p in comps)
        if abs(tot - 100) > 0.01:
            problems.append(f"blend {r['slug']}: components sum to {tot}")
        for s, _ in comps:
            if s not in spice | pantry:
                problems.append(f"blend {r['slug']}: unknown component {s}")
    for f in ("spices", "blends", "pantry", "gift_sets"):
        for r in c[f]:
            if r.get("seasonality") and r["seasonality"] not in profiles:
                problems.append(f"{f} {r['slug']}: unknown seasonality {r['seasonality']}")
    for r in c["gift_sets"]:
        for s, _ in pairs(r["contents"]):
            if s not in spice | pantry | blends:
                problems.append(f"gift set {r['slug']}: unknown item {s}")
    for r in c["events"]:
        for p in split(r["profiles"]):
            if p not in profiles:
                problems.append(f"event {r['event']} {r['date']}: unknown profile {p}")
    if abs(sum(float(r["account_share"]) for r in c["cuisines"]) - 1) > 0.01:
        problems.append("cuisines.csv account_share does not sum to 1")
    if abs(sum(float(r["share"]) for r in c["consumer_segments"]) - 1) > 0.01:
        problems.append("consumer_segments.csv share does not sum to 1")
    if sum(int(r["suppliers"]) for r in c["supplier_countries"]) != 150:
        problems.append("supplier_countries.csv suppliers do not sum to 150")
    return problems


def main() -> int:
    c = load()
    probs = check(c)
    for p in probs:
        print("PROBLEM:", p)
    from . import catalog
    cat = catalog.build(c)
    print(f"products {len(cat['products']):,}   skus {len(cat['skus']):,}   ingredients {len(cat['ingredients']):,}")
    print(cat["products"].groupby("category_id").size().to_string())
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
