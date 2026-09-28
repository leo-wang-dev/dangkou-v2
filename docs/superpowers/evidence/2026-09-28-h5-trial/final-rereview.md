# Final correction scoped re-review — 472bcc1..787fd89

**Scoped spec-compliance verdict: Approved. I1, I2 and M1 are ADDRESSED.**

**Scoped code-quality verdict: Approved. No new Critical, Important or Minor defect established in the fix package.** This closes the three concrete findings from `final-review.md` within the implemented scope. Task4B remains a required unanswered product choice; this is neither full-product completion nor live production acceptance.

## I1 — ADDRESSED: actual worker and limiter ownership survive endpoint cancellation

`catalog/request_lifecycle.py:33–90` now separates the endpoint await from a dedicated runner task. The endpoint shields that runner, and the runner owns the actual `anyio.to_thread.run_sync` call/limiter context. A locked pending/running/abandoned handshake prevents a cancelled callback that has not begun from accessing request resources. The callback sets completion in its own `finally`; running work is joined through repeated native cancellation under an AnyIO shield before cancellation propagates to middleware cleanup. Pending cancellation marks abandonment and sets completion even if the runner coroutine never started, preventing an unfinishable drain. Both common app wrappers now delegate to this helper (`catalog/api.py:122`, `catalog/userapp.py:128`).

The new regression in `tests/test_request_worker_lifetime.py` captures the actual registered `APIRoute.dependant.call` and cancels that endpoint task. Its separate observation around `run_sync` captures the runner only for connection/limiter observation; it does not accidentally cancel the new runner and call that an endpoint test. For both photo surfaces it holds a real callback, checks a live file-SQLite request connection and unset completion event after two native cancels, occupies the other limiter slots and verifies a real replacement acquisition raises `WouldBlock`. After release it checks no thread error, zero persisted rows, connection closure and no borrowed permit. Queued cases check no callback work and prompt cleanup while the unrelated holder remains occupied.

`tests/test_request_lifecycle.py` additionally covers an AnyIO cancelled scope retaining capacity through callback exit and cancellation before runner dispatch. Existing outer HTTP cancellation tests remain unchanged. These directly address the original resource/permit defect and the never-dispatched edge, rather than merely testing that a task returned. No additional probe was warranted.

## I2 — ADDRESSED: supported dynamic tenant chat reaches its selected tenant

`catalog/merchant_binding.py:130–174` adds an exact method-specific customer route allowlist. GET page/session/list-token, the required POST chat actions, and existing GET/PATCH list/photo/export routes are allowed; public administration and invalid methods are not. Upstream host remains literal loopback and port comes only from the stored running merchant row. Query host/port values cannot choose the destination. Multipart content type/boundary and customer language are preserved, photo bodies are bounded separately from small JSON bodies, and the forwarding timeout is180 seconds.

The management addition at `catalog/merchant_binding.py:236` admits only authenticated POST `cs/chat-token`. `static/index.html:718` now generates the selected `/merchant/customer/<mid>/cs/chat/<token>` link. Static chat prefixes every customer request, list iframe and session storage key (`static/cs/chat.html:67–73`, `:120–138`). The existing static list already carries its mount prefix for reads/edits/photos/downloads; its relative language assets remain correct. Uni APIs compose a validated tenant prefix and scoped session key (`frontend/src/api.js:27–50`); chat/list page options set that context. All existing CS API functions, including photo/upload/export, consume the same base. Fixed root `/cs/` behavior remains supported.

`tests/test_tenant_chat.py` exercises actual registered gateway and two file-backed tenant APIs: authenticated issue, page/session/language, multipart photo, pending discard, mode, batches, message, list, photo, edit, actual RTL workbook and session end. It rejects another tenant's chat/visitor/list capabilities and management key, denies public admin routes/wrong methods, and verifies the server-selected port despite host/port query parameters. Transport substitution is restricted to the two expected loopback ports; provider results are deterministic seams.

`tests/e2e/test_tenant_chat_browser.py` covers both tenants in static and freshly compiled H5: actual authenticated share-button click, issued session, browser JPEG multipart upload, intent resolution through mode control, image/list rendering, real downloaded Arabic workbook and wrong-tenant capability rejection. It asserts there are no unintended root `/cs/` requests. `frontend/session-behavior.test.mjs` covers tenant URL composition and fixed-entry compatibility without dropping prior session-handler assertions.

The NGINX diff is one explanatory comment only. Existing `/merchant/customer/...` requests already reach the hub via `location /`; fixed single-shop `/cs/` remains on19010. No NGINX directive, installer or service rerun was needed to judge this fix.

## M1 — ADDRESSED: selective Starlette multipart localization

`catalog/cs_i18n.py:96–108` registers a handler for the Starlette base HTTPException, mapping only status400 and the two exact parser count messages to canonical `uploadTooManyFiles` / `uploadTooManyFields` resources with stable codes `upload_too_many_files` / `upload_too_many_fields`. The FastAPI subclass retains its existing customer error handler. Unrelated Starlette exceptions fall through to the standard handler with status/detail/headers intact; customer-only boundaries preserve Chinese administration.

Canonical JSON adds both strings for all13 languages; generated customer assets are included. New actual multipart tests cover files/fields on central and shop surfaces in Arabic/French, and the gateway suite covers both limits plus body size while preserving the selected locale. The separate handler test verifies an unrelated418 response/header and a Chinese administrative403 remain unchanged.

## Evidence reviewed, not rerun

- Read `final-fix-report.md`, supplied sanitized fix package and applicable test source. Inspected narrow unchanged list/API interfaces only where necessary to follow new tenant URLs; no second broad branch audit.
- Read retained baseline red log `/tmp/final-fix-confirmed-red.log`: **11 failed, 2 passed, 28 deselected**. Failures are both running direct-endpoint cancellation cases, eight selected Arabic/French multipart cases and tenant issuance404. The already-passing queued cases were retained rather than claimed as initial failures.
- Read `/tmp/final-fix-covering.log`: **87 passed, 2 known upstream warnings in6.95s**.
- Read `/tmp/final-fix-full.log`: **686 passed, 1 skipped, 2 known upstream warnings in55.37s**. The report ties this to final code with no subsequent implementation change. The genuine real-photo dataset skip is retained; fresh compiled H5 was supplied for browser checks.
- Read final frontend logs: executable23/session-tenant cases and language/quote/display semantics passed; H5 and mp-weixin builds both completed. Browser evidence is the reported actual static/compiled test execution and inspected assertions, not a browser execution by this reviewer.
- No suites, builds, new probes, providers, live network, services/installers or git mutations were performed in this re-review. No original `.env`, raw historical credential section or unsanitized package was opened. Only this report was written in the checkout.

## New breakage and out-of-scope observations

- **New fix-introduced defects: none established.** No new out-of-scope defect discovered.
- **Task4B:** required central-only versus central-plus-shop authenticated-history decision remains pending. The correction does not select a default, add cross-DB email ownership or complete overallTask4/project.
- **Live acceptance:** provider/model accuracy and latency, intended original workbooks/photo corpus, native-language fluency, own-mailbox delivery, SSH/publicURL/DNS/TLS/physical devices, native systemd/Docker/bridge/host resource ownership remain unverified. Offline suites/builds do not substitute for them.
- **Prior deferred maintenance/safety:** two upstream deprecations remain nonblocking and unsuppressed; formal mini-program release is separate; historical credential purge/rotation requires separate authorized shared-environment work. The original installer-test incident remains recorded rather than reclassified as a harmless dry run. Other final-review deferred-item triage and ruling costs are unchanged.
