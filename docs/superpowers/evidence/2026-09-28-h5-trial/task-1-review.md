### Spec Compliance

- ❌ Issues found: request-owned commits and bounded photo offload are implemented, but text model work retains the SQLite writer lock (`catalog/api.py:1205`), and cancellation closes a worker's live connection (`catalog/api.py:143`). These violate safe transaction/HTTP isolation.
- ✅ All required files have changes. File-backed photo durability, outbox durability, and rollback have independent-connection assertions (`tests/test_h5_transactions.py:45`, `:59`, `:69`). Both photo paths have a concurrency limit of four with no guest usage quota (`catalog/api.py:111`, `catalog/userapp.py:108`).
- ⚠️ `task-1-report.md:22` reports a final full-suite import failure followed by an isolated pass. This review does not establish its cause or attribute it to Task 1. Later supplier/import/expiration/batch/i18n/OTP work is outside this gate.

### Strengths

- `catalog/api.py:125`–`:130` commits successful requests and rolls back ordinary errors. Setting `_processing` before visitor creation (`catalog/api.py:1203`) prevents the earlier partial visitor commit.
- Photo extraction precedes visitor writes (`catalog/api.py:1228`–`:1237`), and the concurrency regression verifies another visitor can write while vision waits (`tests/test_http_concurrency.py:25`–`:41`).
- Userapp connections are request-owned (`catalog/userapp.py:115`). Its partial-photo failure regression follows the failed request with a successful request to catch unintended later commits (`tests/test_h5_transactions.py:105`).

### Issues

#### Critical (Must Fix)

- None established.

#### Important (Should Fix)

1. **Text model work retains the writer lock and can stall unrelated HTTP.** `catalog/api.py:1205`–`:1209`: visitor creation/update now runs with internal commits suppressed, and `_text_turn` also logs before model work. Thus a slow text model owns SQLite's writer transaction throughout its wait. A focused file-backed reproduction using the existing H5 fixture and blocked `llm.chat_text` made a different visitor's `/lang` return **HTTP 500 after 5.17 seconds**; the original text request later succeeded. A second reproduction seeded another visitor's editable note: their PATCH reached synchronous SQLite work in the existing async handler (`catalog/api.py:1274`, `:1292`), returned **500**, and delayed `/health` until **5.19 seconds** after PATCH started. This violates the binding responsiveness and independence requirements. Stage model work before acquiring the writer transaction, then atomically apply writes; keep potentially blocking SQLite work off the event loop. Add text contention coverage. The implementer's “remaining consideration” does not defer this requirement.

2. **Cancellation closes the connection before the offloaded worker finishes.** `catalog/api.py:143`–`:146` and `catalog/userapp.py:128`–`:130`: middleware cleanup closes request state as its await is cancelled, while the endpoint worker remains active. `anyio.to_thread.run_sync` alone does not protect this outer resource lifetime. A focused H5 reproduction cancelled the HTTP task after fake vision started, then released vision: `_prepare_photo` raised **`ProgrammingError('Cannot operate on a closed database.')`** at its subsequent candidate lookup. Ensure cancellation waits for worker completion before rollback/close, or make the worker own connection and transaction finalization; preserve rollback on cancellation. Add a regression asserting worker completion precedes close. Userapp has the same lifetime structure but was not separately reproduced.

#### Minor (Nice to Have)

- `tests/test_h5_transactions.py:86`: the other-visitor success/failure test issues sequential requests. It establishes durable success survives a later failure, but not overlapping isolation. Add overlapping coverage while fixing the lifecycle defects.
- `task-1-report.md:17`, `:21`–`:23`: validation contains deprecation warnings and a later import-thread warning. Reproductions also emitted the known Starlette TestClient deprecation warning. These are known validation noise/evidence limitations, not established new Task 1 regressions; the supplied baseline already had deprecations.

### Checks and Evidence Limits

- Read the supplied diff; recovered its initially truncated API/userapp section. Read the API middleware separately only because its diff hunk ended mid-function.
- Named risk: nested helper commits bypassing rollback. Checked directly invoked `cs_chat`/`CsBot` methods and commit occurrences in their photo/supplier/shop/i18n/purchase/merchant helpers. No additional definite mid-turn commit escape was established for message/photo.
- Named risk: contention across retained text transactions. Checked `db.connect` WAL/busy timeout, text write/model order, and the existing async note-edit call site. Ran two single-shot file-backed reproductions described above.
- Named risk: cancellation lifetime. Ran one blocked-photo cancellation reproduction and observed the worker's closed-connection error.
- Reproduction setup: run an inline Python script with `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python` from this worktree; instantiate `tests.test_h5_transactions.h5.__wrapped__(Path(temp_directory), pytest.MonkeyPatch())`, and use `httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test')`. For contention, replace `llm.chat_text` with a function that signals a `threading.Event`, waits on a release event, then returns `'{"actions":[]}'`. POST `/cs/chat/test-shop/message` with visitor `slow` and text `请记录一个黑色型号ABC`; after the start event, POST `/cs/chat/test-shop/lang` with visitor `other` and lang `English`. Release the model after the second response. For the read stall variant, seed another customer's draft note and an unexpired `cs_link`, start PATCH `/cs/link/{link_token}/note/{note_id}` with `{"field":"颜色","value":"黑色"}`, then after an asyncio 0.05-second sleep request `/health`; measure elapsed wall time from PATCH start.
- Cancellation scenario: patch `llm.chat_vision` to signal start, wait on a release event, then return one valid item. Wrap `H5Bot._prepare_photo` to record and re-raise exceptions. Start a photo HTTP task with the fixture JPEG, await the start event, call `task.cancel()`, allow 0.1 seconds for cancellation cleanup, release vision, and await the cancelled task while catching `asyncio.CancelledError`. The wrapper records the closed-database exception above. All waits had finite timeouts and no external model requests were made.
- No full-suite rerun, code edits, git mutations, or subagents. Implementer test counts were inspected, not independently regenerated. The only checkout write is this requested report.

### Assessment

**Task quality: Needs fixes.**

**Reasoning:** Durability and photo responsiveness changes are targeted, but confirmed text contention and cancellation use-after-close prevent approval of the transaction/HTTP isolation contract.
