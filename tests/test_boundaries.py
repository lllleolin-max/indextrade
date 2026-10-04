from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from indextrade import IndexTradeError, advise
from indextrade.cli import main, read_workload
from indextrade.pilot import create_service, workload


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        create_service(self.source, 80)
        self.spec = workload(80)
        self.spec["candidates"] = []
        self.spec["limits"] = dict(samples=1, warmup=0)

    def test_cli_bom_duplicate_properties_unknown_and_huge_limit(self):
        filename = self.root / "input.json"
        filename.write_bytes(b"\xef\xbb\xbf" + json.dumps(self.spec).encode())
        self.assertEqual(read_workload(filename), self.spec)
        filename.write_text('{"version":1,"version":2}')
        with self.assertRaises(IndexTradeError):
            read_workload(filename)
        self.spec["limits"]["max_sets"] = 10**400
        filename.write_text(json.dumps(self.spec))
        with redirect_stderr(io.StringIO()) as error, redirect_stdout(io.StringIO()) as out:
            status = main(["advise", str(self.source), str(filename), str(self.root / "out")])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(error.getvalue())["error"], "IndexTradeError")
        self.assertEqual(out.getvalue(), "")
        self.assertFalse((self.root / "out").exists())

    def test_budgeted_unknown_cli_exit_and_unicode_identifiers_in_data(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("UPDATE customers SET region=? WHERE id=1", ("华东🙂",))
            db.commit()
        self.spec["candidates"] = [dict(name="by_status", table="orders", columns=["status"])]
        self.spec["limits"]["max_sets"] = 1
        filename = self.root / "input.json"
        filename.write_text(json.dumps(self.spec), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            status = main(["advise", str(self.source), str(filename), str(self.root / "out")])
        self.assertEqual(status, 4)
        self.assertEqual(json.loads(out.getvalue())["status"], "UNKNOWN")
        self.assertTrue(out.getvalue().isascii())

    def test_freelist_reuse_has_nonzero_index_storage(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE TABLE retired(padding BLOB)")
            db.executemany("INSERT INTO retired VALUES(zeroblob(4096))", [()] * 100)
            db.commit()
            db.execute("DROP TABLE retired")
            db.commit()
            self.assertGreater(db.execute("PRAGMA freelist_count").fetchone()[0], 0)
        self.spec["candidates"] = [dict(name="by_status", table="orders", columns=["status"])]
        report = advise(self.source, self.spec, self.root / "out")
        self.assertGreater(report["portfolios"][1]["index_bytes"], 0)
        self.assertIn(report["portfolios"][1]["storage_method"],
                      ("dbstat_index_pages", "allocated_page_increment_upper_bound"))

    def test_real_process_exit_before_and_after_report_has_no_complete(self):
        filename = self.root / "input.json"
        filename.write_text(json.dumps(self.spec))
        original = self.source.read_bytes()
        for point in ("snapshot", "report"):
            output = self.root / point
            script = """
import json, os, pathlib, sys
import indextrade.engine as engine
source, workload, output, point = sys.argv[1:]
if point == 'snapshot':
    original = engine.snapshot
    def snapshot(*args):
        original(*args)
        os._exit(23)
    engine.snapshot = snapshot
else:
    original = pathlib.Path.write_text
    def write(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.name == 'report.json':
            os._exit(23)
        return result
    pathlib.Path.write_text = write
engine.advise(source, json.loads(pathlib.Path(workload).read_text()), output)
"""
            result = subprocess.run([sys.executable, "-c", script, str(self.source),
                                     str(filename), str(output), point], capture_output=True)
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertTrue((output / "INCOMPLETE.json").exists())
            self.assertFalse((output / "COMPLETE.json").exists())
            self.assertEqual(self.source.read_bytes(), original)

    def test_completion_io_error_leaves_source_unchanged_and_incomplete(self):
        original = Path.write_text
        source_bytes = self.source.read_bytes()

        def fail(self, *args, **kwargs):
            if self.name == "COMPLETE.json":
                raise OSError("injected completion write failure")
            return original(self, *args, **kwargs)

        with patch.object(Path, "write_text", fail), self.assertRaises(OSError):
            advise(self.source, self.spec, self.root / "out")
        self.assertTrue((self.root / "out/INCOMPLETE.json").exists())
        self.assertFalse((self.root / "out/COMPLETE.json").exists())
        self.assertEqual(self.source.read_bytes(), source_bytes)

    def test_hidden_rowid_and_without_rowid_tables_in_actual_state(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE TABLE labels(name TEXT PRIMARY KEY, value BLOB) WITHOUT ROWID")
            db.execute("INSERT INTO labels VALUES('a',x'00ff')")
            db.commit()
        report = advise(self.source, self.spec, self.root / "out")
        self.assertTrue(report["proposal"]["feasible"])
        self.assertEqual(len(report["proposal"]["write_results"]), 5)


if __name__ == "__main__":
    unittest.main()
