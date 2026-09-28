# Task4 implementation report

Status: independent scope implemented; **NEEDS_CONTEXT** for the required cross-DB account-history choice. No choice was inferred, no client-provided email is trusted for shop ownership. Controller review still required after this handoff.

## Changes and interfaces

- Added `catalog/guest_sessions.py`: random server-issued capabilities, hash-only storage, distinct random owner IDs, configurable24h inactivity (`GUEST_IDLE_SECONDS`, minimum60sec), read-before-model/final-commit validation, inactivity touch, revocation, expiry, post-commit reference-aware file cleanup, and periodic sweep CLI. Neither central nor shop normal guest use has a count/day quota.
- Central routes require issued guest capabilities. Verified OTP merge moves current guest notes AND batches/cards/active batch to the authenticated email owner; photo references survive. Consumed guest token no longer reads notes/images/Excel. Account logout revokes its bearer token; frontends start a fresh guest. Email OTP send/abuse protections remain Task6.
- Shop routes issue session via `POST /cs/chat/{token}/session`; `GET .../session` returns public mode/batch/unassigned-note state. Existing message/lang/photo/list-token calls retain `visitor` field, now an issued capability instead of caller-chosen ID. List capability reads/images/edits/exports check and refresh the same guest lifetime. `POST .../session/end` revokes and purges anonymous state.
- `note_batches` additive migration plus `batch_id` on central and CS notes. Cards are pending; confirmation/manual switch affects subsequent photos/text procurement notes and explicitly selected unassigned notes only. Mixed card/product photos cannot silently associate even if the model emits supplier fields. Same confirmed batch can be reselected. Per-note supplier edits detach into a new manual batch. Configured receiving-shop fallback is snapshotted with explicit preset provenance. Legacy customer-wide latest-card overlay removed.
- Grouped Excel renderer creates safe unique31-character Sheet names per batch, correct independent card/preset headers, literal cell values and real images. Same display name alone never merges batches. Existing single-group renderer callers remain supported. Updated older tests retain all product/price/photo assertions across all worksheets and card-header offsets.
- First shop photo is retained without model calls until `POST .../mode` selects `search` or `notes`. Selection processes the same queued image, rejects concurrent duplicate effects, resets on a new session. Search creates no notes; notes makes no catalog/quote claims. Central remains notes mode. Internal catalog candidates may establish applicable approved photo policy; only a real approved-rule match creates the durable handoff outbox record in the same business transaction.
- Extraction/review prompt now specifies independent product box vs price box. Finite/increasing/bounded geometry only. Unique-name carryover supports review reordering; duplicate names never guess. Explicit null/invalid correction prevents carryover. Crop fallback is visible in note fields. Real red/blue pixel assertions verify actual different crops.
- Both static H5 and uni-app use browser-session/in-memory anonymous storage; previous localStorage IDs are ignored. Both have end-session, visible mode/change controls, pending card confirmation, manual switch and explicit unassigned-note selection. Fixed invisible static toast intercepting clicks, discovered by mobile-width real browser flow. Task5 owns all13-language translations/final phrasing.

Detailed durable contract and sweep commands: `docs/superpowers/2026-09-28-guest-session-contract.md`. CLI: `python -m catalog.guest_sessions --kind central|shop --db <one DB> --photo-dir <matching root>`. Run once per database (e.g.15-minute timer), commit DB purge before unlinking, never delete surviving account/shared references; CLI exceptions propagate failure. Task6 must wire deployment scheduling; this task did not touch a live scheduler. Browser closure is not claimed as immediate server deletion.

## Red → green evidence

All Python invocations use `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py` in the isolated worktree. Models/network are replaced, real SQLite/HTTP/Excel/pixels are used.

1. `tests/test_photo_mode.py -q`: initial2 failures: reordered review returned full200x100 image rather than correct100x100 crop; reversed product coordinates accepted. After fix2 passed. Additional explicit-null test failed (initial box restored), then passed after explicit-clear marker. Duplicate-name test retains no ambiguous crop.
2. `tests/test_guest_sessions.py -q`: initial4 failures: fabricated guest read returned200; end endpoint404; no session table; merged guest remained readable. After issued capabilities/end/expiry/merge4 passed. Later list-capability inactivity test failed (expiry stayed60sec), then passed after same-session hash touch. Periodic sweep test initially AttributeError, then passed with real row/orphan/shared-user-file assertions.
3. `tests/test_note_batches.py -q`: initial2 failures missing pending batch protocol. After implementation one SQL ambiguous-owner column failure was fixed;2 passed. Later mixed-card supplier test failed (`A` rather than pending unknown), then passed after removing unconfirmed extractor supplier association. Explicit per-note edit detach verified. Text-note batch test initially None, then passed. Merge continuation test initially created batch3 instead of preserving1, then passed after preserving active guest batch during merge.
4. Shop mode tests initially failed because unissued visitor accepted and session route absent. Green includes first upload zero model calls, retained selection, search no notes, notes mode, export before login, end/list revocation, approved redline outbox persistence.
5. Concurrency tests pass for revoke during blocked vision (410, no resurrected rows/unowned files) and concurrent queued-image mode submissions (one200, one409, exactly one note/file). Task1 blocked-model independent-writer/read, text cached replay, bounded conflict and cancellation rollback assertions retained with actual issued capabilities.
6. Related backend slice `tests/test_userapp.py tests/test_h5_transactions.py tests/test_csbot.py tests/test_photo_inquiry.py tests/test_guest_sessions.py tests/test_photo_mode.py tests/test_note_batches.py -q --tb=short`:59 passed after first fixture updates. Later core guest/batch/photo slice18 passed; transaction+concurrency expanded slice34 passed. `tests/test_csbot.py tests/test_purchase_conversation.py -q --tb=short`:57 passed after final grouped export reader updates.
7. Real browser new case `tests/e2e/test_guest_photo_session.py`: initial failure exposed invisible toast pointer interception; fixed CSS. Next fixture failure due to missing static mount was corrected to match production's real StaticFiles mount. Green1 passed: actual mobile browser issued session→upload→mode→unassigned checkbox→card confirmation→list iframe verifies supplier→manual switch→end/new session. Screenshot `/tmp/dangkou-task4-audit/guest-photo-session.png`; generated artifacts are not tracked.
8. Static chat/tool scripts checked with `node --check`. Frontend initial node smoke passed, but initial direct builds failed (`uni: command not found`). Resolved without install/network: fresh current frontend sources copied to `/tmp/dangkou-task4-frontend-build`, node_modules symlinked to existing `/tmp/dangkou-frontend-delivery-build/node_modules`, whose package-lock matched current SHA25635f25cc4d4a6c31b1e827fb6fad7a2109cc3718cbd945b0a30d0564761ef2dc8. `npm test`, `npm run build:h5`, `npm run build:mp-weixin` all passed. Repeated after final frontend styles using same fresh-source copy; logs `/tmp/dangkou-task4-frontend-test.log`, `/tmp/dangkou-task4-h5-build.log`, `/tmp/dangkou-task4-mp-build.log`.

## Full-suite failure accounting (preserved logs)

First full run command: `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task4-audit <offline runner> -q --tb=short > /tmp/dangkou-task4-full.log 2>&1`.
Result:568 passed,12 failed,1 skipped,7 warnings,72.67sec. Every failure:
- `tests/e2e/test_pages.py::test_chat_page_list_drawer`: old localStorage visitor fixture replaced by a real issued capability in sessionStorage; same drawer/export behavior retained.
- `tests/e2e/test_shop_linkage_page.py::test_merchant_approval_to_customer_shop_and_excel`: receiving-shop preset snapshotted; manual edit makes separate batch/header. Same supplier/contact clearing, actual image and workbook checks retained with card-header row count.
- `tests/test_http_concurrency.py::test_h5_health_responds_while_vision_is_blocked`: issued visitors and explicit notes mode added; same independent health/write assertions retained.
- `tests/test_http_concurrency.py::test_userapp_guest_responds_while_vision_is_blocked`: obtains central guest capability from route before blocked photo; concurrent guest request assertion retained.
- `tests/test_http_concurrency.py::test_h5_text_model_wait_allows_other_visitor_write_and_read`: issued visitors; same independent edit/read and cached-call uniqueness assertions retained.
- `tests/test_http_concurrency.py::test_h5_text_replans_when_same_visitor_language_changes`: issued same-session capability and DB owner lookup; language-replan assertion retained.
- `tests/test_http_concurrency.py::test_h5_remote_catalog_wait_allows_other_write_and_reuses_result`: issued visitors; same concurrent writer and single remote request assertions retained.
- `tests/test_http_concurrency.py::test_h5_photo_cancel_waits_for_worker_and_rolls_back`: issued visitor, notes mode, wrapper accepts prepare kwargs; cancellation/worker-drain/no-note assertions retained.
- `tests/test_http_concurrency.py::test_userapp_photo_cancel_waits_for_worker_and_rolls_back`: real central issued guest; same cancellation/drain/rollback assertions retained.
- `tests/test_manual_category.py::test_h5_chat_flow`: issued visitor+notes mode; forged other visitor now explicitly401 (stronger than old empty-list check); language/photo/export checks retained.
- `tests/test_shop_linkage.py::test_merchant_approval_bot_photo_web_excel_same_identity`: implemented immutable receiving-shop batch preset, updated expected card headers and historic profile preservation; tenant/service-auth assertions retained.
- `tests/test_vision_recovery.py::test_empty_vision_result_retries_once_before_creating_notes`: obsolete exact-field-count changed to explicit visible crop fallback assertion; retry count and conservative price assertions retained.

Second full log `/tmp/dangkou-task4-final-full.log`:581 passed,5 failed,1 skipped,7 warnings,48.47sec. These5 readers assumed active Sheet or row1 without card header; all data assertions retained across grouped sheets:
- `tests/test_csbot.py::test_assign_different_suppliers_and_export_without_cross_customer_changes`
- `tests/test_purchase_conversation.py::test_text_creates_multiple_notes_and_exports`
- `tests/test_purchase_conversation.py::test_selected_photo_is_enriched_without_duplicate_and_excel_has_image`
- `tests/test_purchase_conversation.py::test_export_contains_catalog_enriched_and_unmatched_purchase_rows`
- `tests/test_purchase_conversation.py::test_export_refreshes_catalog_fields_and_enriches_a_previously_unmatched_note`

Final full command repeats the same offline full suite after those corrections; log `/tmp/dangkou-task4-verified-full.log`. Final result and commit recorded below after completion.

## Self-review and limitations

- Rechecked model preparation before writer lock, final guest validation, queued-image compare-and-consume, cleanup after commit/rollback, and shared account photo preservation. Internal catalog model IDs are not emitted by the session-state response.
- Rechecked cross-owner batch confirmation isolation; card snapshots and old note associations never inherit a newly recognized latest card. Manual association is explicit and restricted to unassigned owned notes.
- No live model quality, external email delivery, deployed timer, proxy, device or production data validation is claimed. Full suite retains its existing skip/warnings; later stages own those environment boundaries.
- No network install, `.env` read/copy, production write, push or deployment. No agents/reviewers spawned.
- Required cross-DB authenticated history decision remains the only product dependency for Task4: central-only durable history vs central+shop-CS history. Independent code does not decide it. A both-history answer requires trusted scoped account binding plus merchant DB merge/discovery/export; never client-email trust.


## Final verification addendum

- Completed full suite before last migration edge patch: **586 passed,1 skipped,7 warnings,53.16sec**, exit0, `/tmp/dangkou-task4-verified-full.log`.
- Final self-review added `test_upgrade_revokes_legacy_unscoped_list_capability`: red200 vs expected410, then migration expires old unscoped list links while preserving legacy notes/customer rows. Rollout impact is documented. Issued-session-backed links remain governed by active session state.
- Last migration+guest+transaction+concurrency+shop-linkage focused run and already-started full run results recorded below once collected. The extra full run started before the controller's guidance that focused verification alone was sufficient; no further automatic reruns will be started.

- Final post-migration focused command: `<offline runner> tests/test_guest_sessions.py tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_shop_linkage.py -q --tb=short` → **31 passed,2 warnings,1.80sec**, exit0.
- Final already-started post-migration full suite: **587 passed,1 skipped,7 warnings,44.98sec**, exit0; `/tmp/dangkou-task4-migration-full.log`.
- Final frontend smoke plus both H5/Weixin builds: exit0; compiler reports `DONE Build complete`; static script syntax checks and `git diff --check` clean.
- Commit: **7142217** — `feat: secure trial guest sessions and supplier photo batches` (32 deliberate app/test/documentation files; no reports/artifacts/secrets staged).
- Handoff status: **NEEDS_CONTEXT** only for the explicit central-only versus central+shop-CS authenticated-history choice; independently reviewable implementation committed. No push/deploy.

# Task4A review fix round1/5 (base7142217)

The five Important findings in `task-4-review.md` and both Minor test-meaning findings are addressed in this single scoped round. Task4B history choice remains unanswered; no central-only default or client-email binding was introduced.

## Corrections

1. `merchant_policy.photo_rule` uses the established candidate `product_id`. Six new real-HTTP cases seed visible catalog products and run production local candidate lookup (no candidate stub): search/notes, each with no rule, approved shop rule or approved product rule. They assert successful response, correct note count and durable outbox behavior.
2. Any owner-scoped pending card blocks implicit old-supplier assignment for all later plain photos and text notes. Earlier notes remain unchanged. Explicit confirmation or reselection resolves pending switches; a new `action=decline` path marks one pending card declined, reports `declined:true`, and resumes the old active supplier only when no unresolved switch remains. Both clients expose decline and retain explicit association. Central photo, shop photo and shop text HTTP regressions cover the barrier/resolution behavior.
3. A retained image blocks any fresh upload even after mode persistence or processing failure. Processing validates and clears only its actual pending path. Added owner-scoped `/pending-photo/discard`, which compares the actual path and preserves transaction/file cleanup semantics. Static and uni clients restore `intent_required`, visibly show pending work, offer retry/current mode and explicit discard, and refresh state after mode failures. The real browser now deliberately fails its first model request, reloads, sees pending work, observes fresh-upload409, retries, associates the recovered note, and finally explicitly discards another retained image.
4. Export grouping now follows the binding one-Sheet-per-proven-supplier requirement, correcting the previous contract. Immutable batch provenance stays in SQLite. Exact normalized name+address+contact information establishes the supplier key; all supplied card values must agree, including an individual contact if present. A person's name is optional. NFKC/whitespace normalization is deterministic; uncertain tokens, partial cards and conflicting same-name contacts remain separate. Actual workbook tests cover repeated recognized identical cards, same-name different contacts, uncertain cards and absence of a named individual. Durable contract updated accordingly.
5. All four actual reset handlers (static tool/chat and uni tool/chat) treat401/410 as already-ended anonymous sessions, replace stored credentials and initialize anew.500, transport status0 and thrown network failures preserve the existing session and report failure. An executable Node VM harness runs the shipped handler functions with controlled DOM/platform/transport seams; assertions inspect calls, resulting identity/storage and retained-photo visibility/actions. This is behavior execution, not source-substring assertions. A pytest wrapper includes these tests in future suites.
6. `tests/test_userapp.py` captures the server-side issued `owner_id`, verifies an actual guest row exists before merge, then verifies that owner's rows disappear. `tests/test_csbot.py` restores `_make_link` + real HTTP export helper usage, returning the workbook for grouped-sheet assertions rather than bypassing the route.

## Red/green commands and outputs

All Python commands below use the required offline runner, real temporary file SQLite, HTTP routes and Excel parsing. All models/network are replaced. No external calls, package install, credentials, production data, subagents or full-suite rerun occurred in this round.

- `<offline runner> tests/test_photo_mode.py tests/test_note_batches.py -q --tb=short` before fixes: **10 failed,12 passed** in0.90sec. Preserved `/tmp/dangkou-task4-fix1-red.log`. Failures: all six unique-local-candidate policy cases returned500; failed retained-photo/fresh upload accepted rather than409; central pending-card successor assignedA; shop photo/text pending barrier assigned confirmed batches; repeated identical full-card export had3 Sheets instead of2.
- `node --test frontend/session-behavior.test.mjs` before reset fixes: **8 failed,8 passed**; `/tmp/dangkou-task4-fix1-client-red.log`. All four401 resets failed to issue new credentials; all four thrown-network cases escaped handlers.410 and500 baseline behavior remained covered.
- Additional supplier-identity edge tests were red individually: `test_ambiguous_or_partial_identical_cards_do_not_merge` merged uncertain contact text, then green after uncertainty exclusion; `test_supplier_identity_does_not_require_a_named_individual_contact` split an otherwise fully identified stall, then green after making named-person optional without relaxing address/contact equality.
- First amended backend slice (`test_photo_mode`, `test_note_batches`, `test_userapp`, `test_csbot`): **56 passed,2 warnings,1.29sec**, `/tmp/dangkou-task4-fix1-backend.log`.
- Related regression command: `<offline runner> tests/test_photo_mode.py tests/test_note_batches.py tests/test_guest_sessions.py tests/test_userapp.py tests/test_csbot.py tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_purchase_conversation.py tests/test_shop_linkage.py tests/test_frontend_session_handlers.py -q --tb=short` → **124 passed,2 warnings,3.19sec**, `/tmp/dangkou-task4-fix1-related.log`. This includes request transaction/cancellation, blocked-model independent writes, export/photo/supplier and restored HTTP-helper coverage.
- Final amended focused slice (`test_note_batches`, `test_photo_mode`, `test_guest_sessions`, `test_frontend_session_handlers`) → **32 passed**, log `/tmp/dangkou-task4-fix1-final-focused.log`. Includes the final text HTTP assertion, optional-person supplier identity and truthful decline protocol.
- Final Node behavior matrix: **22 passed,0 failed**, `/tmp/dangkou-task4-fix1-client-green.log`: four handlers ×401/410 successful recovery, four handlers ×500/0/network preserved failure, and static/uni retained-photo failure/retry/discard behavior.
- `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task4-audit <offline runner> tests/e2e/test_guest_photo_session.py -q --tb=short` → **1 passed,2 warnings,1.77sec**, `/tmp/dangkou-task4-fix1-browser.log`; screenshot remains outside git in `/tmp/dangkou-task4-audit/guest-photo-session.png`.
- Fresh frontend copy `/tmp/dangkou-task4-fix1-frontend-build`, audited existing dependency symlink only: `npm test`, `npm run build:h5`, `npm run build:mp-weixin` exit0, both compilers `DONE Build complete`. Logs `/tmp/dangkou-task4-fix1-smoke.log`, `...-h5-build.log`, `...-mp-build.log`. Both static scripts pass `node --check`; `git diff --check` clean. Existing module/dependency warnings remain unchanged.

## Self-review and remaining limits

Checked pending-card ownership/resolution without rewriting old confirmed notes; literal export cells and distinct ambiguous groups; retained-path compare/consume/discard under final validation; pending state after model error/reload; and new reset handling that does not turn real failures into silent session replacement. No new broad risk justified a full-suite rerun. The earlier587-pass whole-suite evidence remains specifically pre-fix-round evidence, not a claim for this revised commit.

Task4A fix round is ready for controller review. Task4B requires the user's authenticated-history scope choice. Task5 translation and Task6 deployment/OTP/sweep scheduling remain their existing responsibilities. Commit recorded below after staging only deliberate application, test and durable contract files.

- Fix-round commit: **0c749ba** — `fix: resolve guest photo queue and supplier session review findings` (18 deliberate app/test/durable-contract files).
- Final focused result confirmed: **32 passed,2 warnings,0.92sec**, exit0; final `git diff --check` clean.
- Handoff: **Task4A DONE_WITH_CONCERNS** for controller review; **Task4B NEEDS_CONTEXT** for the pending history choice. No push/deploy.
