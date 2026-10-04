"""Independent stdlib consumer of published artifacts, without importing IndexTrade.

Run: python examples/verify_proposal.py SOURCE_DB ADVICE_DIRECTORY
This checks actual query answers and post-write logical state on separate copies.
It does not certify production performance or dishonest source edits.
"""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


def read_copy(source, target):
    copied = Path(str(target) + ".input")
    shutil.copyfile(source, copied)
    if Path(str(source) + "-wal").exists():
        shutil.copyfile(str(source) + "-wal", str(copied) + "-wal")
    with closing(sqlite3.connect(copied)) as db, closing(sqlite3.connect(target)) as out:
        db.backup(out)


def tag(value):
    if value is None:
        return ("null",)
    if type(value) is float:
        return ("float", value.hex())
    if type(value) is bytes:
        return ("bytes", value.hex())
    return (type(value).__name__, value)


def rows_key(rows, ordered=False):
    tagged = [tuple(tag(x) for x in row) for row in rows]
    return tagged if ordered else Counter(tagged)


def states(db):
    result = {}
    for schema, name, kind, ncol, without_rowid, strict in db.execute("PRAGMA table_list"):
        if schema != "main" or name.startswith("sqlite_"):
            continue
        names = {c[1].lower() for c in db.execute('PRAGMA table_xinfo("' + name + '")')}
        aliases = [n for n in ("rowid", "_rowid_", "oid") if n not in names]
        projection = '"' + aliases[0] + '",*' if not without_rowid and aliases else "*"
        result[name] = rows_key(db.execute('SELECT ' + projection + ' FROM "' + name + '"').fetchall())
    return result


def verify(source, output):
    source, output = Path(source), Path(output)
    manifest = json.loads((output / "COMPLETE.json").read_bytes())
    for name, expected in manifest["artifacts_sha256"].items():
        if hashlib.sha256((output / name).read_bytes()).hexdigest() != expected:
            raise ValueError("artifact hash mismatch: " + name)
    spec = json.loads((output / "workload.json").read_bytes())
    report = json.loads((output / "report.json").read_bytes())
    proposal = report["proposal"]
    if proposal["index_bytes"] > spec["budgets"]["max_index_bytes"]:
        raise ValueError("storage budget exceeded")
    if proposal["write_vm_steps"] > report["baseline"]["write_vm_steps"] * spec["budgets"]["max_write_ratio"]:
        raise ValueError("write budget exceeded")
    for new, old, query in zip(proposal["queries"], report["baseline"]["queries"], spec["queries"]):
        if new["vm_steps"] > old["vm_steps"] * query["max_regression"]:
            raise ValueError("query guardrail exceeded")
    with tempfile.TemporaryDirectory() as folder:
        left, right = Path(folder) / "source.db", Path(folder) / "proposal.db"
        read_copy(source, left)
        read_copy(output / "proposal.db", right)
        with closing(sqlite3.connect(left)) as a, closing(sqlite3.connect(right)) as b:
            a.execute("PRAGMA foreign_keys=ON")
            b.execute("PRAGMA foreign_keys=ON")
            if states(a) != states(b):
                raise ValueError("application rows differ before workload")
            checked = 0
            for query in spec["queries"]:
                for params in query["cases"]:
                    old = rows_key(a.execute(query["sql"], params).fetchall(), query["ordered"])
                    new = rows_key(b.execute(query["sql"], params).fetchall(), query["ordered"])
                    if old != new:
                        raise ValueError("actual query answer mismatch: " + query["id"])
                    checked += 1
            writes = 0
            for write in spec["writes"]:
                for params in write["cases"]:
                    a.execute(write["sql"], params)
                    b.execute(write["sql"], params)
                    if states(a) != states(b):
                        raise ValueError("actual post-write application state mismatch")
                    writes += 1
            a.rollback()
            b.rollback()
    return dict(independent_consumer=True, query_cases=checked, write_cases=writes,
                source_proposal_answers_equal=True, source_proposal_write_effects_equal=True,
                proposal=proposal["indexes"], sqlite_version=sqlite3.sqlite_version)


if __name__ == "__main__":
    print(json.dumps(verify(*sys.argv[1:]), sort_keys=True))
