# Laboratory integration and comparison

The packaged `indextrade pilot OUTPUT --orders 1200` creates a real SQLite order
service: 60 customers, 1,200 orders, 3,600 line items. Four application queries have
ten parameter cases: customer history, fulfilment, regional revenue join/aggregation,
and grouped pick lists. Five actual inserts/status updates execute and roll back on
every portfolio. Four candidate indexes yield sixteen combinations with shared
read effects, storage and write maintenance. Data, business amounts and workloads
are explicitly synthetic; there is no customer or revenue claim.

All policies see the same database snapshot, ANALYZE rule, SQL, parameter cases,
warmup/sample count, cache/temp/automatic-index settings and rolled-back writes.
Baseline keeps original indexes and adds none. The naive policy chooses each
query's best singleton candidate then unions them and measures that union;
it is an explicitly defined engineering shortcut, not a claim about all mature
advisors. Constrained IndexTrade checks storage/write/query/semantic guards across
the full candidate portfolios. No query result may be silently traded for speed.

`consumer()` independently calculates expected order totals and pick-list quantities
from generating arithmetic, then executes actual SQL against both databases.
`examples/verify_proposal.py` is a second independent stdlib program with no
IndexTrade imports: it consumes the full artifact manifest, reads actual queries,
compares typed row multisets/order and post-write states on separate copies, and
checks reported budget predicates. Its structural rowid handling uses SQLite
metadata rather than the implementation's historical broken SQL classification.

Per-case timing records contain every measured sample. VM counters are progress
callback work on the observed SQLite build, not economic units or CPU-cycle
guarantees. Timings include execute/fetch, a cooperative progress handler and no
hashing; timing observations are not the optimization objective. Full source
snapshot preparation, copying, index construction, ANALYZE and validation add
real overhead. A 1,200-order experiment is not production capacity certification.

Final versioned ordinary-wheel results are recorded in the handoff evidence and
`SELF_REVIEW.md`; do not inherit the initial pilot's metrics across code changes.
Required negative cases include zero storage/no improvement, write budget 1.0,
and max_sets=2 yielding UNKNOWN with a verified incumbent. The final pilot script
also varies the storage allowance on the same workload and preserves rejection
causes instead of claiming every index helps.

## Established work, checked 2026-10-04 UTC

SQLite [query planning](https://sqlite.org/queryplanner.html) explains index choice;
[ANALYZE](https://sqlite.org/lang_analyze.html) explains statistics and explicitly
does not guarantee every plan improves. [CREATE INDEX](https://sqlite.org/lang_createindex.html)
documents nonunique, multicolumn, expression/partial and collation semantics;
IndexTrade supports a narrow nonunique column subset.

[EXPLAIN QUERY PLAN](https://sqlite.org/eqp.html) output is unstable and used only
as raw human evidence. [DBSTAT](https://sqlite.org/dbstat.html) depends on a build
option, so observed storage method must accompany every budget. The supported
page-count/freelist fallback is described and exercised in DESIGN and tests.
[Progress callbacks](https://sqlite.org/c3ref/progress_handler.html) are cooperative,
approximate instruction intervals, not a hard CPU limiter.

The [experimental SQLite Expert](https://sqlite.org/cli.html#index_recommendations_sqlite_expert)
already offers index recommendations and optional sampled statistics.
[HypoPG](https://hypopg.readthedocs.io/en/rel1_stable/hypothetical_indexes.html)
already supports hypothetical planner indexes.
[CoPhy](https://arxiv.org/abs/1104.3214) already researches constrained index tuning.
[pganalyze's Indexing Engine](https://pganalyze.com/docs/indexing-engine) already
combines workload selection and write overhead. IndexTrade's measured local
snapshots, exact declared finite frontier and explicit UNKNOWN outcome are a useful
packaging/scope choice, not proof these systems lack those abilities. No patent,
world-first or vendor-performance superiority claim follows from documentation.

The sqlite3 shell executable was absent from this Windows host. SQLite Expert was
not executed, so its comparative performance is unknown. The executed baseline is
the documented same-workload singleton union. No third-party cloud service was
run, and no hidden production savings are extrapolated from the experiment.
