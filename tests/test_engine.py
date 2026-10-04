from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from indextrade import IndexTradeError, advise, validate_workload
from indextrade.engine import answer, metric, nondominated
from indextrade.pilot import consumer, create_service, workload


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        create_service(self.source, 80)
        self.spec = workload(80)
        self.spec["limits"] = dict(samples=1, warmup=0, max_sets=16)

    def run_advice(self, spec=None, name="out"):
        return advise(self.source, spec or self.spec, self.root / name)

    def test_complete_actual_materialization_source_unchanged(self):
        before = self.source.read_bytes()
        report = self.run_advice()
        self.assertEqual(report["status"], "EXHAUSTIVE")
        self.assertEqual(report["explored_sets"], 16)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(consumer(self.source, 80), consumer(self.root / "out/proposal.db", 80))
        self.assertEqual(metric(report["proposal"]), metric(report["materialized_verification"]))
        complete = json.loads((self.root / "out/COMPLETE.json").read_text())
        for name, expected in complete["artifacts_sha256"].items():
            self.assertEqual(hashlib.sha256((self.root / "out" / name).read_bytes()).hexdigest(), expected)

    def test_finite_selection_and_pareto_independent_oracle(self):
        report = self.run_advice()
        feasible = [r for r in report["portfolios"] if r["feasible"]]
        winner = min(feasible, key=lambda r: (r["weighted_read_vm"], r["index_bytes"],
                                              r["write_vm_steps"], r["indexes"]))
        self.assertEqual(winner, report["proposal"])
        for member in report["pareto"]:
            costs = (member["weighted_read_vm"], member["index_bytes"], member["write_vm_steps"])
            for rival in feasible:
                other = (rival["weighted_read_vm"], rival["index_bytes"], rival["write_vm_steps"])
                self.assertFalse(all(a <= b for a, b in zip(other, costs)) and
                                 any(a < b for a, b in zip(other, costs)))

    def test_unknown_has_materialized_incumbent(self):
        self.spec["limits"]["max_sets"] = 2
        report = self.run_advice()
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["explored_sets"], 2)
        self.assertTrue((self.root / "out/COMPLETE.json").exists())

    def test_zero_storage_no_candidates_and_no_benefit(self):
        self.spec["budgets"]["max_index_bytes"] = 0
        report = self.run_advice()
        self.assertEqual(report["proposal"]["indexes"], [])
        self.assertTrue(any("storage_budget" in r["rejected_by"] for r in report["portfolios"]))

    def test_storage_boundary_plus_minus_one(self):
        report = self.run_advice()
        value = report["portfolios"][1]["index_bytes"]
        self.assertGreater(value, 0)
        for amount, accepted in ((value - 1, False), (value, True), (value + 1, True)):
            spec = copy.deepcopy(self.spec)
            spec["budgets"]["max_index_bytes"] = amount
            check = self.run_advice(spec, "b" + str(amount))
            self.assertEqual("storage_budget" not in check["portfolios"][1]["rejected_by"], accepted)

    def test_write_budget_rejects_actual_maintenance_work(self):
        self.spec["budgets"]["max_write_ratio"] = 1
        report = self.run_advice()
        self.assertTrue(any("write_budget" in r["rejected_by"] for r in report["portfolios"]))

    def test_authorizer_rejects_writes_functions_and_attach(self):
        for index, sql in enumerate(("DELETE FROM orders", "SELECT random()", "SELECT load_extension(?)",
                                     "SELECT * FROM pragma_table_info('orders')", "SELECT 1; ATTACH 'x' AS other")):
            spec = copy.deepcopy(self.spec)
            spec["queries"][0]["sql"] = sql
            spec["queries"][0]["cases"] = [[]]
            with self.subTest(sql=sql), self.assertRaises((IndexTradeError, sqlite3.Error)):
                self.run_advice(spec, "bad" + str(index))
        self.assertEqual(consumer(self.source, 80)["orders"], 80)

    def test_duplicate_names_bool_nan_unknown_fields(self):
        bad = copy.deepcopy(self.spec)
        bad["candidates"][1]["name"] = bad["candidates"][0]["name"].upper()
        with self.assertRaises(IndexTradeError):
            validate_workload(bad)
        for prop, val in (("max_sets", True), ("samples", float("nan")), ("secret", 3)):
            bad = copy.deepcopy(self.spec)
            bad["limits"][prop] = val
            with self.assertRaises(IndexTradeError):
                validate_workload(bad)

    def test_existing_output_source_alias_and_schema_collision(self):
        for output in (self.source, self.root):
            with self.assertRaises(IndexTradeError):
                advise(self.source, self.spec, output)
        self.spec["candidates"][0]["name"] = "orders"
        with self.assertRaises(IndexTradeError):
            self.run_advice()
        self.assertFalse((self.root / "out/COMPLETE.json").exists())

    def test_unsupported_schema_and_result_values(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE VIEW order_view AS SELECT * FROM orders")
            db.commit()
        with self.assertRaises(IndexTradeError):
            self.run_advice()
        with self.assertRaises(IndexTradeError):
            answer([(float("inf"),)], False)

    def test_typed_multiset_preserves_duplicates_null_blob_and_types(self):
        self.assertEqual(answer([(None, b"x"), (1, "1"), (1, "1")], False),
                         answer([(1, "1"), (None, b"x"), (1, "1")], False))
        self.assertNotEqual(answer([(1,)], False), answer([(1.0,)], False))
        self.assertNotEqual(answer([(1,), (1,)], False), answer([(1,)], False))

    def test_low_resource_fails_baseline_without_complete(self):
        self.spec["limits"]["max_statement_vm"] = 1
        with self.assertRaises(IndexTradeError):
            self.run_advice()
        self.assertTrue((self.root / "out/INCOMPLETE.json").exists())
        self.assertFalse((self.root / "out/COMPLETE.json").exists())


if __name__ == "__main__":
    unittest.main()
