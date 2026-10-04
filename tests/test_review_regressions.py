"""Original independently replayable defect probes, added only after review findings."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from indextrade import IndexTradeError, advise, validate_workload
from indextrade.engine import snapshot
from indextrade.pilot import workload


class ReviewRegressions(unittest.TestCase):
    def test_sqlite_camelcase_application_table_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE sqliteOrders(id INTEGER PRIMARY KEY, kind INTEGER)")
                db.executemany("INSERT INTO sqliteOrders VALUES(?,?)", [(1, 100), (2, 99)] +
                               [(i, 0) for i in range(3, 103)])
                db.commit()
            spec = dict(version=1,
                        queries=[dict(id="orders", sql="SELECT id,kind FROM sqliteOrders", cases=[[]],
                                      weight=1, ordered=False, max_regression=100)],
                        writes=[dict(sql="DELETE FROM sqliteOrders WHERE id IN "
                                         "(SELECT id FROM sqliteOrders WHERE kind>98 LIMIT 1)", cases=[[]])],
                        candidates=[dict(name="by_kind", table="sqliteOrders", columns=["kind"])],
                        budgets=dict(max_index_bytes=100000, max_write_ratio=100),
                        limits=dict(samples=1, warmup=0))
            report = advise(source, spec, root / "output")
            self.assertIn("write_answer_mismatch", report["portfolios"][1]["rejected_by"])

    def test_without_rowid_literal_cannot_hide_real_rowid_write_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE events(kind INTEGER, label TEXT DEFAULT 'WITHOUT ROWID')")
                db.executemany("INSERT INTO events(kind) VALUES(?)", [(100,), (99,)] + [(0,)] * 100)
                db.commit()
            spec = dict(version=1,
                        queries=[dict(id="events", sql="SELECT kind,label FROM events", cases=[[]],
                                      weight=1, ordered=False, max_regression=100)],
                        writes=[dict(sql="UPDATE events SET rowid=rowid+1000 WHERE rowid IN "
                                         "(SELECT rowid FROM events WHERE kind>98 LIMIT 1)", cases=[[]])],
                        candidates=[dict(name="by_kind", table="events", columns=["kind"])],
                        budgets=dict(max_index_bytes=100000, max_write_ratio=100),
                        limits=dict(samples=1, warmup=0))
            report = advise(source, spec, root / "output")
            self.assertIn("write_answer_mismatch", report["portfolios"][1]["rejected_by"])

    def test_output_cannot_occupy_source_sidecar_namespace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY)")
                db.execute("INSERT INTO events VALUES(1)")
                db.commit()
            spec = dict(version=1,
                        queries=[dict(id="events", sql="SELECT id FROM events", cases=[[]],
                                      weight=1, ordered=False, max_regression=1)],
                        writes=[dict(sql="INSERT INTO events VALUES(?)", cases=[[2]])],
                        candidates=[], budgets=dict(max_index_bytes=0, max_write_ratio=1),
                        limits=dict(samples=1, warmup=0))
            for suffix in ("-wal", "-shm", "-journal"):
                output = Path(str(source) + suffix)
                with self.subTest(suffix=suffix):
                    with self.assertRaises(IndexTradeError):
                        advise(source, spec, output)
                    self.assertFalse(output.exists())
            with closing(sqlite3.connect(source)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("INSERT INTO events VALUES(2)")
                db.commit()

    def test_huge_input_integer_is_a_controlled_domain_rejection(self):
        value = workload()
        value["limits"]["max_sets"] = 10**400
        with self.assertRaises(IndexTradeError):
            validate_workload(value)
    def test_committed_wal_rows_are_in_snapshot_without_source_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "wal.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO events VALUES(1,'committed-in-wal')")
                db.commit()
                before = {p.name: p.read_bytes() for p in root.glob("wal.db*")}
                snapshot(source, root / "snapshot.db")
                with closing(sqlite3.connect(root / "snapshot.db")) as copied:
                    rows = copied.execute("SELECT * FROM events").fetchall()
                self.assertEqual(rows, [(1, "committed-in-wal")])
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob("wal.db*")})

    def test_index_changing_written_rows_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.db"
            fixture = [(1, 100), (2, 99)] + [(i, 0) for i in range(3, 103)]
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, kind INTEGER)")
                db.executemany("INSERT INTO events VALUES(?,?)", fixture)
                db.commit()
            sql = "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE kind>98 LIMIT 1)"
            # Actual SQLite oracle: a new index changes which row LIMIT chooses.
            observed = []
            for indexed in (False, True):
                with closing(sqlite3.connect(":memory:")) as db:
                    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, kind INTEGER)")
                    db.executemany("INSERT INTO events VALUES(?,?)", fixture)
                    if indexed:
                        db.execute("CREATE INDEX by_kind ON events(kind)")
                    db.execute("ANALYZE")
                    db.execute(sql)
                    observed.append(db.execute("SELECT id FROM events").fetchall())
            self.assertNotEqual(observed[0], observed[1])
            spec = dict(version=1,
                        queries=[dict(id="events", sql="SELECT id,kind FROM events ORDER BY id",
                                      cases=[[]], weight=1, ordered=True, max_regression=100)],
                        writes=[dict(sql=sql, cases=[[]])],
                        candidates=[dict(name="by_kind", table="events", columns=["kind"])],
                        budgets=dict(max_index_bytes=100000, max_write_ratio=100),
                        limits=dict(samples=1, warmup=0))
            report = advise(source, spec, root / "output")
            self.assertIn("write_answer_mismatch", report["portfolios"][1]["rejected_by"])


if __name__ == "__main__":
    unittest.main()
