# Buyer Login and Mobile Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a procurement buyer log in by email from a shop chat, merge the active guest history, recover that shop's notes and conversation on another device, and keep the chat input visible above mobile browser chrome.

**Architecture:** The central user-app owns verified account identity and bearer tokens. Each shop retains its own buyer records and binds a centrally verified opaque account ID in a local transaction; the public merchant gateway forwards only the required scoped customer routes and Authorization header. Static and uni H5 clients share central login, recover the shop session/history, and use a visual-viewport-aware flex layout.

**Tech Stack:** FastAPI, SQLite, vanilla H5, Vue 3/uni-app, pytest, Node tests, browser E2E, nginx/systemd trial services.

**Spec:** `docs/superpowers/specs/2026-09-29-buyer-login-and-mobile-chat-design.md`

## Global Constraints

- Guest shopping, photo notes and Excel export remain available without login or a normal-use quota; guest data expires after 24 hours of inactivity unless merged.
- Only central verified bearer identity may establish a shop account binding; no client-submitted email/account ID is trusted.
- Existing email users/tokens and existing shop guests survive migrations; account data is not purged with guest sessions.
- Both static and uni H5 entrypoints, 13 languages, and existing mini-program build compatibility are required.
- Public trial email codes may be delivered by configured mail sender only; never enable plaintext code logging on a public service.
- Trial rollout uses isolated data and proves the public `/tool/` and merchant customer URL target matching trial services; do not clear business data.

## Review Focus

1. Central identity lookup times out during claim or later account write: report retryable 503 and retain the active guest/authorized account state (Task 2 tests).
2. A buyer logs into an account that already has shop notes and a different active card: merge new guest notes without reassigning existing batch/card/photo relationships (Task 2 tests).
3. The same guest capability or OTP result is replayed: no duplicate notes, links or customer rows; expired guest cannot be claimed (Task 2 tests).
4. A valid buyer visits a second shop or sends a client-written email/account ID: the account may use the second shop but cannot see the first shop's rows there, and client-written identity has no effect (Tasks 2 and 3 tests).
5. Mobile browser expands a bottom address bar, then opens the keyboard and a list drawer: input/send remain visible and tappable without a blank bottom gap with a top address bar (Task 4 browser tests).

---

## File map

- `catalog/userapp.py`: stable opaque central `account_id`, authenticated `/me` response and backward-compatible migration.
- `catalog/buyer_identity.py` (new): bearer extraction and central `/me` verification over a configured private base URL, with bounded timeout and explicit 401/503 distinction.
- `catalog/shop_account.py` (new): shop account binding, idempotent guest claim, merge and authenticated owner lookup; all shop DB mutations in one transaction.
- `catalog/api.py`: customer routes use guest or verified account identity; claim, session/history, list and export paths integrate the owner resolver.
- `catalog/merchant_binding.py`: narrow claim/history allowlist and Authorization forwarding.
- `static/cs/chat.html`, `frontend/src/pages/chat/chat.vue`, `frontend/src/api.js`: login and recovery UI/requests plus viewport layout.
- `frontend/src/customer-languages.json`, `frontend/src/customer-catalog.js`, `static/customer-catalog.js`: all customer-facing login and recovery strings; keep generated/catalog copies synchronized.
- `deploy/nginx-merchant.conf`, `.env.example`, operations docs: private identity-service wiring and trial public-route verification.
- Focused test files below: real SQLite and HTTP security/merge tests, executable client tests, mobile browser tests and live trial evidence.

### Task 1: Central opaque account identity

**Files:** Modify `catalog/userapp.py`; test `tests/test_userapp.py`, `tests/test_userapp_auth_limits.py`.

**Interfaces:** Produce `GET /me` for a valid bearer as `{kind:"user", email:string, account_id:string, lang:string}`. `account_id` is stable, random and unique; legacy users receive it in an additive migration. Guest `/me` never receives an account ID.

- [ ] Add `test_existing_email_and_token_gain_stable_opaque_account_id`: a file DB with a legacy `users` row and valid token opens with the new app, returns the same non-email ID across restarts and another login, and preserves the old token.
- [ ] Run `.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/test_userapp.py tests/test_userapp_auth_limits.py -q --tb=short`; the new assertion must fail before code changes.
- [ ] Add the additive `users.account_id` migration and authenticated `/me` field in `catalog/userapp.py`; do not expose the ID through unauthenticated responses.
- [ ] Run the same tests until green; commit only Task 1 files.

### Task 2: Shop account binding, claim and owner resolution

**Files:** Create `catalog/buyer_identity.py`, `catalog/shop_account.py`; modify `catalog/db.py`, `catalog/api.py`, `catalog/guest_sessions.py`, `catalog/note_batches.py` only where owner migration requires it; test new `tests/test_shop_account.py` and existing `tests/test_guest_sessions.py`, `tests/test_purchase_conversation.py`.

**Interfaces:** `buyer_identity.verify(request) -> account_id` accepts a bearer and calls `CUSTOMER_IDENTITY_BASE_URL` plus `/me` over private HTTP with a bounded timeout before any shop writer transaction; invalid/revoked bearer is 401, unavailable service is 503. `shop_account.claim(conn, account_id, visitor) -> customer_id` is idempotent for the same account, consumes an active shop guest and preserves its notes/cards/photos/logs; another account cannot claim that guest. `shop_account.resolve(conn, account_id=None, visitor='') -> customer_id` is DB-only and chooses verified account when supplied, otherwise validates guest. The existing visitor JSON field remains for guest clients. Add `POST /cs/chat/{token}/session/claim` and paginated `GET /cs/chat/{token}/history`.

- [ ] Add red file-DB/HTTP tests for new account claim, existing account plus guest merge, same claim replay, invalid/expired guest, central timeout, token revocation, two users/two shops isolation, all note/batch/card/photo/log relationships and old guest link invalidation; assert account rows survive guest sweeps.
- [ ] Run `.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/test_shop_account.py tests/test_guest_sessions.py -q --tb=short`; new cases must fail.
- [ ] Implement private identity verification and additive shop account binding migration; make claim mutation atomic, preserve existing account card selection, revoke merged guest without purging merged rows, and issue new account-owned list capabilities.
- [ ] Extend shop customer routes to use the owner resolver, including text/photo, mode/lang/batches, list token, note edit/photo/export and bounded history retrieval. Explicit account conversation reset changes mode/context only; account logout uses central token revocation.
- [ ] Run Task 2 tests plus `tests/test_photo_mode.py tests/test_note_batches.py tests/test_shop_linkage.py tests/test_purchase_conversation.py`; commit only Task 2 files after green.

### Task 3: Narrow public gateway and account security

**Files:** Modify `catalog/merchant_binding.py`; test `tests/test_tenant_chat.py`, `tests/test_merchant_onboarding.py`, new `tests/test_shop_account_gateway.py`.

**Interfaces:** Publicly expose exactly the new chat `session/claim` and `history` paths for their intended methods, forward `Authorization` only on customer routes, retain the existing multipart boundary and 20,000-byte non-photo body limit. Management and arbitrary upstream paths stay inaccessible.

- [ ] Add red gateway tests: Authorization reaches only the selected tenant; another tenant's chat token/account binding fails; caller `email`/`account_id` headers have no identity effect; management/private paths remain 404; malformed/oversize bodies remain bounded.
- [ ] Run `.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/test_shop_account_gateway.py tests/test_tenant_chat.py -q --tb=short`; new cases must fail.
- [ ] Extend the fixed route allowlist and safe header forwarding, without accepting client-selected upstream hosts or ports.
- [ ] Run Task 3 tests and `tests/test_merchant_onboarding.py`; commit only Task 3 files after green.

### Task 4: Buyer UI, languages and mobile input layout

**Files:** Modify `static/cs/chat.html`, `static/tool/index.html` if affected by the same viewport bug, `frontend/src/customer-languages.json`, `frontend/src/customer-catalog.js`, `static/customer-catalog.js`, `frontend/src/pages/chat/chat.vue`, `frontend/src/pages/tool/tool.vue` if affected, `frontend/src/api.js`; test `frontend/session-behavior.test.mjs`, `frontend/languages.test.mjs`, `tests/test_frontend_session_handlers.py`, `tests/e2e/test_tenant_chat_browser.py`.

**Interfaces:** Both H5 chat pages show guest/login/account/logout state, send/verify central OTP (including an active central `ut_guest` in the central merge request), claim current shop guest, retain credentials on claim failure, restore current shop notes plus paginated recent conversation, and use bearer for account requests. `100dvh`/safe-area/`visualViewport` keeps the composer above moving mobile chrome and keyboard. Existing guest controls and mini-program compilation remain functional.

- [ ] Add red executable client tests for guest login merge, login-without-guest restore, reload/re-auth, failed claim retry without visitor loss, invalid bearer visibility, logout, 13 translated strings, and account reset preserving account history.
- [ ] Run `cd frontend && npm test` and `.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/test_frontend_session_handlers.py -q --tb=short`; new cases must fail.
- [ ] Implement shared login flow/API headers in static and uni H5, update translation sources/assets and show loaded history without duplicate welcome bubbles.
- [ ] Add mobile browser red visual/interaction cases for bottom and top address bars, keyboard, drawer, portrait/landscape; apply dynamic visible-height layout and safe-area padding until input/send remain interactable.
- [ ] Run frontend tests, browser E2E and `cd frontend && npm run build:h5 && npm run build:mp-weixin`; commit only Task 4 files after green.

### Task 5: Deployment wiring and real TC11–TC16 acceptance

**Files:** Modify `.env.example`, `deploy/nginx-merchant.conf`, `docs/superpowers/2026-09-28-trial-operations.md`; create `docs/superpowers/evidence/2026-09-29-buyer-login-trial.md`; test `tests/test_deploy_preflight.py` and appropriate live HTTP/browser scripts.

**Interfaces:** The private central auth base points to the matching isolated user-app instance. Public `/tool/` and merchant customer routes reach that same trial identity service and tenant set. The evidence file records each testcase's request, observed response/artifact, pass/fail and limitations, without secrets, raw bearer or OTP.

- [ ] Add red config/preflight tests for absent private auth URL, wrong trial `/tool/` upstream and no mail sender; run `tests/test_deploy_preflight.py` and confirm they fail for the new assertions.
- [ ] Add configuration guidance and preflight checks; run focused config tests, related backend tests, then the full offline suite once.
- [ ] Deploy code/config to the isolated trial services, restart the affected services and verify health plus actual public routing before any customer data test. Preserve a rollback copy; do not clear existing business databases.
- [ ] Execute TC11–TC15 with real HTTP/browser requests and real trial SQLite, image/model calls and Excel inspection; test TC16's automatable handoff/language pieces and mark human TG negotiation separately. Record mobile input screenshots at top/bottom browser bars and keyboard open.
- [ ] Write evidence with timestamps, request paths/statuses, generated artifacts and explicit failures. Re-run affected checks only if a fix follows; commit documentation and report the actual deployed revision.
