# Task 1 — H5 transaction durability and HTTP isolation

## Result

Implemented in `32d8890` (`Commit H5 request transactions and isolate vision work`). File-backed H5 requests now commit on a successful response and roll back on an error. A failed customer turn no longer commits the visitor created earlier in that turn. The central photo tool uses a separate SQLite connection per request; its writes commit only on success. Both H5 and central photo vision work run in bounded worker threads, leaving the event loop available. H5 vision and candidate lookup finish before creating the visitor row, avoiding a write lock during a slow vision call.

## Red/green evidence

All commands were run from this worktree with `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py`. The runner now rejects external DNS/socket use in the pytest process; it does not establish what a child process attempted.

- Red: `tests/test_h5_transactions.py -q` → **3 failed**. A photo returned HTTP 200 but the independent SQLite connection saw zero `cs_note` rows; boss handoff returned HTTP 200 but saw zero `cs_outbox` rows; a failed turn left a committed visitor row.
- Red: `tests/test_http_concurrency.py -q` → **2 failed**. Both H5 and central photo vision fakes blocked the event loop until their five-second wait expired.
- Green after the first H5 fix: `tests/test_h5_transactions.py tests/test_http_concurrency.py::test_h5_health_responds_while_vision_is_blocked -q` → **4 passed**.
- A stronger concurrency assertion, requiring another H5 visitor's language write while vision was waiting, failed before moving H5 extraction ahead of its first write. After that change, `tests/test_h5_transactions.py tests/test_http_concurrency.py -q` → **5 passed**.
- Final focused run: `tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_userapp.py -q` → **19 passed**, two existing Starlette deprecation warnings. The userapp failure test sends a later successful code request to catch accidental commits of a failed photo's partial note.

## Full offline suite

- First full run, after the implementation and initial regressions: `-q` → **499 passed, 1 skipped, 6 warnings** in 108.73s.
- Final full run, after strengthening the rollback regression: `-q` → **498 passed, 1 failed, 1 skipped, 7 warnings** in 131.17s. The only failure was `tests/test_dynamic_import.py::test_async_import_without_category_creates_template_ticket`: its two-second polling window ended while a background import was still `parsing`. Captured output showed an HTTP 401 from AI header discovery; that output alone does not establish its network provenance. Another background import thread later touched a closed fixture connection. No Task 1 files appear in that stack.
- Isolated rerun of that failure: `tests/test_dynamic_import.py::test_async_import_without_category_creates_template_ticket -q` → **1 passed** in 2.44s. This is a timing-sensitive import test outside Task 1; the controller is carrying it into import/test-environment work.

## Files and design choices

- `catalog/api.py`: success commit in request middleware, error rollback, bounded worker offload for H5 message/photo turns; visitor creation is included in the turn transaction.
- `catalog/csbot.py`: photo preparation accepts an unbound upload before visitor creation, keeping slow vision/candidate lookup ahead of the first H5 write.
- `catalog/userapp.py`: per-request connections through a context variable, response-bound commit/rollback, bounded worker offload for photo extraction.
- `tests/test_h5_transactions.py`: file-backed independent-connection persistence and rollback regressions for H5 and central photo.
- `tests/test_http_concurrency.py`: blocked fake vision does not stall H5 health, another visitor's write, or the central guest endpoint.

The worker limit is four model/photo turns per app. It is a technical concurrency cap, not a guest usage quota. Request SQLite connections are never used by two workers at the same time.

## Remaining considerations

The database transaction does not atomically include photo files on disk; a later failure can leave an orphan upload. The initial commit still allowed text-model waits to retain SQLite's writer lock and cancellation to close a live worker connection; both were fixed in the review round below.

## Review fix round — contention and cancellation

Implementation and regressions committed as `1743b55` (`Keep text planning and cancelled workers outside live DB locks`).

The review reproduced two defects after `32d8890`: a blocked text model held the SQLite writer lock, making another visitor's language request return 500 after about 5.17 seconds and delaying an independent note edit and health read; cancellation closed the request database while its vision worker was still running. New regressions failed on both cases before this fix. A language-change rollback regression also failed because its helper committed before the HTTP response. A busy-writer regression returned 500 after the original five-second timeout before the bounded conflict response was added.

H5 text now plans its complete turn against a private in-memory SQLite snapshot. Model calls and remote catalog reads are cached, including errors. After planning, it acquires a real `BEGIN IMMEDIATE` writer lock, checks `PRAGMA data_version` on the same connection, and replays the turn with cache-only model/catalog access. If the data changed, it replans up to three times while reusing identical cached results; further churn or a busy writer returns HTTP 409 for a client retry. Any replay error rolls back the request transaction. This copies the shop database for each text turn, trading memory/copy time for a short writer phase. The remote catalog result is frozen for that turn; the replay never waits on a remote read while holding the writer lock.

H5 and central-tool offloaded workers now publish completion through request state. On cancellation, middleware waits for the worker under a cancellation shield, rolls back, then closes the connection. H5 note PATCH and language writes, plus central-tool authentication database work, run off the event loop. The bounded model/photo pool stays occupied until its worker actually exits; unrelated database work uses the ordinary thread pool. H5 language changes now rely on the response transaction rather than committing inside the helper.

The focused red run of the three initial review regressions (`tests/test_http_concurrency.py` text/cancellation cases) produced **3 failed**. The focused red language-change test showed a committed visitor after HTTP 500; the busy-writer test showed HTTP 500 after 5.47 seconds. Final amended-code command: `tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_csbot.py tests/test_purchase_conversation.py tests/test_cs_api.py tests/test_userapp.py tests/test_customer_catalog.py tests/test_release_gates.py tests/test_round3_regressions.py -q` → **138 passed, 2 existing Starlette deprecation warnings** in 2.88 seconds. Regressions cover independent language write/PATCH/read during blocked text, blocked remote lookup, same-visitor replan, no duplicate identical model/remote calls after unrelated writes, bounded version and busy conflicts, replay failure rollback, overlapping success/failure, and cancellation rollback for both apps. No full-suite rerun was requested for this scoped review fix.
