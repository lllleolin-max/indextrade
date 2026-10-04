"""Original independently replayable defect probes, added only after review findings."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from indextrade import advise
from indextrade.engine import snapshot


class ReviewRegressions(unittest.TestCase):
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
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, kind INTEGER)")
                db.executemany("INSERT INTO events VALUES(?,?)", [(1, 2), (2, 1)])
                db.commit()
            sql = "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE kind>0 LIMIT 1)"
            # Actual SQLite oracle: a new index changes which row LIMIT chooses.
            observed = []
            for indexed in (False, True):
                with closing(sqlite3.connect(":memory:")) as db:
                    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, kind INTEGER)")
                    db.executemany("INSERT INTO events VALUES(?,?)", [(1, 2), (2, 1)])
                    if indexed:
                        db.execute("CREATE INDEX by_kind ON events(kind)")
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
