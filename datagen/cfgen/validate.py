"""Validation with DuckDB over the Parquet master copy: counts, keys, referential integrity, and every plant.

The plant queries are written in SQL a lab could adapt for AlloyDB; each records what it found in
validation/plants.json, and the run fails if a plant is missing.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

from . import config as C
from .schemas import T


def _mem_available_gb() -> float | None:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        return None
    return None


def run(out: Path, ga4_dir: Path | None, extra: dict) -> dict:
    pq_dir = out / "parquet"
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    # DuckDB's default limit is 80% of RAM, which doesn't allow for the generator's own memory in the same process;
    # cap it at half of what's free now and let big joins spill to disk instead of the kernel killing the run
    avail = _mem_available_gb()
    if avail:
        con.execute(f"SET memory_limit = '{max(1.0, avail * 0.5):.1f}GB'")
    (out / ".duckdb_tmp").mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{out / '.duckdb_tmp'}'")
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET enable_progress_bar = false")
    present = []
    for t in T:
        d = pq_dir / t.name
        if d.exists():
            con.execute(f"CREATE VIEW {t.name} AS SELECT * FROM read_parquet('{d}/*.parquet')")
            present.append(t.name)
    for f in (pq_dir / "_facts").glob("*.parquet"):
        con.execute(f"CREATE VIEW {f.stem} AS SELECT * FROM read_parquet('{f}')")
    if ga4_dir and ga4_dir.exists():
        con.execute(f"CREATE VIEW ga4_events AS SELECT * FROM read_parquet('{ga4_dir}/events_*.parquet')")

    def q(sql: str):
        return con.execute(sql).fetchall()

    def q1(sql: str):
        return con.execute(sql).fetchone()

    report = dict(tables={}, keys={}, foreign_keys={}, problems=[])
    for t in T:
        if t.name not in present:
            continue
        n = q1(f"SELECT count(*) FROM {t.name}")[0]
        dup = q1(f"SELECT count(*) FROM (SELECT {', '.join(t.pk)} FROM {t.name} GROUP BY ALL HAVING count(*) > 1)")[0]
        report["tables"][t.name] = n
        report["keys"][t.name] = dup
        if dup:
            report["problems"].append(f"{t.name}: {dup} duplicate primary keys")
        for cols, parent, pcols in t.fks:
            if parent not in present:
                continue
            cl, pl = cols.split(","), pcols.split(",")
            on = " AND ".join(f"c.{a} = p.{b}" for a, b in zip(cl, pl))
            nn = " AND ".join(f"c.{a} IS NOT NULL" for a in cl)
            orphans = q1(f"SELECT count(*) FROM {t.name} c WHERE {nn} AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {on})")[0]
            report["foreign_keys"][f"{t.name}.{cols} -> {parent}"] = orphans
            if orphans:
                report["problems"].append(f"{t.name}.{cols}: {orphans} orphans")
    # consistency checks
    checks = {
        "lines_sum_to_subtotal": """SELECT count(*) FROM (SELECT o.order_id, o.subtotal, sum(l.extended_price) s FROM orders o JOIN order_lines l USING (order_id)
                                    GROUP BY ALL HAVING abs(o.subtotal - s) > 0.011)""",
        "order_total_formula": "SELECT count(*) FROM orders WHERE abs(order_total - (subtotal - discount_total + shipping_total + tax_total)) > 0.011",
        "shipped_lines_without_batch": "SELECT count(*) FROM order_lines WHERE batch_id IS NULL AND line_status IN ('fulfilled','substituted','returned')",
        "orders_after_now": f"SELECT count(*) FROM orders WHERE ordered_at >= TIMESTAMPTZ '{C.NOW_UTC.isoformat()}'",
        "orders_before_customer": "SELECT count(*) FROM orders o JOIN customers c USING (customer_id) WHERE o.ordered_at < c.created_at - INTERVAL 1 DAY",
        "batch_before_lots": """SELECT count(*) FROM batch_inputs bi JOIN production_batches b USING (batch_id) JOIN inbound_lots l USING (lot_id)
                                WHERE l.received_at > b.produced_at + INTERVAL 3 DAY""",
        "blend_pct_not_100": "SELECT count(*) FROM (SELECT product_id, sum(pct) s FROM blend_components GROUP BY 1 HAVING abs(s - 100) > 0.05)",
    }
    report["checks"] = {}
    for k, sql in checks.items():
        try:
            v = q1(sql)[0]
        except Exception as e:      # a table not present in this run
            v = f"skipped: {str(e)[:80]}"
        report["checks"][k] = v
        if isinstance(v, int) and v:
            report["problems"].append(f"check {k}: {v}")

    P = C.PLANT
    plants = {}
    plants["P5_smoky_not_hot"] = dict(query="SELECT name, heat_level FROM products WHERE name IN (...) ORDER BY heat_level", rows=q(
        "SELECT name, heat_level FROM products WHERE name IN ('Smoked Paprika (Sweet), Ground', 'Smokehouse Brisket Rub', "
        "'Chipotle Meco Chile, Ground', 'Smoked Paprika (Hot), Ground') ORDER BY heat_level, name"))
    trace_sql = f"""WITH lot AS (SELECT lot_id FROM inbound_lots WHERE lot_code = '{P['failed_lot_code']}'),
  b AS (SELECT DISTINCT bi.batch_id FROM batch_inputs bi JOIN lot USING (lot_id)),
  ol AS (SELECT l.order_id, l.sku_id FROM order_lines l JOIN b USING (batch_id))
SELECT count(DISTINCT o.account_id) AS restaurants,
       count(DISTINCT o.order_id) FILTER (WHERE o.channel = 'online') AS online_orders,
       count(DISTINCT o.customer_id) AS consumers,
       count(DISTINCT o.location_id) FILTER (WHERE o.channel = 'store') AS stores,
       count(DISTINCT o.order_id) AS orders
FROM ol JOIN orders o USING (order_id)"""
    r_ = q1(trace_sql)
    prods = [x[0] for x in q(f"""SELECT DISTINCT p.name FROM inbound_lots il JOIN batch_inputs bi USING (lot_id) JOIN production_batches pb USING (batch_id)
                                 JOIN products p USING (product_id) WHERE il.lot_code = '{P['failed_lot_code']}' ORDER BY 1""")]
    plants["P8_failed_lot"] = dict(query=trace_sql, restaurants=r_[0], online_orders=r_[1], consumers=r_[2], stores=r_[3], orders=r_[4],
                                   products=prods, batches_on_hold=q1("SELECT count(*) FROM production_batches WHERE qc_status = 'hold'")[0])
    plants["P9_ambiguity_groups"] = dict(rows=q("SELECT ambiguity_group, count(*) FROM products WHERE ambiguity_group IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1"))
    plants["P10_contract_prices"] = dict(rows=q1("SELECT count(*), count(DISTINCT account_id) FROM contract_prices"),
                                         reps=q1("SELECT count(DISTINCT account_rep_id) FROM restaurant_accounts")[0])
    plants["P11_lookalike_accounts"] = dict(names=q1("SELECT count(*) FROM (SELECT business_name FROM restaurant_accounts GROUP BY 1 HAVING count(*) > 1)")[0],
                                            accounts=q1("SELECT count(*) FROM restaurant_accounts WHERE business_name IN (SELECT business_name FROM restaurant_accounts GROUP BY 1 HAVING count(*) > 1)")[0])
    plants["P12_seasonality"] = dict(query="baking-profile units by month", rows=q("""
        SELECT strftime(o.ordered_at AT TIME ZONE 'America/New_York', '%Y-%m') m, sum(l.quantity) units
        FROM order_lines l JOIN orders o USING (order_id) JOIN skus s USING (sku_id) JOIN products p USING (product_id)
        WHERE p.seasonality_profile IN ('baking', 'pumpkin') GROUP BY 1 ORDER BY 1"""))
    plants["P13_stockout"] = dict(
        saigon_on_hand_dc_ce=q1(f"""SELECT coalesce(sum(on_hand), 0) FROM inventory_positions ip JOIN skus s USING (sku_id) JOIN products p USING (product_id)
                                    WHERE p.ingredient_id = '{P['stockout_item']}' AND ip.location_id = '{P['stockout_dc']}'""")[0],
        saigon_on_hand_elsewhere=q1(f"""SELECT coalesce(sum(on_hand), 0) FROM inventory_positions ip JOIN skus s USING (sku_id) JOIN products p USING (product_id)
                                        WHERE p.ingredient_id = '{P['stockout_item']}' AND ip.location_id <> '{P['stockout_dc']}'""")[0],
        substituted_lines=q1("SELECT count(*) FROM order_lines WHERE line_status = 'substituted'")[0],
        backordered_lines=q1("SELECT count(*) FROM order_lines WHERE line_status = 'backordered'")[0],
        open_saigon_po=q(f"""SELECT po.po_number, po.expected_on FROM purchase_orders po JOIN po_lines pl USING (po_id)
                             WHERE pl.ingredient_id = '{P['stockout_item']}' AND po.destination_dc_id = '{P['stockout_dc']}' AND po.status = 'in_transit'"""),
        gift_sold_out_last_sale=q1(f"""SELECT max(o.ordered_at) FROM order_lines l JOIN orders o USING (order_id) JOIN skus s USING (sku_id) JOIN products p USING (product_id)
                                       WHERE p.name = 'Holiday Baking Collection {P['sellout_edition']}'""")[0],
        gift_on_hand=q1(f"""SELECT coalesce(sum(on_hand), 0) FROM inventory_positions ip JOIN skus s USING (sku_id) JOIN products p USING (product_id)
                            WHERE p.name = 'Holiday Baking Collection {P['sellout_edition']}'""")[0])
    if "ga4_events" in [x[0] for x in q("SELECT table_name FROM information_schema.tables")]:
        plants["P13_stockout"]["ga4_views_after_sellout"] = q1(f"""
            SELECT count(*) FROM ga4_events, UNNEST(items) AS t(i)
            WHERE event_name = 'view_item' AND event_date > '{P['sellout_date']:%Y%m%d}' AND i.item_name LIKE 'Holiday Baking Collection {P['sellout_edition']}%'""")[0]
        g = q1("""WITH p AS (SELECT DISTINCT ecommerce.transaction_id t FROM ga4_events WHERE event_name = 'purchase')
                  SELECT count(*) FILTER (WHERE p.t IS NULL), count(*) FROM orders o LEFT JOIN p ON p.t = o.order_number
                  WHERE o.channel = 'online' AND o.ordered_at >= TIMESTAMPTZ '2026-01-01 05:00:00+00'""")
        plants["P14_ga4_gap"] = dict(online_orders_2026=g[1], without_purchase_event=g[0], share=round(g[0] / max(g[1], 1), 4))
        report["ga4"] = dict(events=q1("SELECT count(*) FROM ga4_events")[0],
                             days=q1("SELECT count(DISTINCT event_date) FROM ga4_events")[0],
                             by_event=q("SELECT event_name, count(*) FROM ga4_events GROUP BY 1 ORDER BY 2 DESC, 1"))
    plants["P15_peak"] = dict(
        online_top_days=q("""SELECT CAST(ordered_at AT TIME ZONE 'America/New_York' AS DATE) d, count(*) n FROM orders
                             WHERE channel = 'online' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5"""),
        store_top_days=q("""SELECT CAST(ordered_at AT TIME ZONE 'America/New_York' AS DATE) d, count(*) n FROM orders
                            WHERE channel = 'store' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5"""),
        peak_hours=q(f"""SELECT hour(ordered_at AT TIME ZONE 'America/New_York') h, count(*) FROM orders
                         WHERE CAST(ordered_at AT TIME ZONE 'America/New_York' AS DATE) = DATE '{P['peak_day']}' AND channel = 'online'
                         GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3"""),
        november_2026_online_daily_mean=q1("""SELECT count(*) / 30.0 FROM orders WHERE channel = 'online'
                             AND CAST(ordered_at AT TIME ZONE 'America/New_York' AS DATE) BETWEEN DATE '2026-11-01' AND DATE '2026-11-30'""")[0])
    plants["F1_fraud_ring"] = dict(
        query="""SELECT ship_address_hash, count(DISTINCT customer_id) customers, count(*) orders FROM orders
                 WHERE channel = 'online' AND ordered_at >= TIMESTAMPTZ '2026-11-01' GROUP BY 1 HAVING count(DISTINCT customer_id) >= 5""",
        rows=q("""SELECT ship_address_hash, count(DISTINCT customer_id) customers, count(*) orders,
                         count(*) FILTER (WHERE risk_review = 'cancelled_fraud') cancelled, count(*) FILTER (WHERE risk_review = 'cleared') cleared
                  FROM orders WHERE channel = 'online' AND ordered_at >= TIMESTAMPTZ '2026-11-01' GROUP BY 1 HAVING count(DISTINCT customer_id) >= 5"""),
        flagged_online=q1(f"SELECT count(*) FROM orders WHERE risk_score >= {C.RISK_THRESHOLD}")[0])
    if "review_facts" in [x[0] for x in q("SELECT table_name FROM information_schema.tables")]:
        plants["P6_review_themes"] = dict(rows=q("""SELECT t, count(*) FROM (SELECT unnest(CAST(json(themes) AS VARCHAR[])) t FROM review_facts)
                                                    GROUP BY 1 ORDER BY 2 DESC"""), reviews=q1("SELECT count(*) FROM review_facts")[0])
        plants["P7_clumping_by_dc"] = dict(rows=q(f"""
            SELECT x_batch_dc = '{P['clumping_dc']}' AND x_batch_month IN {tuple(P['clumping_months'])} AS humid_batches,
                   count(*) reviews, round(avg(CASE WHEN themes LIKE '%clumping%' THEN 1 ELSE 0 END) * 100, 2) clumping_pct
            FROM review_facts GROUP BY 1 ORDER BY 1"""))
    report["plants"] = plants
    report["extra"] = extra
    for k, ok in (("P8_failed_lot", plants["P8_failed_lot"]["restaurants"] > 0),
                  ("P13_stockout", plants["P13_stockout"]["saigon_on_hand_dc_ce"] == 0),
                  ("P15_peak", str(plants["P15_peak"]["online_top_days"][0][0]) == str(P["peak_day"])),
                  ("F1_fraud_ring", len(plants["F1_fraud_ring"]["rows"]) >= 1)):
        if not ok:
            report["problems"].append(f"plant {k} not found as designed")
    vdir = out / "validation"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "plants.json").write_text(json.dumps(plants, indent=1, default=str) + "\n", encoding="utf-8")
    (vdir / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    return report
