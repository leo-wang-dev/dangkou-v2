# Consolidated final correction report

Base: `472bcc1`. Local implementation commit: `787fd89687a0aa9944d9104ec99e71dd66ea888e` (`fix: retain request workers and route tenant customer chat`). Only the designated isolated worktree was changed. Worktree is clean after commit; this ignored controller report is not staged.

## Corrections

- **I1:** `catalog/request_lifecycle.py` now owns offloaded work in a separate runner. The native endpoint await is shielded from propagating cancellation into that runner. A locked pending/running/abandoned handshake stops never-dispatched work from accessing request resources, including cancellation before the runner coroutine starts. Running work retains its real AnyIO limiter permit until its callback exits. Direct, repeated native and AnyIO cancellation drain the runner before re-raising; existing middleware performs rollback and connection cleanup. Both API and userapp common worker wrappers use the helper, covering non-photo callers too.
- **I2:** authenticated management adds only POST `cs/chat-token`; its share action builds `/merchant/customer/{mid}/cs/chat/{token}`. The existing customer gateway adds a method-specific chat allowlist, preserving its list/photo/edit/export family and exact language assets. It resolves only the hub's running merchant loopback port. Chat token, guest capability and list capability remain validated in the tenant DB. Public administration, cross-tenant management keys/capabilities and caller-selected hosts/ports are rejected or ignored as appropriate. Multipart content type/boundary and selected language are retained; body accumulation is bounded (photo byte limit plus64KiB overhead, otherwise20,000 bytes) and model-facing forwarding uses180 seconds. Static requests/list iframe and uni APIs/photo/downloads retain tenant context. Uni pages accept a validated `mid`; anonymous storage includes the tenant base. Fixed root `/cs/` remains supported.
- **M1:** a Starlette base exception handler translates only the two known multipart parser count limits to canonical fixed resources: `upload_too_many_files` and `upload_too_many_fields`. Arabic/French actual multipart bytes cover both file/field overflow on both customer photo routes and through the gateway; unrelated Starlette status/detail/headers and Chinese admin behavior are preserved. Added both fixed strings for all13 languages and regenerated assets from canonical JSON.

Route/method, uni entry, storage, timeout and cancellation contracts are documented in `docs/superpowers/2026-09-28-guest-session-contract.md`; language additions in `2026-09-28-customer-language-contract.md`. `deploy/nginx-merchant.conf` changed **only one explanatory comment**; no NGINX directive changed, so no new installer/container/nginx execution was needed.

## Meaningful red and green

The final baseline reproduction restored only the four exact relevant source files from local HEAD (`catalog/api.py`, `userapp.py`, `merchant_binding.py`, `cs_i18n.py`) temporarily, ran the new regressions, then restored amended bytes in a `finally`. No historical plan/credential contents were read. Script: `/tmp/check-final-red.py`.

Command inside that script:

```sh
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q tests/test_request_worker_lifetime.py tests/test_customer_languages.py tests/test_tenant_chat.py -k 'direct_endpoint or multipart_counts or dynamic_chat_chain'
```

Result: **11 failed, 2 passed, 28 deselected**, exit1; `/tmp/final-fix-confirmed-red.log`. The11 failures are both real running endpoint cancellation cases, all8 localized multipart cases, and authenticated tenant chat issuance404. The2 already-passing queued cancellation cases are retained as regression coverage.

Cancellation tests capture `asyncio.current_task()` by wrapping the actual registered `APIRoute.dependant.call` for `/cs/chat/{token}/photo` or `/photo`; the observation around `anyio.to_thread.run_sync` captures the separate runner only for limiter/connection state. The test cancels the captured **endpoint**, never substitutes the new runner. File SQLite close tracking, actual borrowed permit count, an actual rejected replacement acquisition with other slots occupied, repeated cancellation, rollback count and bounded joined callback completion are asserted. Separate coverage handles never-started runner and AnyIO cancellation; existing outer-request cancellation tests are unchanged.

Two-tenant routing tests use real hub/file tenant SQLite, registered gateway/API routes and an explicitly bounded ASGI transport which only accepts the two server-owned loopback ports. Vision/text are deterministic stubs and child provider/file notification functions fail closed. Browser tests run an actual loopback Uvicorn gateway and Chromium, authenticate in management, click the share action, select/upload actual JPEGs, resolve photo intent with the mode button, load list images, download/read actual Arabic RTL XLSX, and test wrong-tenant capabilities. Clipboard delivery alone is intercepted as a browser seam. Both static and freshly compiled H5 run both tenants; generated screenshots are outside the checkout.

Final related command:

```sh
DANGKOU_TEST_H5_DIST=/tmp/dangkou-final-fix-build/frontend/dist/build/h5 DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-final-fix-audit /Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q tests/test_request_lifecycle.py tests/test_request_worker_lifetime.py tests/test_customer_languages.py tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_merchant_onboarding.py tests/test_tenant_chat.py tests/e2e/test_tenant_chat_browser.py
```

**87 passed, 2 warnings in6.95s**, `/tmp/final-fix-covering.log`. Standalone actual two-tenant browser run: **2 passed in3.66s**, `/tmp/final-fix-browser.log`.

Initial test-development failures were not erased as product evidence: the first cancellation fixture matched a concrete path instead of the parameterized route; it was corrected and the full meaningful baseline red was rerun above. Browser automation initially sought the share action while the product tab was hidden; it now clicks the real tab first. A frontend test exposed a location mock with no pathname; prefix detection safely handles that shape. No prior assertion was weakened; the existing executable session-handler harness gained the real new session-key dependency stub.

## Fresh frontend verification

Both lock files matched SHA256 `35f25cc4d4a6c31b1e827fb6fad7a2109cc3718cbd945b0a30d0564761ef2dc8` before dependency reuse. Current frontend and static siblings were copied to `/tmp/dangkou-final-fix-build`; `frontend/node_modules` there links only to the existing `/tmp/dangkou-frontend-delivery-build/node_modules`. No worktree node_modules/dist was created. The corrected current `src/api.js` was recopied before the final executions.

From `/tmp/dangkou-final-fix-build/frontend`:

- `npm test`: structural smoke, **23/23 executable session/tenant URL tests**, all13-language/quote/display tests passed; `/tmp/final-fix-frontend-tests.log`.
- `npm run build:h5`: `DONE Build complete`; `/tmp/final-fix-h5-build.log`.
- `npm run build:mp-weixin`: `DONE Build complete`; `/tmp/final-fix-mp-build.log`.

## One final complete offline suite

```sh
DANGKOU_TEST_H5_DIST=/tmp/dangkou-final-fix-build/frontend/dist/build/h5 DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-final-fix-audit /Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/ audit/ -q
```

**686 passed, 1 skipped, 2 warnings in55.37s**, exit0. Log: `/tmp/final-fix-full.log`. This includes existing merchant runtime, guest/session/photo/userapp, transactions, parser/host ownership and both old/new compiled-browser tests. The one genuine missing real-photo dataset skip is retained (`audit/test_round4_pages.py`); compiled-H5 checks ran with the fresh build. The two existing upstream Starlette/httpx and AnyIO deprecation warnings remain visible. No implementation change followed this successful full run; no duplicate full suite was run.

Artifacts: `/tmp/dangkou-final-fix-audit/tenant-{0,1}-{static,compiled}.png` plus the suite's other browser evidence. Static and compiled screenshots were visually inspected for actual Arabic layout/list rendering; this is not native-speaker fluency certification.

## Self-review and limits

Self-review checked the pending/dispatch race, actual permit ownership, repeated cancellation join, fileDB rollback/close order; exact gateway method boundaries, server-resolved port and cross-tenant capabilities; static asset relative resolution and uni tenant paths; canonical resource generation and exception subclass behavior. `git diff --check` passed before commit. Only the22 intended implementation/test/contract files were committed. Existing assertions were preserved; the controller report remains ignored.

**Task4B is still a required unanswered product choice.** No central-only/shared-history decision, cross-database identity or email ownership assumption was added. These corrections do not constitute overall product completion or production acceptance. Live providers and translation fluency, intended original workbooks/photo corpus, original18/30 logical counts and600-second import performance, own-mailbox delivery, SSH/publicURL/DNS/TLS/phone acceptance, actual host installers/services/Docker bridge/systemd and formal mini-program release remain separate pending acceptance. No actual host installer, production service/data, SSH, model/provider/mail or push was performed; original `.env` and raw credential history were not accessed. Shared history purge/credential rotation remains separately authorized work.
