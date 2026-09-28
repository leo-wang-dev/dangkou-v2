### Spec Compliance

- ❌ Issues found: supplier disambiguation does not cover first-line matching of multiline model names (`catalog/csbot.py:490`).
- ⚠️ Actual extraction of pictures after adding/changing an image field is not verified by this diff. `tests/test_supplier_browser.py:20` checks collected role and persisted template, not parsed image output; the controller should retain source/image association validation in Task3. No definite extraction defect established.

### Strengths

- `catalog/db.py:52` adds/backfills supplier only during migration; `catalog/dynamic_catalog.py:233` preserves explicit product ownership on subsequent writes. File-SQLite regression covers migration and category-default changes (`tests/test_supplier_products.py:120`).
- `catalog/dynamic_import.py:24` and `catalog/dynamic_import.py:693` use the same full-category snapshot for cross-filename matching and approval validation. Supplier edits participate in snapshots; the safe rejection of supplier-less legacy snapshots has unchanged-catalog assertions (`tests/test_supplier_products.py:177`).
- `catalog/dynamic_import.py:41` rejects duplicate incoming identities and ambiguous variants rather than choosing arbitrary rows; missing-row delisting is disabled during both classification and application. Updated old tests legitimately replace obsolete filename identity, implicit delisting, and mutable-spec assumptions (`tests/test_import_conflicts.py:81`, `tests/test_dynamic_import.py:88`).
- `catalog/quote.py:195` uses Decimal half-up rounding and preserves requested/quoted quantities; HTTP, workbook, and plugin tests cover 66.95, 80 units, 5356 total, boundary deposits, and merchant explanation (`tests/test_quote.py:392`, `tests/test_supplier_products.py:84`, `tests/test_supplier_quote_plugin.mjs:9`).
- `catalog/dynamic_catalog.py:271` restricts customer titles to public model fields; the regression checks the full public projection for the internal model string (`tests/test_supplier_products.py:28`).

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

- `catalog/csbot.py:490`: supplier filtering is applied only in the complete-name branch (`:481`), while the first-line model branch returns every matching supplier. With candidates `M1\n铝合金` from 厂甲 and 厂乙, the explicit request `厂乙 M1` returns both IDs. This leaves an ordinary imported multiline model ambiguous after the customer has supplied the required supplier. Apply the same supplier narrowing to both matching paths, with a regression for multiline names and distinct supplier/product IDs.

#### Minor (Nice to Have)

- `catalog/quote.py:214`: returned quote detail takes the raw persisted supplier, whereas product reads resolve an empty legacy supplier to the category default (`catalog/dynamic_catalog.py:293`). A legacy empty-supplier row therefore appears with its default supplier in catalog results but loses that supplier in quote details/adjustment notifications. Resolve the documented fallback consistently when building quote details.
- `tests/test_supplier_browser.py:3` / `/tmp/task2-final-green.log`: verified final evidence contains two dependency deprecation warnings (Starlette/httpx and anyio BlockingPortal). Existing noise, not a task behavior regression; clean up through dependency/test-harness maintenance. The reported unregistered real_agent marker warnings likewise remain test hygiene work.

### Assessment

**Task quality:** Needs fixes.

**Reasoning:** Persistence, import identity protection, no-delist behavior, and Decimal outputs are cohesive and have meaningful regressions. Supplier selection still misses a production matching branch and should be fixed before accepting Task2.

### Checks and Scope

- Read supplied `review-47c3891..d81639e.diff` once in four chunks. No git commands, full-suite reruns, or code changes performed.
- Focused reproduction only: `PYTHONDONTWRITEBYTECODE=1 .../.venv/bin/python` called `CsBot._matching_products('厂乙 M1', ...)` with two multiline-name candidates and printed `['a', 'b']`; expected `['b']`.
- Inspected cut-off function context only: `catalog/dynamic_import.py:235` (image field handling), `:676` (approval snapshot preflight), `catalog/quote.py:156` (product/category ownership and quantity validation), and `catalog/agent.py:20` (image field prompt handling).
- Named external-code risk: mutable stock/note fields accidentally becoming variant identity. Focused role-definition lookup in `catalog/workbook_templates.py:45` and `catalog/agent.py:85` confirms distinct stock/note roles; the template classifier selects only spec roles. Initial lookup also named two nonexistent files and returned errors; existing authoritative definitions resolved the doubt.
- Read `/tmp/task2-final-green.log`: 100 passed, two stated deprecation warnings. Reviewed the supplied report's full-suite failures and the diff's fixture/decimal expectation corrections; these preserve stale-ticket behavior and are legitimate. Existing JS import matrix phase/mode mismatch and partial-failure/crash/notification work remain Task3 scope.

### Scoped Re-review — 48ff1b9

- **Final spec-compliance verdict: ✅ Spec compliant within Task2 scope.** The prior Important supplier-disambiguation defect is resolved: `catalog/csbot.py:467` centralizes supplier narrowing and both complete-name and first-line branches invoke it (`:485`, `:493`). The regression uses real persisted public products and verifies ambiguity without a supplier plus correct selection for each supplier (`tests/test_supplier_products.py:202`).
- **Final task-quality verdict: Approved.** No remaining blocking findings or fix-introduced defects found in `review-d81639e..48ff1b9.diff`.
- Prior Minor quote fallback finding resolved: `catalog/quote.py:145` resolves empty stored suppliers from the existing template, and quote details consume that resolved value (`:216`). HTTP regression verifies catalog/quote agreement and supplier presence in both adjustment text and the merchant notification (`tests/test_supplier_products.py:215`).
- Read the fix diff once and the appended implementer report. Read `/tmp/task2-fix1-green.log`: **78 passed, two existing dependency deprecation warnings**. No test rerun was needed: the new tests directly cover both prior findings, and no new concrete doubt arose. Plugin success is reported by the implementer; the plugin was unchanged in this fix.
- Actual edited-image extraction validation remains Task3; dependency/marker warning cleanup remains Task6. These are the previously recorded handoffs, not unresolved Task2 blockers. No git operations or writes beyond this review report.
