### Spec Compliance

- ❌ Issues found: Task 3 does not yet preserve all valid partial results or safely finish every cancelled import request. Workbook preflight is not integrated into the normal template path. Four Important findings below.
- ⚠️ Live model accuracy/image association, original 18-product input, and the 30-product/600-second target remain unverified, as correctly disclosed in `docs/superpowers/2026-09-28-import-recovery-contract.md:23` and `scripts/benchmark_import.py:50`. Structural 30-row/52-image/2.309-second evidence is not live verification. These are Task 6 dependencies, not additional Task 3 blockers.

### Strengths

- Ticket creation, terminal status, and outbox insertion share a transaction and recheck the current lease owner before committing (`catalog/ingest.py:187`, `catalog/ingest.py:197`). Rollback/recovery and stale-owner tests exercise real SQLite (`tests/test_import_recovery.py:35`, `tests/test_import_recovery.py:71`).
- Template-approved reminders now participate in the approval transaction (`catalog/tickets.py:85`, `tests/test_import_recovery.py:151`). Optional callbacks run after durable completion and cannot relabel success (`catalog/ingest.py:210`, `tests/test_import_recovery.py:25`).
- Owned source copies, verified conversions, isolated attempt directories, and copied checkpoint images address recovery and artifact ownership (`catalog/ingest.py:102`, `catalog/ingest.py:158`, `catalog/dynamic_import.py:279`, `catalog/dynamic_import.py:292`). The process-exit and artifact-isolation regressions test meaningful behavior (`tests/test_import_recovery.py:110`, `tests/test_partial_import.py:153`).
- Incremental classification leaves delist empty, repeated approvals/reimports are tested, and failed sheets retain successful siblings (`catalog/dynamic_import.py:97`, `tests/test_partial_import.py:56`, `tests/test_partial_import.py:108`). The approval UI exposes failures and explicitly distinguishes physical rows from products (`static/index.html:325`).
- Style-only excess and substantive cells/image anchors have focused tests (`tests/test_partial_import.py:17`, `tests/test_partial_import.py:25`, `tests/test_partial_import.py:142`). The benchmark defaults to structural-only and requires explicit model opt-in (`scripts/benchmark_import.py:39`, `scripts/benchmark_import.py:61`).

### Issues

#### Critical (Must Fix)

- None found.

#### Important (Should Fix)

1. **Valid products spanning image-only rows are rejected.** `catalog/dynamic_import.py:307` builds the allowed source-row set with `include_images=False`; `catalog/dynamic_import.py:337` then rejects the entire product if any referenced row is absent from that set. The new prompt explicitly requires all covered image/specification rows (`catalog/agent.py:43`). A valid generated workbook with headers at row 1, product A at row 2, and its image anchored at row 3 produced `candidate_rows=1`; valid model output with `source_rows=[2,3]` produced zero accepted products and the “来源行不在已观察到的数据区域” failure. Row candidates are an incomplete coverage heuristic, not a sufficient validity boundary. Preserve lightweight anchor/merged-region evidence or distinguish valid workbook coordinates from candidate product rows; do not discard otherwise valid model output merely because the heuristic omitted a supporting row. Add a real workbook regression with a product spanning data and image-only rows.

2. **Cancellation before thread dispatch leaves `/import` waiting forever.** `catalog/api.py:479` registers an unset completion event, but only the worker sets it (`catalog/api.py:485`). If cancellation occurs while `anyio.to_thread.run_sync` waits for a capacity token (`catalog/api.py:487`), that worker never starts; the unconditional `drain_worker(done)` at `catalog/api.py:489` cannot finish. Focused reproduction held the default AnyIO limiter's sole token, invoked the actual `/import` endpoint, cancelled the task, then released the token. After release the task remained unfinished and the completion event remained unset; manually setting the event was required solely to clean up the probe. No worker subsequently ran. In HTTP use the request middleware also waits on this event, retaining its connection/request resources. Distinguish cancelled-before-submission from actually running work and drain only a worker that can signal completion, without reintroducing premature connection closure. Extend the started-worker cancellation regression with this capacity-wait case.

3. **One malformed product still discards every valid sibling before partial validation.** The real `agent.parse_dynamic` boundary at `catalog/agent.py:62` rejects a products array unless every element is a dictionary. Consequently the new per-item failure handling at `catalog/dynamic_import.py:328` never sees mixed object/non-object results; the enclosing catch at `catalog/dynamic_import.py:314` returns no rows. With only `_run_container` stubbed to return a valid A product plus `null`, the production chain returned zero accepted products and one whole-source “Agent 输出必须包含 products 对象数组” error. This misses the mixed-valid/invalid-row requirement. Validate the top-level list at the agent boundary and let the new row validator retain good objects and expose each invalid sibling; test through real `parse_dynamic`, not a replacement for it.

4. **The default template import bypasses the new workbook safety preflight.** `catalog/workbook_templates.py:192` invokes preflight only inside `discover_workbook`. `catalog/dynamic_import.py:521` first calls the Qwen template path, whose `extract_header_evidence` opens the workbook without preflight (`catalog/workbook_templates.py:269`); a successful Qwen or agent result never reaches `discover_workbook` at all (`catalog/dynamic_import.py:529`). A generated workbook containing substantive data in column 257 was rejected by direct discovery but accepted by production `build_template_payload` with only the header-model result and supplier inference stubbed. Thus template imports can accept forbidden extents, and oversized/expanded workbooks can reach openpyxl before the new sparse XML/merged-area checks. This is a missing Task 3 integration, not a claim that the older discovery chain was introduced here. Run shared bounded preflight before every template/model evidence path; test successful model discovery as well as fallback.

#### Minor (Nice to Have)

- **Test output remains noisy.** `tests/test_agent_isolation.py:41` adds another unregistered `real_agent` marker warning. The retained final log reports five unknown-marker warnings and two dependency deprecations (`.superpowers/sdd/2026-09-28-h5-trial/task-3-evidence/task3-full-green.log:10`). Register the marker and track the dependency warnings; the supplied 558-pass result is valid evidence, but output is not pristine.

### Checks and Scope

- Read the supplied `48ff1b9..894cc93` diff in chunks; repeated only the dynamic-import segment truncated by tool output and narrowly named-risk/context checks. No git commands, code edits, full-suite reruns, live model calls, or subagents.
- Named-risk checks beyond complete diff hunks: source-row evidence versus image-only/merged support rows (`catalog/workbook_templates.py:230`); partial validation versus the real agent boundary (`catalog/agent.py:58`); import cancellation versus the existing drain/middleware contract (`catalog/request_lifecycle.py:8`, `catalog/api.py:119`); substantive preflight reachability through the normal template discovery chain (`catalog/dynamic_import.py:504`, `catalog/workbook_templates.py:269`). Also inspected cut-off classification/existing-category call context to assess repeated identity and whole-workbook parsing (`catalog/dynamic_import.py:44`, `catalog/dynamic_import.py:256`, `catalog/dynamic_import.py:590`).
- Ran only short local probes for the four uncovered doubts, using temporary generated XLSX/PNG files, in-memory SQLite, model-boundary stubs, and the actual import endpoint under a held AnyIO capacity token. No model/network process was invoked. An initial one-column fixture was unsuitable for the header heuristic; the source-row and mixed-result probes were corrected to a normal two-column workbook before drawing findings.
- Read the final retained full-suite log: **558 passed, 1 skipped, 7 warnings**, matching the report. Node **15 cases passed** remains supplied implementer evidence, not independently rerun. Earlier interrupted/failed suites remain documented; no contrary pass claim is inferred.
- Accepted review boundaries: durable completion remains queued for `wait=true`; explicit retry may reparse the whole document; legacy insufficient snapshots may require 409/reimport; cross-document parser admission is Task 6; the older Task 1 helper's separate cancellation risk is outside this task gate.

### Assessment

**Task quality: Needs fixes.**

The durable transaction/lease design and tests substantially improve recovery. The four focused failures undermine valid-row preservation, cancellation cleanup, and promised workbook limits, so Task 3 should be reviewed again after these are corrected.
