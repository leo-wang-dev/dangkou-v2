### Spec Compliance

- ❌ Issues found. The 13-language registry, language propagation, quote parameter, staged export translation and `/tool/` CS URL work are implemented, but Task 5 is not compliant yet: one existing customer list breaks, merchant-prefixed pages cannot load the added assets or quote actions, dynamic photo/note prose remains untranslated without notice, and translation validation accepts corrupt outputs. See I1–I5.
- ⚠️ Cannot verify from this diff: native-language translation quality, live provider behavior, production proxy deployment and packaging of `frontend/src/customer-languages.json`, formal mini-program release, and the unavailable real customer photo corpus. Controller should retain these as explicit acceptance/deployment checks. The reported offline H5/mini-program builds do not establish these properties.
- ⚠️ Cross-task limits: shop-to-central authenticated history remains Task4B pending user input; this review makes no history-scope assumption. Whole-branch transaction/cancellation lifecycle, redline coverage and unrelated ownership paths are not re-audited here. No new normal guest quota is present in the reviewed changes.

### Strengths

- `catalog/cs_chat.py:127` and `catalog/cs_chat.py:153` apply an explicit language inside both snapshot planning and replay, leaving absence of an override to persisted preference. `tests/test_customer_languages.py:94` exercises translation while a real independent SQLite writer proceeds; `tests/test_http_concurrency.py:160` retains concurrent preference/replay assertions.
- `catalog/cs_export.py:123` plans display translations across worksheets and uses cache/fallback when the supplied connection already has a transaction; `catalog/cs_i18n.py:313` waits until every provider batch has finished before writing new translations. `tests/test_customer_export_translation.py:84` and `tests/test_customer_export_translation.py:124` cover real HTTP/SQLite concurrency and multiple sheets/batches.
- `catalog/quote.py:177`, `catalog/quote.py:222` and `catalog/quote.py:250` localize worksheet headings/direction and totals while retaining model cells and the existing Decimal/carton arithmetic. `tests/test_customer_languages.py:77` checks all 13 quote languages with real XLSX numeric cell assertions.
- `frontend/src/api.js:41` fixes CS URL resolution and `frontend/src/api.js:47` accounts for uni image rewriting. `frontend/languages.test.mjs:5` invokes actual API helpers, and `tests/e2e/test_customer_languages_browser.py:64` checks compiled H5 under `/tool/` using French and Arabic.
- Canonical locale data contains 182 keys with all 13 nonempty translations and matching interpolation placeholders. Both generated JS catalog copies and both alias copies are exact semantic matches to their canonical JSON in the supplied diff. Admin labels and notification prose remain Chinese; the small per-product quote action stays within the controller-approved scope.

### Issues

#### Critical (Must Fix)

None found in this task-scoped review.

#### Important (Should Fix)

**I1. Static central tool cannot display a list containing a photo.**

- Evidence: `static/tool/index.html:171` calls `t('photo')` inside the `notes.forEach` callback, but `static/tool/index.html:181` declares `const t` in that same callback for the timestamp element.
- Actual behavior: the local lexical binding shadows the translation function throughout the callback. Every normal photo note throws a temporal-dead-zone `ReferenceError` before it is appended; the outer catch reports a network error and leaves the list empty/partially rendered. This affects Chinese as well as other languages and breaks an existing customer control.
- Focused proof: executed the shipped `openList` body reconstructed from the supplied diff in a Node VM, with one successful notes response containing `photo:'/notes/1/photo'`. Result: `rendered:0`, `tips:["networkError"]`. This is not a server or translation-provider failure.
- Fix: rename the timestamp DOM variable and add a central static-tool list test using an actual photo note. Existing browser tests exercise the shop list, so they do not catch this path.

**I2. The new merchant quote UI is unusable on the existing management prefix.**

- Evidence: `static/index.html:112` adds a relative catalog script; `static/index.html:691` submits `/quote` through the management prefix and `static/index.html:692` downloads the prefixed `/quotes/...` URL. The unchanged boundary is `catalog/merchant_binding.py:150` (management credential extraction) and `catalog/merchant_binding.py:200`–`:203` (strict route allowlist).
- Actual behavior: at `/merchant/manage/<mid>/`, the script requests `/merchant/manage/<mid>/customer-catalog.js` without the service-token header/query credential and is rejected. Even if that catalog were already available, both `quote` and `quotes/quote-<id>.xlsx` are outside the proxy allowlist and return 404. The passing Node handler test mocks these exact requests, while the HTTP test calls the isolated app directly, so neither verifies the shipping entry point.
- Fix: serve the nonsecret shared language catalog through an appropriate accessible static path; explicitly allow only POST `quote` and bounded GET `quotes/quote-[0-9a-f]{8}.xlsx` after the existing merchant authentication/verification, forwarding to that merchant's runtime with its private service credential. Add a local real HTTP test for the management-prefixed path and cross-merchant rejection. Do not broaden the proxy to arbitrary paths.

**I3. Merchant-prefixed customer list pages now fail before their existing load/edit/export controls initialize.**

- Evidence: `static/cs/list.html:42`–`:46` requires three new relative scripts and immediately destructures `CustomerI18n`. `catalog/merchant_binding.py:127` only allows `cs/list.html` and the bounded `cs/link/...` routes. Its forwarding call at `catalog/merchant_binding.py:139` also drops `X-Customer-Language`.
- Actual behavior: opening `/merchant/customer/<mid>/cs/list.html?k=...` resolves the scripts to `/merchant/customer/<mid>/customer-*.js`; all three are denied by the proxy allowlist. `CustomerI18n` is undefined, so the entire list script stops. This is a regression to the pre-existing merchant customer list route, not merely a missing translation. Once assets are fixed, fetch-based selected-language errors still lose the header at this proxy; the query parameter on export alone does not fix those requests.
- Fix: expose these public static assets without widening access to merchant APIs (or use correct shared root asset URLs), and forward only the validated customer language header/query to the isolated runtime. Add a local prefixed customer-list browser/HTTP case covering load, edit, localized error and export.

**I4. Translation/cache validation does not enforce the claimed literal and honest-fallback boundary.**

- Evidence: `catalog/cs_i18n.py:287` only counts occurrences of literals that already existed in the source when accepting cached text; `catalog/cs_i18n.py:307` performs the same one-sided check for fresh output. Cache acceptance does not reject an unchanged source/target pair.
- Actual behavior, confirmed with the production translator and a fake provider over real in-memory SQLite:
  - Source `Order KS-1100 at 66.95 USD` produced and cached `Commander KS-1100 at 66.95 USD 99.00 USD`. The added monetary value passes because all original values still occur the expected number of times.
  - A persisted French cache pair `source='红色外壳', target='红色外壳'` returns bare Chinese without the localized untranslated notice or a provider attempt.
- Impact: dynamic customer chat and note/quote prose can acquire invented numbers/prices, and old/corrupt cache rows can silently masquerade as translated content. The existing corruption test only adds another occurrence of the *same* original number, which the current count check catches.
- Fix: centralize output validation for fresh and cached values; compare the complete protected/numeric/contact/URL literal inventory in both directions, and invalidate unchanged nonliteral source/target cache rows under the same policy used for provider responses. Retry/fallback honestly when rejected. Add focused new-number and unchanged-cache regression cases.

**I5. Photo replies and on-screen descriptive notes bypass dynamic translation and its fallback notice.**

- Evidence: `catalog/cs_i18n.py:327` interpolates values using only `display_value`; `catalog/cs_i18n.py:345` handles reserved missing/unclear markers and a fixed suffix, not descriptive prose. This is selected by both `catalog/api.py:1379` and `catalog/userapp.py:321`. The corresponding note list displays at `static/tool/index.html:177` and `frontend/src/pages/tool/tool.vue:123` also use this marker-only function.
- Actual behavior: a French receipt for `{'型号或品名':'KS-1100','颜色':'红色','备注':'可折叠便于收纳'}` is `Modèle / article=KS-1100; Couleur=红色; Remarques=可折叠便于收纳`, surrounded by French fixed copy and with no untranslated notice. This was confirmed with a bounded direct call; there is no provider seam in this path. The export follow-up correctly adds translated display prose for spreadsheets but leaves these customer chat/list surfaces behind.
- Impact: selected languages are applied to labels, not the actual nonliteral customer-facing descriptions; failure is not made explicit. Preserving stored source evidence does not require presenting untranslated prose as if localization succeeded.
- Fix: prepare display translations for descriptive fields/custom labels outside business writer transactions, preserve identities/numeric values and stored evidence, and return a separate display projection plus an explicit localized fallback when unavailable. Both static and uni clients should render that display projection without writing it back merely on focus/blur.

#### Minor (Nice to Have)

**M1. Existing verification warnings remain; output is not pristine.**

- Evidence: `.superpowers/sdd/2026-09-28-h5-trial/task-5-export-prose-final-green.txt:4` reports FastAPI/Starlette HTTPX and AnyIO deprecations; the full-run log also identifies unregistered marks at `tests/test_agent.py:40`, `tests/test_agent_import.py:208`, `tests/test_agent_isolation.py:12`, `:34`, and `:41`. `frontend/package.json:10` runs Node semantic tests that emit `MODULE_TYPELESS_PACKAGE_JSON` in the delivery log.
- Impact/fix: these are existing hygiene issues, not a new Task5 blocker. Keep them assigned to queued Task6: register the marker, resolve dependency deprecations through compatible upgrades, and explicitly configure the frontend module mode without breaking existing build/test entry points.

### Review Checks and Scope

- Base `b114f34`; head `af18f97`. Read the supplied review package in sequential hand-written-code/test passes. Large generated locale sections were reviewed by parsing their added contents directly from the diff, validating 13-code coverage/nonempty values/placeholders, printing the Chinese/English/French/Arabic catalog values for inspection, and verifying generated-copy parity. This is not a native-speaker review of all locales.
- The initial combined tool output truncated the API/export portion. Re-read only that missing diff range (`280–690`) to finish the affected API, staged chat and export functions; did not rederive `git diff` or separately reread changed source files. Probes reconstructed the complete static `openList` function from diff context.
- Named unchanged-code check: new quote/download route and relative locale scripts crossing merchant ownership/routing boundaries. Inspected only the relevant proxy registration/authentication/allowlists/forwarding in `catalog/merchant_binding.py`. Direct quote download in `catalog/api.py:1116` remains token-protected and bounded to the app's `_quotes` directory; the identified issue is integration through the existing authenticated proxy, not evidence of direct cross-shop file access.
- Ran only the bounded Node static-list probe and Python translator/cache/receipt probes described above. Fake network responses/provider, no live network/model calls, no original `.env`, messages, deploys, suite reruns, index or branch changes. Only this required ignored review report was written.
- Inspected preserved evidence: full run on `9423870` is **614 passed, 1 skipped, 7 warnings**; follow-up covering run on `af18f97` is **75 passed, 2 warnings**. The report correctly distinguishes these commits. Node delivery results show 22 session cases and the language/URL/control checks passing, with the module warning. Those results do not cover the blockers above.

### Assessment

**Task quality: Needs fixes.**

The registry, staged translation work and semantic export tests are solid, but actual customer and merchant entry points still fail and two dynamic-text boundaries violate the binding language requirements. Address I1–I5 with targeted regression tests before this task is accepted; no broad whole-branch re-review was performed.
