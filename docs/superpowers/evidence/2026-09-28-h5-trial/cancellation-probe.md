# Focused direct endpoint-task cancellation probe

Outcome: **confirmed for both H5 and central userapp on snapshot b114f34**. This is one named final-review risk probe, not a repeat of the completed Task1 gate. No production code, existing tests, git index, HEAD or branch state was changed.

## Scope and reproducibility

- Snapshot: `b114f340ca4c29c40795920c3cd859d167940a02`.
- Probe script: `/tmp/dangkou-direct-cancel-b114f34/test_direct_endpoint_cancel_probe.py`.
- Snapshot root: `/tmp/dangkou-direct-cancel-b114f34`.
- Python: `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python`, version 3.12.14.
- Dependencies: AnyIO 4.15.1, Starlette 1.6.0, FastAPI 0.141.1, HTTPX 0.28.1.
- Run exactly once (two parameterized surfaces); no soak, suite rerun, network, real model, child provider or messages. Offline runner blocks external sockets/DNS; probe additionally rejects `subprocess.Popen`. Real file SQLite, actual registered photo routes, actual application middleware. Fake image model is held with a bounded threading event. All waits are bounded (model hold 5 s; entry/exit waits 2 s with 2.5 s async bound; request finish 1 s).
- Only tracked `catalog`, `static`, and relevant existing test files were copied into the isolated directory. No entire-repository archive, credential file, `.env`, or `OVERNIGHT-PLAN.md` was read or copied. The ignored report is the only active-worktree write.

Commands:

```sh
mkdir -p /tmp/dangkou-direct-cancel-b114f34
git -C /Users/a1/workSpace/Eryuan/dangkou-v2 archive b114f34 catalog tests/conftest.py tests/test_http_concurrency.py tests/test_userapp.py static | tar -x -C /tmp/dangkou-direct-cancel-b114f34
# Probe script was authored at the path above; it does not run the copied test suite.
cd /tmp/dangkou-direct-cancel-b114f34
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q -s test_direct_endpoint_cancel_probe.py
```

## Observation method

The probe decorates `anyio.to_thread.run_sync` only to capture the current downstream endpoint task, passed model limiter, and context-local request connection; its worker decorator records actual callback completion/errors. It still awaits the original AnyIO implementation with the original limiter. Other thread dispatch is forwarded unchanged. A real `sqlite3.Connection` subclass records `close()` while retaining actual SQLite behavior. The request uses HTTPX ASGITransport against the complete app, including database middleware, rather than invoking the helper directly. The photo route's scope exposes the unmodified `database_worker_done` event.

H5 request: create a genuine guest capability and set notes mode, then POST `/cs/chat/test-shop/photo`. Central request: POST `/guest`, then POST `/photo`. Both submit a valid JPEG. The fake model returns a deterministic sample item after release; its second invocation is the ordinary extraction/review path and returns immediately because release is already set.

## Exact observed sequence

1. Actual photo worker enters held fake model. The request SQLite connection is open, the application's done event is unset, and the model limiter has one borrowed token.
2. Call `cancel()` on the explicitly captured downstream endpoint `asyncio.Task`. Assert this is not the outer HTTP client task. Do not cancel the outer request or an AnyIO cancellation scope.
3. Before releasing the fake model: the endpoint task finishes, wrapper done event becomes set, actual worker callback has **not** finished, middleware closes its actual request SQLite connection, and the outer request returns HTTP 500 without being cancelled.
4. The model limiter reports zero borrowed tokens while the actual worker is still held. A replacement borrower can immediately acquire and release a token, confirming actual admission rather than merely a counter observation.
5. Release the model. Both actual worker callbacks subsequently fail with `sqlite3.ProgrammingError: Cannot operate on a closed database.` Both note tables remain empty.

Exact substantive output (timings omitted here only for readability; the command printed them):

```text
PROBE {"surface": "h5", "while_model_held": {"http_status": 500, "outer_cancelled": false, "endpoint_done": true, "wrapper_done_event": true, "actual_thread_done": false, "request_connection_closed": true, "model_limiter_borrowed_tokens": 0, "replacement_permit_acquirable": true}, "thread_error_after_release": "ProgrammingError: Cannot operate on a closed database.", "persisted_note_count": 0}
PROBE {"surface": "central", "while_model_held": {"http_status": 500, "outer_cancelled": false, "endpoint_done": true, "wrapper_done_event": true, "actual_thread_done": false, "request_connection_closed": true, "model_limiter_borrowed_tokens": 0, "replacement_permit_acquirable": true}, "thread_error_after_release": "ProgrammingError: Cannot operate on a closed database.", "persisted_note_count": 0}
2 passed in 0.33s
```

Here "passed" means the focused test successfully asserted the unsafe lifecycle; it does not mean the application cancellation behavior is correct.

## Impact and smallest correction requirements

Locations in the snapshot: `catalog/api.py:118–125` and `catalog/userapp.py:119–126`. Their `finally: done.set()` signifies async-wrapper exit, not actual worker exit. Raw native cancellation interrupts the AnyIO await despite its ordinary cancellation shielding, releases its capacity token, and lets database middleware trust the premature done signal. This is a reproduced use-after-close of a request-owned connection and a permit released before the protected work ends. Repeating such cancellations could admit additional model work while older threads remain alive; no repeated load experiment was needed or run. There is no claim of persisted corruption or actual shutdown frequency.

Minimal correction must preserve **both** actual thread completion and permit ownership:

- Signal completion in the actual worker callback, with a locked pending/running/cancelled-before-dispatch handshake (the narrow existing example is `catalog/api.py:493–516`, import wrapper), so queued cancellation neither hangs draining nor runs against released resources.
- Keep the limiter-owning `run_sync` operation alive until the worker truly exits. One candidate is a separately owned runner task shielded from direct endpoint cancellation, then cancellation-resistant draining/joining before the endpoint re-raises cancellation. Alternatively use explicitly managed permit ownership whose release is coupled to actual work completion. Validate any choice under both direct native cancellation and existing AnyIO cancellation semantics.
- Merely moving `done.set()` into the thread and draining the event is insufficient for the model-capacity invariant: cancellation can already have exited `run_sync`'s limiter context before that draining begins. The import path has no dedicated model limiter and therefore is only a completion-handshake comparison, not proof of model-permit safety.

No correction was implemented. The final whole-branch reviewer should carry this evidence forward. Existing outer-request cancellation and AnyIO level-cancellation scenarios were deliberately not repeated; direct downstream task cancellation is a separate confirmed boundary. Production shutdown behavior, other cancellation sources, other endpoints, and pre-dispatch races were not exercised, so their incidence/outcomes remain unclaimed.
