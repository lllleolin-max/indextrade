"""Finite index portfolios, evaluated by actual SQLite execution, never source DDL."""

from __future__ import annotations

from contextlib import closing
import base64
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import tempfile
import time


class IndexTradeError(ValueError):
    """Unsupported input, failed semantic check, or unsafe experiment boundary."""


class WorkLimit(IndexTradeError):
    """A measured SQLite statement exceeded the declared resource allowance."""


FUNCTIONS = {"abs", "coalesce", "count", "hex", "ifnull", "instr", "length",
             "like", "lower", "max", "min", "nullif", "round", "substr",
             "substring", "sum", "total", "trim", "typeof", "upper"}
LIMITS = {"max_sets": (1, 1024), "max_seconds": (1, 300),
          "max_statement_vm": (1, 20_000_000), "max_rows": (1, 200_000),
          "warmup": (0, 5), "samples": (1, 9)}
DEFAULTS = dict(max_sets=128, max_seconds=60, max_statement_vm=5_000_000,
                max_rows=100_000, warmup=1, samples=3)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode("ascii")).hexdigest()


def fields(obj, required, optional=()):
    if type(obj) is not dict or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        raise IndexTradeError(f"expected fields {sorted(required)}, optional {sorted(optional)}")


def number(value, name, lo, hi, *, integer=True):
    if (type(value) not in ((int,) if integer else (int, float))
            or not lo <= value <= hi or not math.isfinite(value)):
        raise IndexTradeError(f"{name} must be {'an integer' if integer else 'finite'} in [{lo}, {hi}]")
    return value


def identifier(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value):
        raise IndexTradeError("identifiers must be 1..64 ASCII letters, digits or underscores")
    return value


def quote(value):
    return '"' + identifier(value) + '"'


def parameters(cases):
    if type(cases) is not list or not 1 <= len(cases) <= 32:
        raise IndexTradeError("cases must contain 1..32 positional parameter arrays")
    for case in cases:
        if type(case) is not list or len(case) > 64:
            raise IndexTradeError("each case must be a positional parameter array of at most 64 values")
        for value in case:
            if value is not None and type(value) not in (str, int, float):
                raise IndexTradeError("parameters support only null, text, integer and finite real")
            if isinstance(value, str) and len(value.encode("utf-8")) > 65536:
                raise IndexTradeError("parameter text exceeds 64 KiB")
            if type(value) is int and not -(2**63) <= value < 2**63:
                raise IndexTradeError("parameter integer is outside SQLite's signed 64-bit range")
            if type(value) is float and not math.isfinite(value):
                raise IndexTradeError("parameter real must be finite")


def sqltext(value, kind):
    if type(value) is not str or len(value.encode("utf-8")) > 32768 or "\x00" in value:
        raise IndexTradeError("SQL must be bounded text without NUL")
    if not re.match(r"\s*(" + kind + r")\b", value, re.IGNORECASE):
        raise IndexTradeError(f"only {kind} statements are supported")


def validate_workload(value):
    fields(value, ("version", "queries", "writes", "candidates", "budgets"), ("limits",))
    if type(value["version"]) is not int or value["version"] != 1:
        raise IndexTradeError("only workload version 1 is supported")
    if type(value["queries"]) is not list or not 1 <= len(value["queries"]) <= 24:
        raise IndexTradeError("queries must contain 1..24 entries")
    ids = set()
    for query in value["queries"]:
        fields(query, ("id", "sql", "cases", "weight", "ordered", "max_regression"))
        name = identifier(query["id"])
        if name.casefold() in ids:
            raise IndexTradeError("query IDs must be distinct ignoring ASCII case")
        ids.add(name.casefold())
        sqltext(query["sql"], "SELECT")
        parameters(query["cases"])
        number(query["weight"], "weight", 1, 1000)
        number(query["max_regression"], "max_regression", 1, 100, integer=False)
        if type(query["ordered"]) is not bool:
            raise IndexTradeError("ordered must be boolean")
    if type(value["writes"]) is not list or not 1 <= len(value["writes"]) <= 24:
        raise IndexTradeError("writes must contain 1..24 entries")
    for write in value["writes"]:
        fields(write, ("sql", "cases"))
        sqltext(write["sql"], "INSERT|UPDATE|DELETE")
        parameters(write["cases"])
    candidates = value["candidates"]
    if type(candidates) is not list or len(candidates) > 10:
        raise IndexTradeError("at most ten candidate indexes are supported")
    names = set()
    for candidate in candidates:
        fields(candidate, ("name", "table", "columns"))
        name = identifier(candidate["name"])
        identifier(candidate["table"])
        if name.casefold() in names or name.lower().startswith("sqlite_"):
            raise IndexTradeError("index names must be distinct and not reserved")
        names.add(name.casefold())
        if type(candidate["columns"]) is not list or not 1 <= len(candidate["columns"]) <= 8:
            raise IndexTradeError("candidate columns must contain 1..8 names")
        for column in candidate["columns"]:
            identifier(column)
        if len(set(c.lower() for c in candidate["columns"])) != len(candidate["columns"]):
            raise IndexTradeError("duplicate index column")
    fields(value["budgets"], ("max_index_bytes", "max_write_ratio"))
    number(value["budgets"]["max_index_bytes"], "max_index_bytes", 0, 2**40)
    number(value["budgets"]["max_write_ratio"], "max_write_ratio", 1, 100, integer=False)
    limits = value.get("limits", {})
    fields(limits, (), LIMITS)
    for name, val in limits.items():
        number(val, name, *LIMITS[name])
    return value


def typed(value):
    if value is None:
        return ["null"]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float and math.isfinite(value):
        return ["real", value.hex()]
    if type(value) is str:
        return ["text", value]
    if type(value) is bytes:
        return ["blob", base64.b64encode(value).decode("ascii")]
    raise IndexTradeError("unsupported SQLite result type or nonfinite real")


def answer(rows, ordered):
    values = [[typed(value) for value in row] for row in rows]
    if not ordered:
        values.sort(key=encoded)
    return digest(values)


def authorizer(write=False):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}
    if write:
        allowed |= {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}

    def check(action, first, second, database, origin):
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if second.lower() in FUNCTIONS else sqlite3.SQLITE_DENY
        if action in allowed and database in (None, "main") and origin is None:
            if first and first.lower().startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return check


def execute(db, sql, params, limits, *, write=False, count=True):
    steps = 0
    interval = 1 if count else 1000
    deadline = time.monotonic() + limits["max_seconds"]

    def progress():
        nonlocal steps
        steps += interval
        return int(steps > limits["max_statement_vm"] or time.monotonic() > deadline)

    db.set_authorizer(authorizer(write))
    db.set_progress_handler(progress, interval)
    started = time.perf_counter_ns()
    try:
        cursor = db.execute(sql, params)
        rows = cursor.fetchmany(limits["max_rows"] + 1)
        if len(rows) > limits["max_rows"]:
            raise WorkLimit("query result exceeds max_rows")
        elapsed = time.perf_counter_ns() - started
        return rows, steps, elapsed
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise WorkLimit("SQLite statement VM/deadline limit exhausted") from exc
        raise IndexTradeError(f"unsupported or invalid SQL: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise IndexTradeError(f"SQLite workload rejected: {exc}") from exc
    finally:
        db.set_progress_handler(None, 0)
        db.set_authorizer(None)


def schema_check(db, candidates):
    tables = {}
    existing = set()
    for kind, name, sql in db.execute("SELECT type,name,sql FROM sqlite_schema"):
        existing.add(name.casefold())
        if kind in ("trigger", "view") or (kind == "table" and sql and "VIRTUAL TABLE" in sql.upper()):
            raise IndexTradeError("views, triggers and virtual tables are unsupported")
        if kind == "table" and not name.lower().startswith("sqlite_"):
            identifier(name)
            columns = db.execute(f"PRAGMA table_xinfo({quote(name)})").fetchall()
            if any(col[6] != 0 for col in columns):
                raise IndexTradeError("generated or hidden columns are unsupported")
            tables[name.casefold()] = {col[1].casefold() for col in columns}
    for candidate in candidates:
        if candidate["name"].casefold() in existing:
            raise IndexTradeError("candidate name collides with an existing schema object")
        if candidate["table"].casefold() not in tables:
            raise IndexTradeError("candidate table does not exist")
        if any(c.casefold() not in tables[candidate["table"].casefold()] for c in candidate["columns"]):
            raise IndexTradeError("candidate column does not exist")


def index_sql(candidate):
    return f"CREATE INDEX {quote(candidate['name'])} ON {quote(candidate['table'])} ({', '.join(quote(c) for c in candidate['columns'])});"


def used_pages(db):
    return db.execute("PRAGMA page_count").fetchone()[0] - db.execute("PRAGMA freelist_count").fetchone()[0]


def application_state(db, limits):
    """Observe each actual write's logical effect, including accessible hidden rowids."""
    states = []
    tables = db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for name, sql in tables:
        columns = {col[1].casefold() for col in db.execute(f"PRAGMA table_xinfo({quote(name)})")}
        aliases = [alias for alias in ("rowid", "_rowid_", "oid") if alias not in columns]
        projection = quote(aliases[0]) + ",*" if aliases and "WITHOUT ROWID" not in sql.upper() else "*"
        rows, _, _ = execute(db, f"SELECT {projection} FROM {quote(name)}", [], limits, count=False)
        states.append([name, answer(rows, False)])
    return digest(states)


def measure(path, workload, limits, indexes):
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA automatic_index=ON")
        db.execute("PRAGMA cache_size=-2000")
        before_pages = used_pages(db)
        for index in indexes:
            db.execute(index_sql(index))
        added_pages = max(0, used_pages(db) - before_pages)
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        try:
            storage = sum(db.execute("SELECT coalesce(sum(pgsize),0) FROM dbstat WHERE name=?",
                                     (index["name"],)).fetchone()[0] for index in indexes)
            method = "dbstat_index_pages"
        except sqlite3.OperationalError:
            # Before ANALYZE, only CREATE INDEX changed a fresh snapshot: used-page
            # growth includes index + schema overhead, including reused freelist pages.
            storage = added_pages * page_size
            method = "allocated_page_increment_upper_bound"
        db.execute("ANALYZE")
        queries = []
        for query in workload["queries"]:
            cases = []
            for params in query["cases"]:
                rows, vm, _ = execute(db, query["sql"], params, limits)
                truth = answer(rows, query["ordered"])
                samples = []
                for sample in range(limits["warmup"] + limits["samples"]):
                    observed, _, ns = execute(db, query["sql"], params, limits, count=False)
                    if answer(observed, query["ordered"]) != truth:
                        raise IndexTradeError("query answers changed during repetitions")
                    if sample >= limits["warmup"]:
                        samples.append(ns)
                db.set_authorizer(authorizer())
                try:
                    plan = db.execute("EXPLAIN QUERY PLAN " + query["sql"], params).fetchall()
                finally:
                    db.set_authorizer(None)
                cases.append(dict(answer_sha256=truth, rows=len(rows), vm_steps=vm,
                                  timing_ns=samples, median_ns=statistics.median(samples),
                                  explain_query_plan=plan))
            queries.append(dict(id=query["id"], vm_steps=sum(c["vm_steps"] for c in cases),
                                cases=cases))
        db.execute("BEGIN")
        write_vm = 0
        changes = 0
        write_results = []
        try:
            for write in workload["writes"]:
                for params in write["cases"]:
                    _, steps, _ = execute(db, write["sql"], params, limits, write=True)
                    write_vm += steps
                    changed = db.execute("SELECT changes()").fetchone()[0]
                    changes += changed
                    write_results.append(dict(changed_rows=changed,
                                              after_state_sha256=application_state(db, limits)))
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
        return dict(indexes=[c["name"] for c in indexes], index_bytes=storage,
                    storage_method=method, queries=queries, write_vm_steps=write_vm,
                    write_changed_rows=changes,
                    write_results=write_results,
                    weighted_read_vm=sum(q["vm_steps"] * spec["weight"]
                                         for q, spec in zip(queries, workload["queries"])))


def assess(result, baseline, workload):
    reasons = []
    if result["index_bytes"] > workload["budgets"]["max_index_bytes"]:
        reasons.append("storage_budget")
    if result["write_vm_steps"] > baseline["write_vm_steps"] * workload["budgets"]["max_write_ratio"]:
        reasons.append("write_budget")
    if result["write_results"] != baseline["write_results"]:
        reasons.append("write_answer_mismatch")
    for query, old, spec in zip(result["queries"], baseline["queries"], workload["queries"]):
        if [c["answer_sha256"] for c in query["cases"]] != [c["answer_sha256"] for c in old["cases"]]:
            reasons.append("answer_mismatch:" + query["id"])
        if query["vm_steps"] > old["vm_steps"] * spec["max_regression"]:
            reasons.append("query_regression:" + query["id"])
    result["rejected_by"] = reasons
    result["feasible"] = not reasons


def metric(result):
    return result["weighted_read_vm"], result["index_bytes"], result["write_vm_steps"]


def nondominated(results):
    return [r for r in results if r["feasible"] and not any(
        s["feasible"] and all(a <= b for a, b in zip(metric(s), metric(r)))
        and any(a < b for a, b in zip(metric(s), metric(r))) for s in results)]


def fingerprint(source):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(source) + suffix)
        if path.exists():
            if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
                raise IndexTradeError("source or sidecar must be a file no larger than 256 MiB")
            result[suffix or "database"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def snapshot(source, target):
    # Never open the user's source with SQLite: even a read-only WAL reader can
    # touch SHM. Copy the quiesced database + WAL into a private directory, let
    # SQLite recover that private WAL, then use backup for the logical snapshot.
    # A single .db file copy (or immutable=1 open) can omit committed WAL rows.
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=Path(target).parent) as folder:
        copied = Path(folder) / "copy.db"
        shutil.copyfile(source, copied)
        wal = Path(str(source) + "-wal")
        if wal.exists():
            shutil.copyfile(wal, Path(str(copied) + "-wal"))
        with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(target)) as dest:
                src.backup(dest)
                if dest.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise IndexTradeError("source snapshot failed integrity_check")


def advise(source, workload, output):
    """Measure a declared corpus on a quiesced database; reserve a new output directory.

    Returns an exhaustive measured optimum only after all finite sets finish.
    Resource exhaustion returns UNKNOWN with the best completed feasible incumbent.
    Input SQL may read application data. This is not an OS security sandbox.
    """
    validate_workload(workload)
    limits = DEFAULTS | workload.get("limits", {})
    source_label = Path(source).absolute()
    source = source_label.resolve(strict=True)
    output = Path(output).resolve()
    reserved = {str(Path(str(name) + suffix).resolve()).casefold()
                for name in (source, source_label) for suffix in ("", "-wal", "-shm", "-journal")}
    if str(output).casefold() in reserved:
        raise IndexTradeError("output collides with the source database or its SQLite sidecar namespace")
    if not source.is_file() or output.exists():
        raise IndexTradeError("source must be a file and output must not already exist")
    if Path(str(source) + "-journal").exists():
        raise IndexTradeError("rollback journal present; stop writers and recover before advising")
    before = fingerprint(source)
    output.mkdir(parents=False, exist_ok=False)
    (output / "INCOMPLETE.json").write_text(encoded(dict(state="INCOMPLETE")), encoding="ascii")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=".indextrade-", dir=output) as temporary:
        work = Path(temporary)
        base = work / "base.db"
        snapshot(source, base)
        with closing(sqlite3.connect(base)) as db:
            schema_check(db, workload["candidates"])
            db.execute("ANALYZE")
        choices = [combo for size in range(len(workload["candidates"]) + 1)
                   for combo in itertools.combinations(range(len(workload["candidates"])), size)]
        results = []
        stop = None
        for combo in choices:
            if len(results) >= limits["max_sets"] or time.monotonic() - started >= limits["max_seconds"]:
                stop = "portfolio_count_or_deadline"
                break
            path = work / "candidate.db"
            snapshot(base, path)
            try:
                result = measure(path, workload, limits, [workload["candidates"][i] for i in combo])
            except WorkLimit as exc:
                stop = str(exc)
                break
            finally:
                path.unlink(missing_ok=True)
            if not results:
                baseline = result
            assess(result, baseline, workload)
            results.append(result)
        if not results:
            raise WorkLimit("baseline could not be measured; no proposal")
        feasible = [r for r in results if r["feasible"]]
        best = min(feasible, key=lambda r: (*metric(r), r["indexes"]))
        names = best["indexes"]
        selected = [c for c in workload["candidates"] if c["name"] in names]
        naive_names = set()
        singletons = [r for r in results if len(r["indexes"]) <= 1]
        for index in range(len(workload["queries"])):
            winner = min(singletons, key=lambda r: (r["queries"][index]["vm_steps"], len(r["indexes"])))
            naive_names.update(winner["indexes"])
        naive = next((r for r in results if set(r["indexes"]) == naive_names), None)
        report = dict(format="indextrade-report/v1", status="UNKNOWN" if stop else "EXHAUSTIVE",
                      stop_reason=stop, sqlite_version=sqlite3.sqlite_version,
                      source_fingerprint=before, workload_sha256=digest(workload), limits=limits,
                      explored_sets=len(results), total_sets=len(choices), baseline=results[0],
                      naive_per_query_union=naive,
                      naive_note="best singleton per query, union; constraints checked after selection",
                      proposal=best, pareto=nondominated(results), portfolios=results,
                      elapsed_seconds=time.monotonic() - started)
        proposal = output / "proposal.db"
        snapshot(base, proposal)
        replayed = measure(proposal, workload, limits, selected)
        assess(replayed, results[0], workload)
        if not replayed["feasible"] or metric(replayed) != metric(best):
            raise IndexTradeError("materialized proposal differs from its measured portfolio")
        report["materialized_verification"] = replayed
        if fingerprint(source) != before:
            raise IndexTradeError("source changed; discard this incomplete experiment")
        (output / "proposal.sql").write_text("-- Review before deployment. Source was not changed.\n" +
                                             "\n".join(index_sql(c) for c in selected) + "\n", encoding="utf-8")
        (output / "workload.json").write_text(encoded(workload), encoding="ascii")
        (output / "report.json").write_text(encoded(report), encoding="ascii")
        artifacts = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                     for name in ("proposal.db", "proposal.sql", "workload.json", "report.json")}
        (output / "COMPLETE.json").write_text(encoded(dict(artifacts_sha256=artifacts)), encoding="ascii")
        (output / "INCOMPLETE.json").unlink()
        return report
