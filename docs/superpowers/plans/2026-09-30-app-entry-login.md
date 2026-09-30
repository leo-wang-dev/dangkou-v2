# APP Entry Login Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** Add APP login to the existing merchant webpage without changing business or queues.
**Architecture:** Dedicated backend credential mints a 60-second one-use ticket. Exchange creates an 8-hour HttpOnly cookie session. Explicit management route allowlist and CSRF checks add authentication alongside existing service-token access.
**Tech Stack:** FastAPI, SQLite, existing vanilla JS management page, pytest and Node tests.
**Spec:** docs/superpowers/specs/2026-09-30-app-entry-login.md

## Global Constraints

- Feature disabled by default. Only dangkou-v2 changes; no APP projects, deployment or provider calls.
- Preserve legacy credentials and business behavior. No queue changes.
- Fixed HTTPS origin, per-shop secret, hash-only persistent credentials, revoke on configuration rotation.
- User explicitly approved implementation; execute inline without another approval cycle.

## Review Focus

- Cookie authentication must not grant access to operations/customer service interfaces.
- Revoked/expired APP sessions must not fall back to stale service or ticket tokens.
- Images/uploads/downloads and ticket decisions must all participate in APP authentication.
- Concurrent ticket exchange must create one session; malformed inputs must not crash or leak credentials.
- Old webpage, WeChat links and request paths must remain functional when the feature is disabled.

### Task 1: Session lifecycle

Files: catalog/app_entry.py, catalog/db.py, catalog/api.py, tests/test_app_entry.py.
Interface: register(app, request_conn); authenticate state consumed by _auth.
- [x] Write lifecycle, expiry, rotation, revoke, configuration and concurrent replay tests; run and observe missing endpoint failures.
- [x] Implement validated settings, hash-only ticket/session tables, mint/exchange/status/logout/revoke endpoints.
- [x] Run tests/test_app_entry.py; expect pass.

### Task 2: Existing page and management authentication

Files: catalog/api.py, static/index.html, static/app-auth.js, static/app-entry/, static/merchant/wechat-bind.html, tests/test_app_entry.py, tests/app-auth.test.mjs.
Interface: APP header and cookie take priority; CSRF from /app-entry/session; existing legacy token otherwise.
- [x] Add allowlist/CSRF/stale credential and real product-write/review/resource tests, plus browser helper tests. Observe failures.
- [x] Implement explicit route gate and minimal existing-page authentication helper; retain legacy business handlers.
- [x] Run targeted pytest and node --test tests/app-auth.test.mjs; expect pass.

### Task 3: Compatibility and delivery

Files: .env.example, docs/APP登录接入说明.md, plan evidence.
- [x] Document exact configuration, backend calls, root-path deployment limitation and rollback.
- [x] Run complete pytest and frontend npm test, review changes independently, fix material issues with regression tests.
- [x] Commit and integrate verified change into the original clean branch; update desktop handoff.

## Execution ledger

- Initial repository clean at f12f643. Isolated worktree .worktrees/app-entry-login.
- Task 1 and 2 share request-scoped database accessor and request.state authentication; install APP middleware inside the existing database lifecycle.
- Ruling: fail closed for non-root deployment in first release; existing proxy routes require separate integration verification, not guessed cookie support.

- RED: tests/test_app_entry.py initially 15 missing-feature failures; after implementation all passed. Browser helper initially 5 failures, then 5 passed.
- Review fixes: legacy proxy must publicly serve the nonsecret auth helper; HTTPS origin is canonicalized; APP entry secret cannot equal the service secret. Added regression tests before fixes.
- Compatibility failures in full regression were legacy proxy asset loading and isolated browser/VM fixtures missing the new helper. Fixed asset serving and fixture dependencies; no assertions weakened.
- Final verification: pytest 760 passed, 4 skipped (63.28 seconds); two existing Starlette/httpx deprecation warnings. npm test passed including 36 structured tests, existing smoke/language checks, and 5 new auth adapter tests.
- Scope confirmed: no changes to ingest.py, tickets.py, notify.py or indexing; no external APP project changes or deployment.
- Root-path-only APP entry is explicit. Old proxy management pages remain supported using their original credentials; mobile WebView/production proxy integration is still external acceptance.
