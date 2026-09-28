# Task 1 — H5 transaction durability and HTTP isolation

## Result

Implemented in `32d8890` (`Commit H5 request transactions and isolate vision work`). File-backed H5 requests now commit on a successful response and roll back on an error. A failed customer turn no longer commits the visitor created earlier in that turn. The central photo tool uses a separate SQLite connection per request; its writes commit only on success. Both H5 and central photo vision work run in bounded worker threads, leaving the event loop available. H5 vision and candidate lookup finish before creating the visitor row, avoiding a write lock during a slow vision call.

## Red/green evidence

All commands were run from this worktree with `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py`; the wrapper prevents external network traffic.

- Red: `tests/test_h5_transactions.py -q` → **3 failed**. A photo returned HTTP 200 but the independent SQLite connection saw zero `cs_note` rows; boss handoff returned HTTP 200 but saw zero `cs_outbox` rows; a failed turn left a committed visitor row.
- Red: `tests/test_http_concurrency.py -q` → **2 failed**. Both H5 and central photo vision fakes blocked the event loop until their five-second wait expired.
- Green after the first H5 fix: `tests/test_h5_transactions.py tests/test_http_concurrency.py::test_h5_health_responds_while_vision_is_blocked -q` → **4 passed**.
- A stronger concurrency assertion, requiring another H5 visitor's language write while vision was waiting, failed before moving H5 extraction ahead of its first write. After that change, `tests/test_h5_transactions.py tests/test_http_concurrency.py -q` → **5 passed**.
- Final focused run: `tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_userapp.py -q` → **19 passed**, two existing Starlette deprecation warnings. The userapp failure test sends a later successful code request to catch accidental commits of a failed photo's partial note.

## Full offline suite

- First full run, after the implementation and initial regressions: `-q` → **499 passed, 1 skipped, 6 warnings** in 108.73s.
- Final full run, after strengthening the rollback regression: `-q` → **498 passed, 1 failed, 1 skipped, 7 warnings** in 131.17s. The only failure was `tests/test_dynamic_import.py::test_async_import_without_category_creates_template_ticket`: its two-second polling window ended while a background import was still `parsing`. The captured output showed the existing AI header-discovery fallback receiving a blocked-network 401, and another background import thread later touched a closed fixture connection. No Task 1 files appear in that stack.
- Isolated rerun of that failure: `tests/test_dynamic_import.py::test_async_import_without_category_creates_template_ticket -q` → **1 passed** in 2.44s. This is a timing-sensitive import test outside Task 1; the controller is carrying it into import/test-environment work.

## Files and design choices

- `catalog/api.py`: success commit in request middleware, error rollback, bounded worker offload for H5 message/photo turns; visitor creation is included in the turn transaction.
- `catalog/csbot.py`: photo preparation accepts an unbound upload before visitor creation, keeping slow vision/candidate lookup ahead of the first H5 write.
- `catalog/userapp.py`: per-request connections through a context variable, response-bound commit/rollback, bounded worker offload for photo extraction.
- `tests/test_h5_transactions.py`: file-backed independent-connection persistence and rollback regressions for H5 and central photo.
- `tests/test_http_concurrency.py`: blocked fake vision does not stall H5 health, another visitor's write, or the central guest endpoint.

The worker limit is four model/photo turns per app. It is a technical concurrency cap, not a guest usage quota. Request SQLite connections are never used by two workers at the same time.

## Remaining considerations

The database transaction does not atomically include photo files on disk; a later failure can leave an orphan upload. H5 text turns are offloaded but can still hold SQLite's write lock while generating a model reply because the existing text flow logs the user before model work. Neither affects the tested H5 vision path; both warrant separate lifecycle work if strict file cleanup or concurrent text writes are required.
