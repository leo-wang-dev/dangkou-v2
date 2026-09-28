# Task5 implementation report

Status: implemented and committed for independent review. Commits `9423870`, follow-up `af18f97`, and review fix `071bc10` on `codex/h5-trial-20260928`; parent `b114f34`. No push/deploy or production data/credentials access. Controller owns review. Task4B central-plus-shop account-history scope remains explicitly pending.

## Interfaces and implementation

- Canonical registry `zh/en/fr/es/pt/ru/ar/de/ja/ko/vi/th/id`,182 keys ×13 languages, reusing the prepared138-key resource and extending actual backend/Task4/quote gaps. Legacy `中文`/`English` inputs persist as `zh`/`en`. Canonical JSON is `frontend/src/customer-languages.json`; backend runtime must include that file. Task6 packaging must retain it. Generator `scripts/build_customer_languages.py` creates the checked-in classic/module assets from canonical resources and aliases; Node verifies parity and placeholders.
- Customer request language is `X-Customer-Language`, with `?lang=` for navigated export links. Shop `/lang` persists `cs_customer.lang`; central `/lang` persists guest/user preference. Current central guest preference follows verified account merge; `/me` restores account preference from another device. UI `dk_lang` is persistent and independent of transient guest credentials. No cross-shop/account-history decision was inferred.
- Both static and uni clients expose language controls, mode selection/change, retained photo retry/discard, manual/confirmed supplier switch, decline and selected unassigned-note association. Fixed customer UI/errors/receipts/standard note labels localize without provider. Backend storage field names remain unchanged. Known missing-data placeholders localize for display; static edit blur does not overwrite an unchanged translated placeholder.
- Machine chat actions (`contact_owner`, `export`, `confirm`) do not parse translated button text. Existing mode and batch action codes remain unchanged. Arabic UI/document direction and worksheets use RTL; raw model/data cells retain LTR reading order.
- Dynamic translation is within Task1 snapshot/model-cache planning and replay. All provider batches precede cache writes. Explicit request locale is applied only inside the staged turn; absent explicit locale, replay sees the latest persisted preference. Protected occurrence tokens and cache validation retain models, suppliers, phones, numbers/amounts, URLs and email. Missing/malformed/unchanged/corrupt translation visibly carries the localized original-text notice; English prose is no longer mistaken for a raw identifier. Existing file-SQLite independent-writer and replay tests pass.
- Notes export localizes standard schema/card labels, empty sheet names and placeholders. The follow-up below adds protected translation of descriptive cells and custom headings for display while preserving stored source evidence, identity values and numeric cells. Quote API/plugin has explicit `target_language`, default `zh`;14-column numeric layout, Decimal/carton/deposit calculations and supplier/product identity retained. Quote labels/totals/carton explanation follow target locale. Description/color translation runs before notification writes, and emits the visible fallback marker if unavailable or if invoked with an already-writing connection.
- Admin copy remains Chinese. There was no pre-existing admin quote UI. Controller explicitly approved the minimal per-product quantity/language action so this planned control was usable. It uses exact category/product identity and existing defaults, handles input/server/download errors, and downloads through the new authenticated bounded `/quotes/quote-<8 hex>.xlsx` route. API adds `target_language` and `download_url` while retaining previous fields and Chinese notifications/plugin administrative explanation.
- Fixed actual uni `/tool/` fetch/upload/export CS requests to `/cs/...`; tool requests retain their relative/configured prefix. Compiled browser found an additional uni `<image>` root-path rewrite, fixed by fully resolving browser photo URLs before handing them to uni. Static language scripts use deployment-relative paths.

## Red/green evidence and encountered failures

All named files below are preserved in this ignored SDD directory. They are not committed. A first test-fixture import collection error was corrected (`tests.test_h5_transactions`); it was not counted as semantic red. Temporary authoring-shell UTF-8 parsing errors were corrected before generated source execution.

- `task-5-red-python.txt`:5 expected failures: shop language clamping, fixed intent/error locale, English prose/protected translation, note XLSX headings/RTL and central language preference.
- `task-5-red-node.txt`: actual `csApi.newSession` URL was `https://example.test/tool/cs/chat/shop/session` rather than `/cs/chat/shop/session`.
- `task-5-red-quote.txt`: target-language renderer interface missing.
- `task-5-red-staging.txt`: first-request selected language never reached dynamic translation. Subsequent semantic test pauses the provider while another real SQLite writer completes, then confirms one cached provider call and correct French output.
- `task-5-red-quote-download.txt`: first quote download test initially exposed fixture shop ownership mismatch instead of missing download route; that fixture issue is separately retained and later corrected to match the app shop. Real authenticated quote+download behavior is covered in final tests.
- `task-5-fixed-chat-red.txt`, `task-5-cache-red.txt`, `task-5-placeholder-red.txt`: fixed response catalog gap, unsafe cached literal/extra numeric literal and Chinese reserved placeholders. Final corresponding tests pass.
- `task-5-full-first.txt`:16 failed,591 passed,1 skipped. Exact failures:

```text
FAILED audit/test_browser_round2.py::test_c_list_edit_refresh_export[desktop]
FAILED audit/test_browser_round2.py::test_c_list_edit_refresh_export[mobile]
FAILED tests/e2e/test_guest_photo_session.py::test_issued_photo_intent_and_explicit_card_association
FAILED tests/e2e/test_pages.py::test_list_page_click_edit_export - AssertionE...
FAILED tests/test_cs_api.py::test_link_export_excel - AssertionError: assert ...
FAILED tests/test_csbot.py::test_language_candidates_are_mandarin_and_english_only
FAILED tests/test_fields.py::test_full_catalog_quote_end_to_end - StopIteration
FAILED tests/test_http_concurrency.py::test_h5_text_replans_when_same_visitor_language_changes
FAILED tests/test_manual_category.py::test_h5_chat_flow - AssertionError: ass...
FAILED tests/test_quote.py::test_header_row_and_layout - AssertionError: asse...
FAILED tests/test_quote.py::test_full_fields_three_items - AssertionError: as...
FAILED tests/test_quote.py::test_multi_item_quote_with_adjustment - Assertion...
FAILED tests/test_quote.py::test_twenty_items_rows_and_totals - AssertionErro...
FAILED tests/test_quote.py::test_sample_xlsx_structure_in_tmp - AssertionErro...
FAILED tests/test_quote_delivery.py::test_quote_api_queues_file_in_same_database
FAILED tests/test_userapp.py::test_unreadable_photo_no_note - AssertionError:...
```

Corrections to this full run: Chinese note headers were accidentally normalized from `价格` to `单价`; fixed lookup now preserves original zh labels, retaining the existing semantic export assertions. Explicitly captured default zh overwrote a concurrently selected language on replan; only an explicit request override is now applied, and the replay test still asserts concurrent persistence plus actual selected-language output. Two-language-only/canonical-label expectations and English-default quotation labels were superseded by the approved13-code/zh-default requirements; changed those expectations while preserving numeric/formula/identity tests. Quote footers add visible carton explanations and their row-count assertions were updated with semantic content checks. Task4 photo-mode display wording now comes from the shared resource; browser behavior coverage remains. Original Chinese empty-photo wording was preserved.

- `task-5-covering-second.txt`:74 passed,3 failed. Quote HTTP fixture copied a foreign `shop_id`, correctly rejected by the existing ownership trigger; fixture now uses this app's shop identity. Concurrent preference assertion expected legacy stored `English`; now canonical `en`. Three-product quote row count expected7 but one carton-adjustment footer makes8; test checks its requested100/quoted120 contents.
- `task-5-covering-third.txt`:75 passed after those corrections.
- `task-5-browser-first.txt`: static French flow reached the real download, then test passed an extensionless Playwright artifact path to openpyxl; corrected test uses BytesIO. No production download issue was hidden.
- `task-5-full-second.txt`:609 passed,1 failed,1 skipped. Last failure expected a Chinese export reply despite choosing English; now asserts `Export ready` plus the actual list link.
- `task-5-compiled-browser.txt`, `task-5-compiled-url-red.txt`: compiled H5 browser exposed the extra uni image path rewrite below `/tool/cs/`; `assetUrl` now supplies absolute browser image URLs. `task-5-browser-green.txt`:2 browser tests passed, each exercising French and Arabic, before final resource refinements.
- `task-5-covering-final.txt`:29 passed,1 failed because provider-free fixed fallback no longer needs an English translation model prompt. Kept the concurrent-language replay test but asserted actual English response and persisted canonical locale instead of forcing an unnecessary provider call. `task-5-covering-final-green.txt`:30 passed.

## Final exact verification

Working directory for Python is the isolated worktree. Runner is `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py`; it blocks external sockets/DNS. All new model/vision calls are stubbed, real SQLite/HTTP/openpyxl/Playwright execute. Background test servers are joined.

```sh
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q tests/test_customer_languages.py tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_frontend_session_handlers.py
# 30 passed, 2 dependency warnings; task-5-covering-final-green.txt

DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task5-final-browser-evidence DANGKOU_TEST_H5_DIST=/var/folders/z_/9jwqqszn5c99z0dvq7bydy0m0000gn/T/dangkou-task5-final-source-6yxfm2ey/frontend/dist/build/h5 /Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q
# 614 passed, 1 skipped, 7 warnings in54.17s; task-5-full-final.txt

node tests/test_supplier_quote_plugin.mjs
# quantities, explanation and explicit/default target language passed; task-5-plugin-final.txt
```

The sole final skip is `audit/test_round4_pages.py`'s unavailable real customer photo dataset. Warnings are existing FastAPI/Starlette dependency deprecations and unregistered real_agent marks; no failure omitted. The full run included the fresh compiled H5 test via the environment path above.

Offline frontend dependency lock parity: both lockfiles SHA256 `35f25cc4d4a6c31b1e827fb6fad7a2109cc3718cbd945b0a30d0564761ef2dc8`. Fresh current-source copy at `/var/folders/z_/9jwqqszn5c99z0dvq7bydy0m0000gn/T/dangkou-task5-final-source-6yxfm2ey/frontend`; node_modules symlink only there points to `/tmp/dangkou-frontend-delivery-build/node_modules`. No repo node_modules/dist. Byte comparison after checks confirmed frontend and static source parity with the delivery copy.

```sh
npm test
# structure smoke,22 actual session-handler cases and semantic languages/URLs/storage/merchant quote checks pass
npm run build:h5
# DONE Build complete.
npm run build:mp-weixin
# DONE Build complete.
```

Outputs are `task-5-npm-delivery.txt`, `task-5-h5-delivery.txt`, `task-5-mp-delivery.txt`. Node reports the existing module-type warning; builds succeed. Browser screenshots `/tmp/task5-{fr,ar}-{static,compiled}.png`; Arabic compiled screenshot was visually inspected, exposing reserved Chinese placeholders which were then localized and included in the final full run. `git diff --check` passed.

## Artifact hygiene, limitations and pending work

Full-suite fixtures rewrote74 tracked audit images/ZIP/JSON outputs and three known template audit outputs. They were copied to the unique directory recorded in `task-5-artifact-archive.txt`, then only those tracked generated outputs were restored and the three known untracked generated artifacts removed. No audit output/SDD report, dependency tree or generated build was staged. Checked-in generated language JS is deliberate runtime source generated from the canonical catalog.

No live inference, provider translation quality, real customer photo corpus, production proxy, email, native-speaker review or formal mini-program release is claimed. Fixed locales and behavior are offline verified; dynamic provider failures visibly preserve original text. Stored freeform note values and field keys remain original evidence/schema; exported descriptive values and custom headings now translate for display with protected literals and explicit untranslated fallback (see follow-up). Natural-language intent recognition beyond the stable UI actions depends on the existing model and is not a live13-language model acceptance test. No production deployment, original.env read, credential access, network/model live call or push occurred.

Task4 central-plus-shop authenticated history is still an unanswered dependency; no central-only product decision or untrusted cross-DB account login was implemented. Controller's confirmed direct downstream cancellation finding remains reserved for final lifecycle correction; this task did not modify worker done/limiter lifecycle helpers.

## Changed files (exact commit)

```text
catalog/api.py
catalog/cs_chat.py
catalog/cs_export.py
catalog/cs_i18n.py
catalog/csbot.py
catalog/quote.py
catalog/userapp.py
docs/superpowers/2026-09-28-customer-language-contract.md
engine-plugin/catalog-v2.mjs
frontend/languages.test.mjs
frontend/package.json
frontend/session-behavior.test.mjs
frontend/src/api.js
frontend/src/components/language-picker.vue
frontend/src/components/note-table.vue
frontend/src/customer-catalog.js
frontend/src/customer-languages.json
frontend/src/customer-source-aliases.json
frontend/src/customer-sources.js
frontend/src/i18n.js
frontend/src/pages/chat/chat.vue
frontend/src/pages/list/list.vue
frontend/src/pages/tool/tool.vue
frontend/src/storage.js
frontend/src/use-language.js
scripts/build_customer_languages.py
static/cs/chat.html
static/cs/list.html
static/cs/products.html
static/customer-catalog.js
static/customer-i18n.js
static/customer-sources.js
static/index.html
static/tool/index.html
tests/e2e/test_customer_languages_browser.py
tests/e2e/test_guest_photo_session.py
tests/test_csbot.py
tests/test_customer_languages.py
tests/test_fields.py
tests/test_http_concurrency.py
tests/test_manual_category.py
tests/test_quote.py
tests/test_quote_delivery.py
tests/test_supplier_quote_plugin.mjs
```


## Follow-up: translate notes-export prose and custom headings

Commit: `af18f97 fix: translate descriptive notes export text safely` (parent `9423870`).

Controller correctly identified a binding-spec gap in the original commit/report: preserving stored evidence did not justify leaving every descriptive export cell and custom heading untranslated. This follow-up supersedes that original export limitation; it does not alter source notes, quotation arithmetic, session/history scope or frontend code.

Both central and shop export endpoints now supply the request connection and translation provider to `render_notes`. The renderer gathers visible custom headings and eligible descriptive cells across **all worksheets** into one translation plan, reusing the existing protected-literal/cache path. Known supplier/model/contact/address and numeric fields remain literal; underscore identity aliases such as `model_id` and `supplier_name` normalize consistently. All provider batches finish before translation-cache writes. Existing writers use local resources/cache and a localized original-text notice without a provider call. Normal HTTP ownership reads occur before translation; session touch occurs afterward in existing middleware. DB source values/keys and XLSX numeric types stay unchanged. Provider failures visibly retain original prose/headings with the selected-language notice.

Changed files in this follow-up: `catalog/cs_export.py`, `catalog/api.py`, `catalog/userapp.py`, `tests/test_customer_export_translation.py`, and `docs/superpowers/2026-09-28-customer-language-contract.md`.

Evidence uses fake providers, real HTTP/file SQLite and real XLSX parsing. Six new semantic cases prove translated French custom headings/prose/color, masked models/suppliers/phones/amounts/URLs and exact restoration, unchanged DB/input evidence, numeric cells, honest failed-provider and existing-writer fallback, independent SQLite writes while central/shop HTTP exports wait on translation, 65 descriptions spanning two worksheets and two provider batches before any cache writer, and literal underscore identity fields. No live translation-quality claim is added.

Red/green commands and exact outputs (all logs under this report's SDD directory):

```sh
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q tests/test_customer_export_translation.py
# Before implementation: 4 failed (missing translations/provider call); task-5-export-prose-red.txt
# First implementation: 4 passed, 2 warnings in 0.37s; task-5-export-prose-first-green.txt

# First covering run after the multi-sheet case: 74 passed, 2 warnings in 6.10s;
# task-5-export-prose-covering.txt
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q tests/test_customer_export_translation.py::test_custom_underscore_identity_fields_remain_literal
# Self-review regression before alias correction: 1 failed, 2 warnings in 0.27s;
# task-5-export-alias-red.txt

DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task5-prose-evidence \
DANGKOU_TEST_H5_DIST=/var/folders/z_/9jwqqszn5c99z0dvq7bydy0m0000gn/T/dangkou-task5-final-source-6yxfm2ey/frontend/dist/build/h5 \
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q \
  tests/test_customer_export_translation.py tests/test_customer_languages.py \
  tests/test_cs_api.py tests/test_userapp.py tests/test_note_batches.py \
  tests/test_guest_sessions.py tests/test_h5_transactions.py \
  tests/test_http_concurrency.py tests/e2e/test_customer_languages_browser.py
# Final: 75 passed, 2 warnings in 7.08s; task-5-export-prose-final-green.txt
```

The final covering run includes snapshot/replay and independent-writer tests plus static/compiled French and Arabic browser behavior. The earlier **614 passed, 1 skipped** full run belongs to `9423870`; it is not claimed as a post-follow-up full run. No broad full rerun or frontend rebuild was needed for this backend-only correction; the browser used the existing verified build whose frontend source is unchanged. `git diff --check` passed. No generated audit artifacts changed in this follow-up. Pending Task4B history choice and controller-owned cancellation correction remain unchanged.


## Independent review fix round 1 (I1–I5), base `af18f97`

Commit: `071bc10 fix: complete customer language display and merchant routes`.

All five important findings were verified and corrected. No subagents/reviewers, live providers, original environment/credentials, production changes or push were used. Controller retains the independent review gate.

- **I1:** Renamed the timestamp DOM binding that shadowed `t` in the static central list callback. An actual browser now opens a central note with a real JPEG and verifies the visible note, loaded image, preserved model and translated prose. The same case runs against compiled uni H5.
- **I2:** The merchant gateway serves the exact three public, nonsecret language assets from this release. Existing merchant authentication and verification still precede POST `quote` and bounded GET `quotes/quote-[0-9a-f]{8}.xlsx`; those are the only new API paths/methods. Real prefixed HTTP generates and downloads Arabic XLSX, verifies numeric behavior through the existing quote tests, rejects cross-merchant credentials and wrong quote methods/filenames. The actual merchant runtime browser opens the quote control and verifies all 13 options.
- **I3:** Those exact public assets are also available under the existing customer prefix. The customer proxy forwards a normalized `X-Customer-Language` derived from header/query; administrative paths remain rejected. Real prefixed HTTP checks selected-language fallback and expired-link errors. The child-runtime browser loads the prefixed customer list, leaves a focused cell unchanged without PATCH, intentionally edits its raw model field, and downloads a French Excel preserving that edit.
- **I4:** One validator handles fresh and cached output. It compares complete protected/numeric/contact/URL literal inventories in both directions, including new values, and rejects unchanged nonliteral cache rows. Contact handles are included alongside phones/email/URLs. Rejected cached text is retried or returned with the localized original-text notice. Semantic regressions cover an extra NEW `99.00 USD` in fresh/cached text, bare unchanged Chinese cache, and invented contact handles.
- **I5:** `project_fields`/`project_notes` return separate `display_fields` and `display_labels` under raw authoritative keys. Supplier/model/contact/address/numeric values remain literal. All notes and provider batches are gathered before cache writes; central/shop photo receipts prepare display text before business writes and suppress cache writes. Existing writer callers use cache/fallback without model work. Static/uni list surfaces render projections and preserve raw `fields`; deliberate edits reload display only after mutation completes. Unchanged focus/blur (including translation whitespace) never persists translated values. Locale changes refresh uni list display. HTTP tests prove translated/fallback photo receipts and list prose with unchanged DB originals and successful independent SQLite writes during provider calls; 65-note/two-batch cases cover French and Arabic before cache writer acquisition.

No lifecycle/staging cancellation helper, history decision, quota policy, merchant notification language or quotation arithmetic was changed. The shared export literal-field classifier moved into `cs_i18n` so display and XLSX use the same identity boundary.

### Red and intermediate evidence

All log names below are in this ignored SDD directory; failures were preserved.

1. `task-5-review-r1-display-red.txt`: **8 failed, 14 deselected**. Failing names: `test_translation_rejects_new_monetary_literal[False/True]`, `test_unchanged_chinese_cache_retries_then_falls_back_honestly`, `test_photo_and_list_prose_projection_preserves_source_outside_writer[False/True-central/shop]` (four cases), and `test_central_static_tool_list_with_real_photo`. Command: offline runner `-q tests/test_customer_languages.py -k 'new_monetary or unchanged_chinese or prose_projection' tests/e2e/test_customer_languages_browser.py -k 'new_monetary or unchanged_chinese or prose_projection or central_static'`. Failures showed accepted extra money/unchanged cache, unlocalized receipt prose and zero rendered photo notes.
2. `task-5-review-r1-prefix-red.txt`: **1 failed** from `-q tests/test_merchant_onboarding.py::test_prefixed_language_assets_customer_and_quote_boundaries`; management catalog asset returned 401 before initialization.
3. `task-5-review-r1-first-green.txt`: **23 passed, 4 failed**. Command: offline runner `-q tests/test_customer_languages.py tests/test_customer_export_translation.py tests/test_merchant_onboarding.py::test_prefixed_language_assets_customer_and_quote_boundaries tests/e2e/test_customer_languages_browser.py::test_central_static_tool_list_with_real_photo`. The four new photo-projection fixtures used arbitrary `备注`, which existing extraction folds into canonical `其他` as `备注：...`; corrected the fake vision fixture to the existing canonical `其他` key and retained exact original-storage assertions. No production normalization was altered.
4. `task-5-review-r1-browser-display-red.txt`: **1 failed** from `-q tests/e2e/test_customer_languages_browser.py::test_french_and_arabic_photo_batch_and_export`. Actual French static list still showed `红色` before projection wiring; subsequent green asserts `Rouge`, Arabic localized fallback, no-op PATCH absence and DB original value.
5. `task-5-review-r1-second-green.txt`: **28 passed, 1 deselected** after fixture/projection corrections (same first-green files, all browser cases with `-k 'not compiled'`).
6. `task-5-review-r1-merchant.txt`: **23 passed, 1 failed** from `-q tests/test_merchant_onboarding.py tests/e2e/test_merchant_runtime.py`. The new real-download assertion passed Playwright's extensionless temporary path to openpyxl, which rejects unknown extensions; corrected only the test loader to use `BytesIO` of the actual downloaded bytes. Real runtime load/edit/export had already completed.
7. `task-5-review-r1-noop-red.txt`: `node frontend/languages.test.mjs` failed because unchanged whitespace-surrounded translated text caused a static PATCH. Both handlers now compare normalized display whitespace; the same semantic script passed (`task-5-review-r1-noop-green.txt`) and also verifies intentional edits use raw keys and refresh the projection.
8. First fresh build/source logs: `task-5-review-r1-{npm,h5,mp}.txt` all passed. After the whitespace fix, a second fresh source copy and all frontend checks were run as documented below.
9. `task-5-review-r1-covering.txt`: **106 passed, 2 warnings in 13.06s**; `task-5-review-r1-browser-final.txt`: **4 passed, 2 warnings in 4.23s** before final contact-handle validation refinement.
10. `task-5-review-r1-contact-red.txt`: **1 failed, 2 warnings in 0.27s**, `-q tests/test_customer_languages.py::test_contact_handle_is_protected_and_new_handle_rejected`; cached `Bonjour @invented` was accepted for source `Hello`. Added contact handles to the same complete literal inventory and reran covering dependencies plus browsers.

### Final current-source verification

```sh
DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task5-review-r1-evidence \
DANGKOU_TEST_H5_DIST=/var/folders/z_/9jwqqszn5c99z0dvq7bydy0m0000gn/T/dangkou-task5-review-r1-final-8ytc_jnh/frontend/dist/build/h5 \
/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py -q \
  tests/test_customer_languages.py tests/test_customer_export_translation.py \
  tests/test_merchant_onboarding.py tests/e2e/test_merchant_runtime.py \
  tests/test_h5_transactions.py tests/test_http_concurrency.py tests/test_note_batches.py \
  tests/test_cs_api.py tests/test_userapp.py tests/test_guest_sessions.py \
  tests/e2e/test_customer_languages_browser.py
# 111 passed, 2 warnings in 16.91s
# task-5-review-r1-final-covering.txt
```

Fresh frontend working directory: `/var/folders/z_/9jwqqszn5c99z0dvq7bydy0m0000gn/T/dangkou-task5-review-r1-final-8ytc_jnh/frontend`. Only this temporary directory symlinks the approved offline dependency tree. Exact lock parity SHA256 remains `35f25cc4d4a6c31b1e827fb6fad7a2109cc3718cbd945b0a30d0564761ef2dc8`. After builds, every frontend/static source file was compared byte-for-byte to the worktree and matched.

```sh
npm test
# structure suite, 22 actual session-handler behavior cases, language/URL/quote/no-op edit semantics passed
npm run build:h5
# DONE Build complete.
npm run build:mp-weixin
# DONE Build complete.
```

Outputs: `task-5-review-r1-npm-final.txt`, `task-5-review-r1-h5-final.txt`, `task-5-review-r1-mp-final.txt`. Browser checks actually exercised this build under `/tool/`, French translations, Arabic visible failed-translation fallback/RTL, static+uni no-op edit behavior, and static+uni central tool notes with real photos and translated prose. `git diff --check` passed. No generated audit outputs changed. Only deliberate source/tests/contract docs are committed; SDD/logs/builds/dependencies remain unstaged.

Earlier full-suite **614 passed, 1 skipped** belongs to `9423870`; **75 covering passed** belongs to `af18f97`. This fix has the **111-test focused covering run**, not a new whole-suite claim. M1 warnings remain assigned to Task6 (FastAPI/AnyIO deprecations, Node module mode; this covering run does not exercise unrelated marker warnings). Native-language quality/live inference/production proxy+packaging and real photo-corpus acceptance remain unverified. Task4B central-plus-shop history remains unanswered. Controller's reserved downstream cancellation correction is unchanged.

Exact changed files for this fix:

```text
catalog/api.py
catalog/cs_export.py
catalog/cs_i18n.py
catalog/merchant_binding.py
catalog/userapp.py
docs/superpowers/2026-09-28-customer-language-contract.md
frontend/languages.test.mjs
frontend/src/components/note-table.vue
frontend/src/pages/tool/tool.vue
static/cs/list.html
static/tool/index.html
tests/e2e/test_customer_languages_browser.py
tests/e2e/test_merchant_runtime.py
tests/test_customer_languages.py
tests/test_merchant_onboarding.py
```
