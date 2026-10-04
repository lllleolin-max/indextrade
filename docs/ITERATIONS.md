# Actual review and correction history

The initial implementation is not a correction cycle. Findings below were
reproduced before product changes and corrected in separate commits. Probe source
is `tests/test_review_regressions.py`; copy that final file into an exact historical
Git archive and select a method with unittest `-k`. Source replay is distinct from
the final ordinary-wheel installation. Raw before/after logs and canonical archive
replay JSON are retained outside this repository in the workspace's
`work/new-ten-projects/implementation/indextrade` evidence directory.

## Initial implementation

`3f4366c1fe4c09b55c3dbac27833a50b7fa235bc`: implemented finite actual SQLite
portfolio evaluation, guards, SDK/CLI, order-service integration and 12 passing
tests. No correction credit for initial features or their test count.

## 1. Committed WAL data omitted

Before `3f4366c1fe4c09b55c3dbac27833a50b7fa235bc`.
Probe `test_committed_wal_rows_are_in_snapshot_without_source_edits` created a table
and committed a row into an uncheckpointed WAL. The immutable source open produced
a snapshot without that table: `OperationalError: no such table: events`, exit 1.
This was a real correctness defect discovered by the implementer's self review.

After `fe9fe74fda12009f85a105de37718e5a6d9620b8`: copy quiesced database+WAL to a
private directory, open only the private copy, recover it and take a SQLite backup.
The same probe passed, exit 0, including exact database/WAL/SHM source-byte retention.
Live writer synchronization remains outside the contract.

## 2. Same read results, different actual write effects

Before `fe9fe74fda12009f85a105de37718e5a6d9620b8`.
Probe `test_index_changing_written_rows_is_rejected` uses actual DELETE with an
unordered LIMIT subquery. Without an index it deletes id 1; with a covering kind
index it deletes id 2. Both change one row, and pre-write query answers are equal.
The implementation incorrectly called the candidate feasible. Exact before archive
with the final probe failed, exit 1.

After product fix `8af9057031ec3020d7beffa9d6907b51b56915e5`: observe `changes()` and
the full typed logical application state after **each** actual write, including
accessible hidden rowids. Compare with baseline before rollback. Enable foreign
keys on every experiment connection. The same final probe on the exact after
archive passed, exit 0, rejecting `write_answer_mismatch`.

The first two-row test fixture independently executed SQLite without ANALYZE. After
the product correction, the tool's ANALYZE chose a different plan and that fixture
did not exercise the intended difference; the test failed and its raw log is kept.
Test-only follow-up `6504cb916b3afaf9d3b7a70fa6ecd38376d4e1d2` uses a 102-row
fixture and the same ANALYZE normalization in the independent oracle. This test
correction is not a second product cycle. Both historical product commits were
replayed with the identical corrected test and produced genuine FAIL→PASS.

## 3. Large integer limits escape the JSON error boundary

Before `6504cb916b3afaf9d3b7a70fa6ecd38376d4e1d2`.
Probe `test_huge_input_integer_is_a_controlled_domain_rejection` supplies `10**400`
as max_sets. `math.isfinite` coerced it to float before checking its bound and
raised uncaught OverflowError; the SDK domain-error assertion failed, exit 1.

After `832cc0712c14c9414891baf1740601a0075a7e2c`: validate type and integer range
before finite-float conversion. Same exact-archive probe passed, exit 0. CLI tests
also verify controlled JSON stderr, exit 2, and no output directory. This is an
input-boundary defect, not a core optimization failure.

## 4. Output directory blocks source application's WAL

Independent reviewer `/root/evidencecache_implementation` installed the ordinary
fe9fe74 wheel and ran `indextrade_output_sidecar.py`: `output=source.db-wal` was
created before validation, then snapshot failed. The source database bytes were
unchanged but the leftover directory caused a real resumed SQLite application
write to fail (`unable to open database file`). P2. Independent original probe exit 1.

Product before `832cc0712c14c9414891baf1740601a0075a7e2c`; after
`92f9cc772c8008e06b392c67b5ca066970e40f8a`. Preflight now rejects source database
and WAL/SHM/journal namespaces, considering both original source label and resolved
path, **before any mkdir**. The implementer's three-sidecar/resumed-WAL regression
passed. The independent reviewer installed the 92f9 wheel and confirmed their
unchanged original probe exit 0. Their initial test harness forgot SQLite close;
they corrected it and retained that separate harness error, not a product cycle.

## 5. SQL text is not structural table metadata

Before `92f9cc772c8008e06b392c67b5ca066970e40f8a`.
The implementer reproduced an ordinary table whose DEFAULT text contains
`'WITHOUT ROWID'`: substring classification excluded its real hidden rowid, so an
index-induced change in which rowid was updated was accepted. Probe
`test_without_rowid_literal_cannot_hide_real_rowid_write_changes`: FAIL, exit 1.
Separately, the independent reviewer installed the 92f9 wheel and found that SQL
LIKE `sqlite_%` wrongly excluded valid table `sqliteOrders` because `_` is a
wildcard. An indexed DELETE changed which row was removed, yet empty state hashes
matched. Their unchanged `indextrade_write_state_reserved_prefix.py` exited 1.

After `42551f14bbf9bae4e947d75986feaf068a51cdf2`: use PRAGMA table_list structural
WITHOUT ROWID bit and literal Python `startswith('sqlite_')` filtering. Minimum
SQLite is explicitly 3.37. The default-text rowid regression passed, exit 0; a
permanent sqliteOrders regression also passes. Both probes cover the same product
correction cycle, not two manufactured rounds. Independent final-wheel replay is
required before external acceptance.

## Remaining boundaries

These records do not assert absence of all defects, independent C/T/I scores,
customer adoption, production savings, hosted CI success or a public release.
State/hash checks prove consistency with a declared quiesced snapshot and corpus,
not upstream truth, all parameter values, OS isolation or hardware power-loss
recovery. Search is exponential and deliberately capped at ten candidates.
