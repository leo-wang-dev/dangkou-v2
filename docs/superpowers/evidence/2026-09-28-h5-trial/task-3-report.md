# Task 3 implementation report

Worktree: `/Users/a1/workSpace/Eryuan/dangkou-v2/.worktrees/h5-trial-20260928`.
Branch: `codex/h5-trial-20260928`. Implementation baseline `48ff1b9`. No production requests, live model invocation, live notifications, original environment file, provider substitution, deployment or push. Controller's plan-doc edit and pre-existing/generated audit files are not staged.

## Delivered contracts

- Immutable owned copy of original upload bytes, original SHA-256, checked isolated XLS conversion, persisted verified converted path/hash for stable recovery checkpoints.
- File-SQLite lease/heartbeat and cross-process atomic claim; stale owner fenced from ticket/status/outbox finalization. Parser output directories are per-attempt; validated checkpoint image hashes are checked and images copied into a reclaimed worker's private directory.
- Startup recovery plus recovery polling in the existing notification worker. No competing notification consumer. Real process-exit regression covers abandoned durable job reclamation. Legacy abandoned jobs without an owned source fail actionably instead of remaining dedup-locked.
- Exactly one durable completion/failure event per committed job outcome, atomic with ticket and status. Template-approved reminder also rolls back with approval if outbox insertion fails. Optional postcommit callback failures cannot relabel success. Default notifier not called twice. Existing no-secret queue materialization remains.
- `wait=true` retains durable notification even if inline result is returned (controller-approved semantics); raw repeated HTTP cancellation drains its request worker before closing SQLite. Existing Task1 worker helpers and snapshot logic unchanged. Private in-memory DB compatibility runs synchronously; production fileDB async behavior is tested separately.
- Partial model results preserve valid rows, invalid/empty rows and ambiguous identity errors are exposed as failed regions, image paths/content/count are verified, failures/coverage appear on approval UI and import stats, zero valid rows gives clear error/no ticket. Supplier/category identity and full-category stale snapshot remain; no missing-row delist. Stable draft keys include parser source/template/sheet identity.
- Physical candidate rows and logical product output are separately reported. Missing model source references yield explicit uncertain coverage; no claim all physical rows equal products. Completed sheet checkpoints survive crashes; failed-sheet output does not erase completed sheets. Legacy existing-category loop now accumulates all sheets.
- Explicit retries remain fresh whole-sheet/document uploads after pending approval/rejection, not targeted-region retries. Existing products are matched by supplier/model/spec; repeat token cannot execute, retries do not duplicate validated approved products.
- Style-only far columns pass. Substantive values/formulas/inline text and image anchors beyond 200 columns/20,000 rows reject. File/unpacked bytes, sparse cell count, worksheet XML size and merged area are bounded. Model process nonzero exit rejects output; timeout salvage remains explicitly incomplete and normally validated.
- Benchmark script defaults to structural-only and requires explicit `--parse --template-json` for an authorized model run. Product preparation avoids extracting/writing discarded images. Sheet model calls remain serial to bound resource use; no alternate provider or code-only product fallback.

## Red/green evidence

All Python commands used `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py` (external socket/DNS blocked). Child process crash test invokes only local db/import code and replaces the model builder with `os._exit`; fake subprocess tests never invoke Docker.

1. Initial red: `tests/test_import_recovery.py tests/test_partial_import.py -q`: **5 failed, 2 passed, 3 errors, 1 warning**. Missing recovery/join APIs, style-only257 rejection and missing partial failures reproduced; old callback path also raised from a successful ticket and unjoined old daemon warnings were preserved. Initial green same command: **7 passed**.
2. `tests/test_partial_import.py::test_failed_sheet_does_not_erase_completed_sheet -q`: red **1 failed** (B parser exception erased A); partial-sheet implementation green in combined focus.
3. `tests/test_partial_import.py::test_legacy_existing_import_accumulates_all_sheets -q`: red **1 failed**, actual only B vs A+B; fixed accumulated ParsedRows.
4. `tests/test_import_recovery.py::test_recovery_reuses_verified_conversion_for_stable_checkpoints -q`: red **1 failed** (2 conversions vs1); persisted verified conversion green in combined focus.
5. `tests/test_partial_import.py::test_reclaimed_attempt_copies_checkpoint_images_to_own_directory -q`: red **1 failed** (no recovered row); isolated immutable recovery copy green in combined focus.
6. `tests/test_partial_import.py::test_malformed_failure_coordinates_remain_renderable -q`: red **1 failed** (string coordinates); normalization green in final focus.
7. Final combined focus: `tests/test_supplier_products.py tests/test_partial_import.py tests/test_import_recovery.py -q`: **36 passed, 2 dependency warnings, 0.80s**.
8. FileDB wait cancellation: `tests/test_import_wait.py::test_cancelled_wait_keeps_connection_until_worker_and_completion_durable -q`: **1 passed**. It cancels the HTTP task twice during blocked parsing and asserts persisted ticketed status plus one outbox event.
9. Fence/CRUD/wait: `tests/test_partial_import.py tests/test_import_recovery.py tests/test_import_wait.py tests/test_crud_api.py -q`: **32 passed, 2 dependency warnings, 3.89s** (before final added regressions).
10. Related: `tests/test_dynamic_import.py tests/test_workbook_templates.py tests/test_import_wait.py tests/test_import_recovery.py tests/test_partial_import.py tests/test_agent.py tests/test_agent_import.py tests/test_agent_isolation.py tests/test_import_conflicts.py tests/test_shop_and_worker.py tests/test_quote_delivery.py tests/test_http_concurrency.py tests/test_h5_transactions.py -q`: **82 passed, 7 warnings, 6.12s** (before final added regressions).
11. `node tests/test_plugin_wechat_matrix.mjs`: **7 positive + 8 negative cases passed**. Obsolete omitted `phase`/`mode` fixture updated; product notification wording reports failed regions and avoids claiming an old async response proves parsing is still active.
12. `git diff --check`: clean.

Earlier related failures were retained in `task-3-evidence/task3-related*.log`: `test_async_import_without_category_creates_template_ticket`; obsolete `test_template_wait_returns_inline_and_skips_push`; `test_template_wait_timeout_falls_back_to_push`; `test_agent_rows_shape_and_image_main_prepend`; old `test_agent_failure_returns_none_and_import_raises`; `test_legacy_payload_agent_down_raises`; fake `test_agent_only_mounts_input_and_output_and_filters_service_secrets`; then `test_qwen_fast_path_discovery` due an overbroad initial test fake. Corrected deterministic fakes, joined fixtures, real XLSX/PNG evidence, explicit failure contract and subprocess CompletedProcess; no production parsing fallback.

## Full-suite history (no failures hidden)

- First `-q` full run was interrupted on controller instruction because it was draining already-diagnosed obsolete long waits against pre-fix source. It emitted failure indicators, not a valid final full count. Log `task-3-evidence/task3-full.log` retained. Independently reproduced CRUD shared-memory SQLite error and fixed stable draft-key fixture assumptions; corrected invalid PNG fixtures in search tests, explicit AI fake-enabled flag in manual-category helper test, and synchronous in-memory compatibility. No full-pass claim is based on that partial run.
- Fresh full `-q`: **1 failed, 555 passed, 1 skipped, 7 warnings in 40.90s**. Exact remaining failure: `tests/test_supplier_products.py::test_import_uses_row_supplier_then_document_vendor_then_category_default`, because its nonexistent `unused.xlsx` fixture now undergoes source-evidence discovery. Replaced with a real two-row XLSX retaining supplier-precedence assertions; final focus above passes. Log `task-3-evidence/task3-full-final.log` retained.
- Final post-fix full result appended below when complete.

## Real-source structural evidence and limits

Command: `python scripts/benchmark_import.py '<probe-approved WeChat directory>/剃须刀现货.xls' --sha256 67c2f515c6140bf09ce9a4ce597bcba7ceff2600dabc273f45d34fd1b43bb3a9 --expected-products 30`. Exact command source directory is the named path in `import-probe.md`; source only read/copied into temporary directory. Result: **30 physical candidate rows (5–34), 52 image objects**, conversion **2.212s**, discovery **0.097s**, total **2.309s**. `target_verified=false` because no model ran. Previously reported257 styled-column extent no longer rejects the source.

Command analogous for `华岳电器有限公司.xlsx`, SHA `d8f112a9b6e0a2cbbb71ea06358c80a5db596bb24304bbb8c192f8d01cacd675`: **63 physical candidate rows, 64 images**, total structural **0.038s**. This does **not** assert63 logical products; source grouping remains uncertain and model/merchant review is needed. JSON logs retained under `task-3-evidence/`.

Original18 source remains unavailable. Intended BAILIAN credential401/parser credential unavailable; no actual model accuracy/latency benchmark and no ten-minute end-to-end claim. Added/changed template image-role test produces a real PNG, verifies pixels/dimensions through Pillow, and asserts imported product image association; production HTTP image persistence/search tests use real PNG/TIFF artifacts. Semantic association on customer source images still needs authorized live-model validation.

Persistent upload/checkpoint retention has no automatic cleanup policy in this task. Lease recovery may wait up to90seconds after process loss plus polling. Whole-document retry may repeat successful model extraction after approval; identity/snapshot checks prevent duplicate writes. Completion delivery remains existing durable outbox retry semantics; exactly-once external visible delivery is not claimed. Dependency deprecations/unknown `real_agent` marker warnings remain visible; no background closed-connection warnings in the final focused/related runs.

## Final verification

`/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q` completed after all source/test changes: **558 passed, 1 skipped, 7 warnings in 40.12 seconds**. No failures or unhandled background-thread warnings. Log: `task-3-evidence/task3-full-green.log`. `node tests/test_plugin_wechat_matrix.mjs` passed7 positive+8 negative cases. `git diff --check` clean. Controller independent gate remains outside this implementer's remit.

Commit: `894cc93` — `Make incremental imports recoverable with partial results and durable completion` (26 deliberate source/test/doc files). No audit artifacts, ignored report/evidence, or controller plan edit included.

## Review fix round 1 / 5 (base `894cc939a7dc47ec5b2a31f944218007e4934cdc`)

Addressed the four Important findings in `task-3-review.md`, without business-scope additions or a broad-suite rerun:

1. Workbook evidence now includes lightweight image anchors and populated merged support rows without extracting image bytes. Product reference validation uses bounded workbook coordinates rather than requiring membership in the candidate-row heuristic. A real XLSX with A on row2 and its PNG anchored on image-only row3 survives through the real `agent.parse_dynamic` boundary with `[2,3]` and a verified image. Physical evidence remains distinct from logical product count.
2. `/import` uses a locked pending/running/cancelled dispatch handshake. Cancellation while pending both completes the drain signal and fences any subsequently invoked callback from accessing request resources. A callback that already acquired the running state is still drained through its real completion event; cancellation alone is never treated as evidence that a thread stopped. The held AnyIO capacity-token regression invokes the actual route endpoint against a file-backed fixture. The existing HTTP repeated-cancellation test for started work and request connection ownership remains green.
3. Real `agent.parse_dynamic` validates the top-level products list, leaving sibling item validation to the partial-row validator. The test stubs only `_run_container` and retains the real agent function, returning a valid object plus `None`; it asserts A survives and the second item gets an explicit failure.
4. Shared `preflight_workbook` contains compressed/unpacked bytes, sparse XML/drawing extents and sheet-count checks. Template building invokes it before Qwen/agent/fallback discovery; header evidence invokes it before openpyxl. Tests exercise a forbidden substantive column257 with Qwen-success, agent-success and fallback configurations and assert no model call occurs. A direct header-evidence test asserts no openpyxl load occurs. Positive Qwen/agent paths retain style-only257 compatibility.

Red commands (same offline Python prefix as above):

- `tests/test_partial_import.py -k 'image_only_row or mixed_malformed or template_preflight or header_evidence_preflights' tests/test_import_wait.py::test_cancel_before_dispatch_does_not_leave_unfinishable_worker -q`: **6 failed, 13 deselected, 2 warnings in0.48s**. The `-k` intentionally selected the product/template regressions; cancellation was run separately.
- `tests/test_import_wait.py::test_cancel_before_dispatch_does_not_leave_unfinishable_worker -q`: corrected actual-endpoint regression **1 failed, 2 warnings in0.92s**, proving the drain remained unset after cancellation and capacity release. The test sets the event only in failure cleanup after recording the failed condition, so red runs do not hang the suite. An earlier exploratory full-HTTP version incorrectly assumed outer HTTP cancellation guarantees no later dispatch; that assumption was removed, and its failed log retained as `task3-fix1-cancel-red.log`. No production change relies on that assumption.

Green commands:

- `tests/test_partial_import.py tests/test_import_wait.py -q`: **23 passed, 2 warnings in3.68s**.
- `tests/test_partial_import.py tests/test_import_wait.py tests/test_import_recovery.py tests/test_workbook_templates.py tests/test_dynamic_import.py tests/test_agent.py tests/test_agent_import.py tests/test_agent_isolation.py tests/test_http_concurrency.py tests/test_request_lifecycle.py tests/test_crud_api.py -q`: **88 passed, 7 warnings in5.85s** before two added positive style-tail path cases. Final result appended below.

Minor marker/dependency warning cleanup remains Task6 as requested. No network/model/container invocation, subagents, deployment, broad test rerun, or changes to Task1 helpers. Plan-doc edit and audit artifacts stay unstaged. Exact logs retained in `task-3-evidence/task3-fix1-*.log`.

Final round1 related command (same eleven named files) completed **90 passed, 7 warnings in5.97s** after the positive style-tail cases. `git diff --check` clean. No broad-suite run was performed in this fix round.

Fix-round1 commit: `8c1af2fcb9bcfe0e870c7048bbb31950c0192477` — `Fix import support-row evidence, preflight and cancellation dispatch` (six scoped application/test files).
