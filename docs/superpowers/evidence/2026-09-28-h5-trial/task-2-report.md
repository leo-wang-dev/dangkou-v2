# Task2 implementation report

Status: DONE_WITH_CONCERNS (implementation complete; full suite had obsolete expectations fixed and covered by narrow reruns, not rerun wholesale; existing unrelated JS matrix contract mismatch remains).

## Implemented

- Added `product_dynamic.supplier`, additive migration/backfill from category defaults, preserved explicit product supplier across edits. Category supplier survives template approval and remains a default/fallback. Merchant direct and ticketed writes support supplier, admin product/stats responses expose explicit supplier, supplier visibility counts aggregate across categories.
- Import identity now spans reuploaded filenames in the category: supplier + model + spec fields. Price/cost/stock/note changes can update the same variant, differing specs are distinct. Row supplier wins, followed by parsed document vendor, then category default. Agent prompt explicitly requests per-row supplier. Ambiguous/missing specs, duplicate matching records, and duplicate incoming identities raise clear supplier/model/row errors instead of arbitrary update/new duplicate. Draft supplier is editable in approval UI.
- Snapshot validation covers full category and supplier, including merchant mutation snapshots. Missing Excel rows never delist: classifier returns empty delist and application ignores old implicit delist drafts. Merchant explicit deletion remains intact.
- Decimal ROUND_HALF_UP unit price to two places, precise amounts/deposit. 65 + 3% = 66.95; requested50/carton40 -> 80, total5356. Common carton counts plus independent pcs/gross/net/dimensions mappings. API returns requested/quoted quantities and supplier/product identity. Plugin preserves details and notifications explain carton adjustment.
- Template approval UI supports type/deletion; added image fields infer role=image. Category supplier action now exists; products have independent supplier edit. Customer titles use public model fields only. Supplier is carried/displayed on customer candidates and used to disambiguate same-model selections.
- Tracked interface doc: docs/superpowers/2026-09-28-supplier-quote-contract.md.

## TDD / verification evidence

All Python commands use `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py` from the assigned worktree. Only model/transport boundaries are faked. New supplier tests use file SQLite; HTTP, approval, public projection, generated Excel and browser clicks use production implementations.

1. RED: `... tests/test_supplier_products.py tests/test_quote.py -q` => 11 failed,42 passed (`/tmp/task2-red.log`). Expected failures: supplier lost, private model leaked, supplier collision/filename identity, 66.95 price and five carton formats. GREEN after implementation: 49 pass,4 obsolete integer-round quote tests fail; updated those explicit expectations to approved decimal rules.
2. RED: `... tests/test_supplier_products.py -q` => 2 failed,5 passed (`/tmp/task2-red2.log`), HTTP supplier output and customer supplier selection. GREEN combined supplier/quote/browser: 58 passed,2 warnings (`/tmp/task2-green3.log`).
3. Browser RED: `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/task2-browser ... tests/test_supplier_browser.py -q` => expected missing delete control timeout (`/tmp/task2-ui-red.log`), after correcting initial test's detail-button wording. GREEN as above; actual type/image collection, template approval, category supplier default and product supplier saves checked.
4. Plugin RED: `node tests/test_supplier_quote_plugin.mjs` => returned items undefined (`/tmp/task2-plugin-red.log`); GREEN => `supplier quote plugin quantities and explanation passed`. `node --check engine-plugin/catalog-v2.mjs` passed.
5. RED supplier ambiguity/document vendor: 2 failed,8 passed (`/tmp/task2-red4.log`). Related GREEN: `... tests/test_supplier_products.py tests/test_quote.py tests/test_dynamic_catalog.py tests/test_dynamic_import.py tests/test_customer_catalog.py tests/test_import_conflicts.py tests/test_quote_delivery.py tests/test_agent_import.py tests/test_photo_inquiry.py -q` =>108 passed,3 warnings (`/tmp/task2-related2.log`). Intermediate related run had seven failures: one error message displayed case-folded model (fixed), intended variant/no-delist and filename-identity old expectations (updated), and photo candidates' newly explicit supplier field expectations (updated).
6. Full offline suite ONCE: `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/task2-full-browser ...` =>526 passed,1 skipped,3 failed,6 warnings,49.93s (`/tmp/task2-full.log`), exit1. Failures:
   - `tests/test_fields.py::test_full_catalog_quote_end_to_end`: obsolete integer price total1320 vs correct1359.60; updated assertion.
   - `audit/test_browser_round2.py::test_b_import_edit_partial_reject_approve`: helper fabricates empty source snapshot despite existing product in category; now uses production full-scope source rows.
   - `audit/test_round3_pages.py::test_import_edit_retains_visibility`: helper manually omitted supplier when constructing snapshot; now includes supplier.
   These fixture adjustments preserve real stale-ticket rejection. Known async-template-import poll failure did NOT occur in this full run.
7. Self-review RED: missing variant attrs and legacy implicit delist 2 failed,10 passed (`/tmp/task2-red-final.log`); fixed conservative ambiguity and old delist guard. Covering GREEN: `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/task2-final-browser ... tests/test_supplier_products.py tests/test_supplier_browser.py tests/test_fields.py tests/test_dynamic_import.py tests/test_import_conflicts.py audit/test_browser_round2.py::test_b_import_edit_partial_reject_approve audit/test_round3_pages.py::test_import_edit_retains_visibility -q` =>42 passed,2 warnings (`/tmp/task2-final-focused.log`). This explicitly reruns all three full-suite failures.
8. Final ambiguity RED: repeated incoming same variant could choose one update and insert another =>1 failed,13 passed (`/tmp/task2-ambiguity-red.log`). Added preflight rejecting duplicate incoming variant identities. GREEN final core/related/browser: `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/task2-final-browser ... tests/test_supplier_products.py tests/test_dynamic_import.py tests/test_import_conflicts.py tests/test_dynamic_catalog.py tests/test_quote.py tests/test_customer_catalog.py tests/test_photo_inquiry.py tests/test_supplier_browser.py -q` =>100 passed,2 warnings (`/tmp/task2-final-green.log`). Includes focused legacy supplier-less snapshot rejection with unchanged product assertions.
9. Additional boundary/compatibility checks: `... tests/test_quote.py tests/test_photo_inquiry.py -q` =>56 passed,2 warnings (`/tmp/task2-quote-final.log`); explicit -100, below -100, Decimal 1.005 ->1.01, retained legacy candidate shape. No implementation change after these checks. `git diff --check` passed. Browser final screenshot inspected: distinct product supplier and category default visible, usable layout.

An exploratory run of existing `node tests/test_plugin_wechat_matrix.mjs` failed at its preexisting catalog_import call lacking required `phase` (and temporary phase correction exposed missing `mode`). That unrelated test file was restored exactly and is NOT changed/staged. New isolated quote plugin regression covers this task without relying on those obsolete import fixtures. These failures are not claimed green.

Warnings are existing FastAPI/Starlette httpx/anyio deprecations and unregistered real_agent markers. No live model, Telegram/email, real Excel corpus or production deployment quality is claimed.

## Self-review / handoff to Task3

- Supplier top-level field is the persistence/API/import contract. Draft edits use `__supplier`; no new conflict draft shape. `_classify_rows` raises ValueError with supplier/model/row on ambiguous data; Task3 may turn that into partial row failures. Do not swallow it into empty success.
- `_source_rows` now means the full category snapshot even when old source arguments are passed; classification restricts supplier/model/spec. Scope is conservative: unrelated category edits can invalidate pending imports, safely requiring reimport.
- Historical supplier-less/source-local snapshots that cannot validate against new scope safely return existing409/reimport guidance. Per controller ruling, no unsafe compatibility acceptance. Regression proves unchanged products on rejection. Empty/no-existing legacy snapshots that still validate are not universally invalidated. Approved catalogs preserved by additive migration.
- Existing row fingerprint alone no longer skips changed data; supplier and data must match. Variants are conservative: spec changes create distinct products; missing attrs that overlap existing variants require merchant clarification.
- API/index/import files are already large; changes kept local without architecture restructuring. Request transaction ownership, cs_chat replay/snapshot, request_lifecycle worker cleanup unchanged.
- No generated audit assets staged; original env/production untouched. New screenshot/trace evidence stays under /tmp/task2-*-browser. Full suite deliberately run once; controller approved narrow final verification after known expectation fixes.

## Changed files

catalog/schema.sql; catalog/db.py; catalog/dynamic_catalog.py; catalog/dynamic_import.py; catalog/quote.py; catalog/api.py; catalog/agent.py (per-product supplier extraction instruction); catalog/tickets.py (supplier mutation/edit/snapshot); catalog/csbot.py and catalog/photo_inquiry.py (approved scoped customer supplier disambiguation); static/index.html; engine-plugin/catalog-v2.mjs.

tests/test_supplier_products.py; tests/test_supplier_browser.py; tests/test_supplier_quote_plugin.mjs; tests/test_quote.py; tests/test_dynamic_import.py; tests/test_import_conflicts.py; tests/test_photo_inquiry.py; tests/test_fields.py; audit/test_browser_round2.py; audit/test_round3_pages.py (only fabricated snapshot fixture updates); docs/superpowers/2026-09-28-supplier-quote-contract.md.

Commit: `d81639e fix: preserve product suppliers and decimal carton quotes` (23 deliberate files). Ignored report not staged.

## Review fix round1 (base d81639e)

Addressed only the Important multiline-name supplier narrowing and Minor quote fallback findings in task-2-review.md. `CsBot._matching_products` now shares supplier narrowing between complete-name and first-line model branches. Quote extraction resolves raw empty supplier through the already-loaded category template; details and existing notification generation therefore agree with catalog reads. No import/image parsing, dependencies or lifecycle behavior changed.

RED: `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/test_supplier_products.py -k 'multiline_model or quote_details_and_notification' -q` => **2 failed,14 deselected,2 warnings** (`/tmp/task2-fix1-red.log`). Multiline request returned both IDs instead of supplier-selected b; legacy quote supplier was empty instead of 默认厂. Regressions exercise file SQLite public product projection and real HTTP quote generation, checking API details and notification text.

GREEN: `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py tests/test_supplier_products.py tests/test_quote.py tests/test_customer_catalog.py tests/test_photo_inquiry.py tests/test_quote_delivery.py -q` => **78 passed,2 warnings in0.71s** (`/tmp/task2-fix1-green.log`). `node tests/test_supplier_quote_plugin.mjs` => **supplier quote plugin quantities and explanation passed**. `git diff --check` passed. Reviewed the three-file diff; no full-suite rerun per controller scope. Existing dependency warnings remain Task6; actual added-image extraction verification remains Task3.

Files: catalog/csbot.py, catalog/quote.py, tests/test_supplier_products.py. Report ignored and not staged.

Fix round1 commit: `48ff1b9 fix: resolve suppliers consistently in matching and quotes`.
