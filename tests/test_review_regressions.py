"""Original independently replayable defect probes, added only after review findings."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
