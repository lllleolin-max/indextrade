# IndexTrade

Measure a **portfolio of SQLite indexes** against a real application's query and
write corpus before proposing a deployment. IndexTrade snapshots a quiesced source,
creates every allowed candidate combination on disposable copies, checks typed query
answers, and minimizes weighted read VM work subject to storage, write VM work, and
each query's regression ceiling. It materializes the winning proposal and writes
reviewable SQL; your source database receives no DDL or DML.

For an application team maintaining a local order service, a fast customer-history
query can still add storage, make status updates more expensive, or slow a regional
report. The output exposes those tradeoffs together rather than recommending an
index from one EXPLAIN plan.

```sh
python -m pip install .
indextrade pilot pilot-output --orders 1200
indextrade advise /path/to/quiesced.db workload.json new-output
```

The first command creates a real synthetic SQLite order service, runs its multiquery
workload, and independently checks invoices and pick lists against the materialized
proposal. No network, credentials, third-party runtime dependencies or production
data are required. `new-output` must not already exist; parents must exist.

```python
from indextrade import advise
from indextrade.cli import read_workload

report = advise("quiesced.db", read_workload("workload.json"), "new-output")
print(report["status"], report["proposal"]["indexes"])
```

Inspect `report.json`, `proposal.sql`, `proposal.db` and `COMPLETE.json`. The report
includes baseline, a naive best-single-index-per-query union, all tested portfolios,
the feasible Pareto frontier, answers, raw EXPLAIN rows, repeated timing observations,
and actual rolled-back write VM work. A complete finite search says `EXHAUSTIVE`;
an exhausted search says `UNKNOWN` with a measured feasible incumbent. This is an
optimum over **your candidate list and observed SQLite VM metric**, not all possible
indexes or universal production latency. A bounded statement can also fail before
any incumbent exists, leaving `INCOMPLETE.json` and no completion marker.

中文：在只读源数据库的工作副本上，真实执行多查询与写入语料，比较有限索引组合。
存储、写入代价和每个查询的退化上限共同约束建议；穷举未完成时明确输出 UNKNOWN。
SQL 建议需要应用团队审阅和部署，本工具不会自动修改原应用。

Supported: Python 3.11+ with SQLite 3.37+, ordinary SQLite tables and B-tree column indexes, bounded
SELECT SQL with a small deterministic function allowlist, positional scalar
parameters, and rolled-back INSERT/UPDATE/DELETE workloads. Views, triggers,
virtual/generated columns, extensions, nondeterministic functions, distributed
databases and live writers are outside this release. Stop writers while measuring;
the immutable read-only snapshot contract cannot validate hostile concurrent edits.
Typed answers preserve NULL, integer/real distinctions, text, BLOB and duplicate
rows; choose bag semantics unless you require a deterministic total ORDER BY.

SQLite's [query planner](https://sqlite.org/queryplanner.html) already chooses access
plans; the experimental [SQLite Expert](https://sqlite.org/cli.html#index_recommendations_sqlite_expert)
already recommends indexes. [HypoPG](https://hypopg.readthedocs.io/en/rel1_stable/hypothetical_indexes.html)
and [CoPhy](https://arxiv.org/abs/1104.3214) establish mature hypothetical-index and
constrained index-advisor work. [pganalyze](https://pganalyze.com/docs/indexing-engine)
already combines workload/index selection and write overhead. IndexTrade does not invent index advising. Its narrow
pilot is actual local portfolio execution, semantic preservation, write/storage
constraints and an explicit unfinished-search outcome. This is synthetic laboratory
evidence; customers, revenue, willingness to pay and production savings are unknown.

[Design and format](docs/DESIGN.md) · [pilot evidence](docs/PILOT.md) ·
[review iterations](docs/ITERATIONS.md) · [self review](docs/SELF_REVIEW.md)

MIT licensed. `python -m unittest discover -s tests -v` runs the tests.
