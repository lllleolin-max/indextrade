# The finite experiment contract

Version 1 input is UTF-8 JSON (BOM accepted, duplicate properties rejected by the
CLI, 1 MiB input cap). Required fields: `version: 1`, `queries`, `writes`,
`candidates`, `budgets`. Optional: `limits`. Unknown fields are errors. See
`indextrade.pilot.workload()` or the CLI pilot's `input.json` for a complete input.

Each query has `id`, `sql` (SELECT), `cases` (positional parameter arrays), positive
integer `weight`, boolean `ordered`, and `max_regression >= 1`. A query's guardrail
uses total VM instructions across its supplied cases relative to the no-candidate
baseline; it is not a worst-case latency or unobserved-parameter promise. Use a
separate query entry for a latency-sensitive parameter case. Each write has INSERT,
UPDATE or DELETE `sql` and `cases`; its effects are rolled back. Candidate indexes
contain `name`, `table`, `columns`, with ordinary ascending inherited-collation
B-tree semantics. Unique, expression, partial, descending or covering INCLUDE
definitions are unsupported. Caller supplies candidates; this is portfolio
selection, not automatic SQL parsing or generation of all possible indexes.

`budgets.max_index_bytes` is a nonnegative byte ceiling. `max_write_ratio >= 1`
bounds actual write VM work relative to the baseline. `max_sets` bounds completed
portfolios (1..1024), `max_seconds` is a cooperative measurement time allowance
(1..300), `max_statement_vm` is a cooperative SQLite progress-callback allowance
(1..20,000,000), `max_rows` caps returned rows per query/table-state scan
(1..200,000), `samples` is 1..9 and `warmup` 0..5. The callbacks are not an OS CPU,
memory, I/O or hard real-time sandbox. Counting uses an interval of 1; timing uses
interval 1000 and may perform up to 999 additional VM instructions between callbacks.

Inputs are ordinary tables only: no views, triggers, virtual tables or hidden/
generated columns. Identifiers are bounded ASCII. SQL is prepared by SQLite with
an authorizer: read SQL can only SELECT/READ main; write SQL additionally allows
INSERT/UPDATE/DELETE main. A small named deterministic builtin function allowlist
is explicit in `engine.FUNCTIONS`. PRAGMA, ATTACH, extension loading, custom and
volatile functions are denied. SQL prefix validation is not the security boundary.
This does not sandbox a compromised native SQLite library or malicious OS actor.
SQLite 3.37+ is required for structural `PRAGMA table_list` metadata. WITHOUT ROWID
is identified by that metadata, never a substring of schema SQL; literal `sqlite_`
prefix filtering preserves valid application tables such as `sqliteOrders`.

## Source and experiment state

Stop source writers for the whole operation. IndexTrade hashes database and
sidecars, copies the quiesced `.db` and any `-wal` into a private directory, lets
SQLite recover only that private WAL, and uses `Connection.backup` to produce the
logical snapshot. It never opens the user's source with SQLite, so even SHM receives
no tool writes. Opening the user's WAL database with `immutable=1` would omit WAL
contents on our observed runtime: round 1 fixes that defect. Source journals are
rejected rather than recovered in place. Source database/sidecar files each have a
256 MiB cap. Final fingerprints must match before any completion marker is written.
This detects ordinary concurrent changes; it cannot secure hostile modify-and-revert.

All portfolios begin from one normalized snapshot with ANALYZE statistics. Original
indexes remain; "baseline" means no additional candidate indexes. Automatic indexes
are enabled, 2 MiB SQLite cache and memory temp storage are fixed, and foreign-key
enforcement is enabled. Each candidate portfolio is actually built on a fresh copy,
then ANALYZE runs before the same corpus. Raw EXPLAIN QUERY PLAN rows are displayed
with the SQLite version; their unstable textual format is never parsed for safety,
cost or correctness.

Each SELECT is fully consumed, typed and compared across portfolios: NULL, integer,
finite real (hex representation), text, BLOB, row multiplicity. Bag semantics sort
typed encodings; ordinal semantics preserve order. An unordered LIMIT or incomplete
ORDER BY may choose different rows after indexing; such measured changes reject the
portfolio. Each actual write additionally records `changes()` and a typed full
logical application-state hash before rollback; rowids are included where exposed.
Round 2 repairs the original missing write-effect check. Writes and state scans are
separate: state-observation work does not inflate measured write VM work.

## Storage and optimization

When compiled in, DBSTAT reports each new index's allocated pages. Otherwise, before
ANALYZE and immediately after only CREATE INDEX, the increase in **used pages**
(`page_count - freelist_count`) times page size is a conservative incremental storage
upper bound. It includes schema overhead and counts reused freelist pages, unlike
file-size growth. No data pages are removed at that point. The method is explicit in
every result. The fallback is not a compressed, WAL, filesystem allocation or future
growth estimate; it can reject a feasible index because of conservative overhead.

Evaluate combinations by ascending cardinality. Feasible sets preserve read and
write results, fit storage/write ceilings and all measured query guardrails. Keep
the nondominated frontier in (weighted read VM, incremental bytes, write VM). Select
lexicographically by those three costs then index names. Every evaluated set appears
in the report. Finishing all 2^N sets proves the selected measured optimum over this
finite declared candidate list. Resource exhaustion means UNKNOWN, never an unseen
set's infeasibility. A feasible evaluated incumbent is still materialized and marked
complete; UNKNOWN is a complete report, not a global optimization certificate.

A second materialization replays the selected portfolio on a fresh snapshot and
checks its costs and semantic guards. `proposal.sql` is review input, not an execution
or deployment command. Revalidate statistics, workload, plans and migration locks
on the application environment before changing its database.

## Artifacts and exits

A new exclusive output directory is required. Existing directories, symlink aliases
to existing directories, input collisions and files are rejected. `INCOMPLETE.json`
is created first. `report.json`, exact input, SQL and actual proposal database are
hashed in `COMPLETE.json`, which is published last. A failure or real process exit
can leave partial output and private working files; consumers require COMPLETE and
verify all hashes. Failed output is retained for inspection and never silently reused.

CLI emits escaped JSON, including Windows legacy streams. Success exits 0, UNKNOWN
exits 4, handled invalid input/I/O/SQLite failures exit 2. Argparse usage errors are
text with exit 2. No publish, provider request, deployment or external messaging occurs.
