"""A real SQLite order-service consumer; data and economic quantities are synthetic."""

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from .engine import IndexTradeError, advise, encoded, number


def create_service(path, orders=1200):
    number(orders, "orders", 50, 100_000)
    if Path(path).exists():
        raise IndexTradeError("service database already exists")
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE customers(id INTEGER PRIMARY KEY, region TEXT NOT NULL);
            CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL
                REFERENCES customers(id), status TEXT NOT NULL, created INTEGER NOT NULL,
                total_cents INTEGER NOT NULL);
            CREATE TABLE lines(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL
                REFERENCES orders(id), sku TEXT NOT NULL, quantity INTEGER NOT NULL);
        """)
        db.executemany("INSERT INTO customers VALUES(?,?)", [(i, ["east", "west", "north"][i % 3])
                                                            for i in range(1, 61)])
        db.executemany("INSERT INTO orders VALUES(?,?,?,?,?)", [
            (i, 1 + i % 60, ["open", "paid", "sent", "paid"][i % 4], i, 100 + i % 2500)
            for i in range(1, orders + 1)])
        db.executemany("INSERT INTO lines VALUES(?,?,?,?)", [
            (i, 1 + (i - 1) // 3, f"sku-{i % 17}", 1 + i % 5)
            for i in range(1, orders * 3 + 1)])
        db.commit()


def workload(orders=1200):
    return dict(version=1, queries=[
        dict(id="customer_history", sql="SELECT id,status,total_cents FROM orders WHERE customer_id=? ORDER BY created DESC,id DESC",
             cases=[[1], [22], [59]], weight=8, ordered=True, max_regression=1.1),
        dict(id="fulfilment", sql="SELECT id,customer_id FROM orders WHERE status=? AND created>? ORDER BY created,id",
             cases=[["open", orders // 2], ["paid", orders * 3 // 4]], weight=5, ordered=True, max_regression=1.1),
        dict(id="regional_revenue", sql="SELECT c.region,sum(o.total_cents),count(*) FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.status=? GROUP BY c.region ORDER BY c.region",
             cases=[["paid"], ["sent"]], weight=2, ordered=True, max_regression=1.1),
        dict(id="pick_list", sql="SELECT sku,sum(quantity) FROM lines WHERE order_id=? GROUP BY sku ORDER BY sku",
             cases=[[7], [orders // 2], [orders]], weight=9, ordered=True, max_regression=1.1)
    ], writes=[
        dict(sql="INSERT INTO orders VALUES(?,?,?,?,?)", cases=[[orders + 1, 8, "open", orders + 1, 2900], [orders + 2, 22, "paid", orders + 2, 450]]),
        dict(sql="UPDATE orders SET status=? WHERE id=?", cases=[["sent", 7], ["paid", 15]]),
        dict(sql="INSERT INTO lines VALUES(?,?,?,?)", cases=[[orders * 3 + 1, orders + 1, "sku-4", 2]])
    ], candidates=[
        dict(name="it_customer", table="orders", columns=["customer_id", "created", "id"]),
        dict(name="it_status", table="orders", columns=["status", "created", "id"]),
        dict(name="it_customer_status", table="orders", columns=["customer_id", "status", "total_cents"]),
        dict(name="it_lines", table="lines", columns=["order_id", "sku", "quantity"])
    ], budgets=dict(max_index_bytes=90000, max_write_ratio=2),
       limits=dict(max_sets=16, samples=3, warmup=1))


def consumer(path, orders):
    """Independent SQL business results, checked without engine digest helpers."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        invoices = db.execute("SELECT id,customer_id,status,total_cents FROM orders ORDER BY id").fetchall()
        actual_sum = db.execute("SELECT sum(total_cents) FROM orders").fetchone()[0]
        expected_sum = sum(100 + i % 2500 for i in range(1, orders + 1))
        picks = db.execute("SELECT order_id,sum(quantity) FROM lines GROUP BY order_id ORDER BY order_id").fetchall()
    expected_picks = [(i, sum(1 + j % 5 for j in range(3 * i - 2, 3 * i + 1)))
                      for i in range(1, orders + 1)]
    if len(invoices) != orders or actual_sum != expected_sum or picks != expected_picks:
        raise IndexTradeError("independent order service consumer failed")
    return dict(orders=orders, total_cents=actual_sum,
                invoices_sha256=hashlib.sha256(repr(invoices).encode("utf-8")).hexdigest(),
                picks_sha256=hashlib.sha256(repr(picks).encode("utf-8")).hexdigest())


def pilot(output, orders=1200):
    output = Path(output).resolve()
    output.mkdir(parents=False, exist_ok=False)
    source = output / "service.db"
    create_service(source, orders)
    specs = workload(orders)
    (output / "input.json").write_text(encoded(specs), encoding="ascii")
    before = consumer(source, orders)
    report = advise(source, specs, output / "advice")
    after = consumer(output / "advice" / "proposal.db", orders)
    if after != before:
        raise IndexTradeError("source/proposal consumer mismatch")
    verification = dict(source=before, proposal=after, equal=True, synthetic=True)
    (output / "consumer.json").write_text(encoded(verification), encoding="ascii")
    report["pilot_consumer"] = verification
    return report
