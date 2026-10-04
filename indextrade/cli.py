"""JSON CLI with explicit incomplete/unknown experiment outcomes."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .engine import IndexTradeError, advise, encoded


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IndexTradeError("duplicate JSON property: " + key)
        result[key] = value
    return result


def read_workload(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise IndexTradeError("workload exceeds 1 MiB")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measured guarded SQLite index portfolios")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("advise")
    run.add_argument("source", type=Path)
    run.add_argument("workload", type=Path)
    run.add_argument("output", type=Path)
    demo = sub.add_parser("pilot", help="create and execute a synthetic order-service integration")
    demo.add_argument("output", type=Path)
    demo.add_argument("--orders", type=int, default=1200)
    args = parser.parse_args(argv)
    try:
        if args.command == "pilot":
            from .pilot import pilot
            result = pilot(args.output, args.orders)
        else:
            result = advise(args.source, read_workload(args.workload), args.output)
        print(encoded(result))
        return 4 if result.get("status") == "UNKNOWN" else 0
    except (IndexTradeError, OSError, sqlite3.Error, UnicodeError, RecursionError, ValueError) as exc:
        print(encoded(dict(error=type(exc).__name__, message=str(exc))), file=sys.stderr)
        return 2
