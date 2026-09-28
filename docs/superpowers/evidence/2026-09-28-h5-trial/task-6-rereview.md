# Task 6 fix-round 1 re-review — b60fd2d..0c8e9f8

**Scoped spec verdict: Approved — I1, I2, I3 and I4 ADDRESSED.**

**Scoped code-quality verdict: Approved.** No new blocking defect found in the fix package. This verdict does not close the explicitly deferred photo-cancellation/M1 findings or establish live deployment acceptance.

## Finding verdicts

### I1 — ADDRESSED: actual execution survives business-lease expiry

`catalog/ingest.py:71` acquires host/document ownership before the business claim and thread dispatch; `:166` activates that ownership throughout the existing synchronous import pipeline. `catalog/parser_execution.py:109` uses both a physical slot flock and a document flock. An expired SQLite lease alone can no longer start a second worker, including when configured capacity is2.

`catalog/agent.py:165` durably records container identity before launch and passes both ownership descriptors to the Docker CLI. Its cleanup at `:177` delegates to `catalog/parser_execution.py:50`, which verifies exact container name and execution label, removes that specific owned identity, then confirms absence. A disappearing supervisor/CLI alone does not clear an orphan marker. Unknown daemon state, foreign identity, absent orphan/dispatch ambiguity and uncertain conversion state fail closed. `Slot.close` releases local descriptors but deliberately preserves unresolved child metadata; admission checks it before physical-slot reuse and scans same-document orphan markers across all slots before selecting another slot. The conversion path also records a marker and passes ownership descriptors (`catalog/ingest.py:41`).

The focused tests cover live parser plus expired lease at capacities1/2 (`tests/test_import_admission.py:60`), real local supervisor exit with surviving child/inherited locks (`tests/test_parser_execution.py:9`), inherited descriptor lifetime, identified orphan cleanup, absent/unknown daemon, foreign identity and same-document uncertain marker at capacity2 (`:76`). The adjusted business-fencing test still asserts that a late loser cannot replace the winner or duplicate the ticket/outbox. Conservative manual reconciliation after genuinely ambiguous child state is now explicit behavior, consistent with the controller's fail-closed ruling.

### I2 — ADDRESSED: shared host capacity across tenant databases

`.env.example:73` supplies a single persistent host authority directory. `catalog/parser_execution.py:20` rejects missing/relative state configuration; `:109` arbitrates all physical slots under the authority lock and persists a shared capacity value, rejecting disagreement among processes using that authority. The tenant business DB no longer defines the actual resource boundary. Direct parser calls enter the same authority (`catalog/agent.py:114`).

`tests/test_import_admission.py:79` verifies two distinct real SQLite databases share capacity, and the tenant-provisioning assertion verifies that isolated DB paths retain the same inherited host state directory (`tests/test_merchant_onboarding.py`, changed provision test). Operations documentation explains the required common directory/local flock filesystem and coordinated quiescent capacity changes. As with other deployment configuration, actual host path/filesystem/permission consistency remains a live acceptance item; it is not claimed from tests.

### I3 — ADDRESSED: existing deploy caller supplies its configured environment safely

`deploy/deploy.sh:23` now explicitly invokes `scripts/preflight.py --env-file .env`. `scripts/preflight.py:10` parses the deliberately selected file as assignment data, handles comments/quotes/export syntax, and never shell-evaluates values. Diagnostics for syntax errors identify line numbers rather than values. Default preflight remains env-only and read-only.

`tests/test_deploy_preflight.py:55` exercises a placeholder file containing literal command-substitution text, verifies that its sentinel is not created, and captures the deployment SSH heredocs through hermetic SSH/rsync stubs. The remote payload is never evaluated; sudo/systemctl/install stubs fail. This directly repairs the caller contract without repeating the earlier host-installer incident.

### I4 — ADDRESSED: dynamic tenants own background lifecycle

`scripts/run_merchant_runtime.py:88` now launches API, notification/import recovery, independent index repair and guest sweep together. A DB-specific runtime flock is inherited by every managed role. Partial launch failure stops started children; the existing supervisor's any-child-failure check now covers the complete group and retains its30-second retry policy. `:77` closes the parent lifeline, terminates/reaps children, and only then closes the parent's runtime lock.

`scripts/run_managed_child.py:17` watches a read-only pipe endpoint; the parent write endpoint is not passed to child processes. Parent loss therefore triggers SIGTERM and an8-second forced process-exit fallback. Child-held runtime ownership prevents another group from overlapping surviving role processes. Parser descendant/container safety is independently retained by the I1 execution ownership and markers. `scripts/run_guest_sweep.py:13` adds per-DB sweep ownership, and `:27` uses the tenant DB/photo environment.

`tests/test_merchant_runtime_workers.py` covers all four role commands, partial-spawn cleanup, actual local wrapper termination on pipe EOF, real temporary-DB sweep, duplicate sweep/group locks and reacquisition after stop. The supervisor test at `:66` exercises whole-group failure, restart and final shutdown with fake roles. Existing notification/index locks remain intact.

## Scope and evidence

- Read the fix instructions, appended implementation report and the supplied sanitized fix diff. No raw/history credential section or original `.env` was accessed. No source/index/HEAD mutation; only this report was written.
- No suites, builds, installers, services, network/provider/email operations or new probes were run. The reported final affected covering run is **108 passed, 2 existing dependency warnings**; earlier red/failing results and the original installer incident remain preserved in the implementation report.
- One focused unchanged-code interface check searched `catalog/dynamic_import.py` for agent call sites and thread/executor boundaries: template/product parser calls at167/314 are synchronous, consistent with the new execution ContextVar being activated around the import worker. No changed-function rereads or broad branch review were needed.
- The prior confirmed direct-photo cancellation defect and **M1 multipart-limit localization remain open for the final correction wave**. M2 upstream dependency warnings remain explicit, without suppression. Task4B account-history choice remains pending.
- Actual Docker daemon/container behavior, host flock/filesystem configuration, native systemd, real bridge/model/mail, server access, original workbook/performance and device/TLS acceptance remain unverified. Offline ownership seams and managed local child tests do not substitute for those acceptance steps.
