### Scoped Verdict

**I1–I5: ADDRESSED. Fix-round quality: Approved.** No new Critical or Important issue found in the reviewed fix diff.

This is the requested scoped re-review of `af18f97..071bc10`, not a fresh Task5 or whole-branch review. The original task brief, appended fix-round report, supplied diff and preserved final verification logs were checked. Prior acceptance limits and Task4B's pending history decision are unchanged.

### Original Findings

**I1. “Static central tool cannot display a list containing a photo.” — ADDRESSED**

- `static/tool/index.html:181` renames the timestamp binding to `timestamp`; it no longer shadows the `t('photo')` translation call. The same callback now renders the display projection at `static/tool/index.html:177`.
- `tests/e2e/test_customer_languages_browser.py:135` exercises both static and compiled central tool lists with a real JPEG, visible note, loaded image, exact model, translated color/prose and unchanged stored source. This directly covers the previously failing surface, rather than only the shop list.

**I2. “The new merchant quote UI is unusable on the existing management prefix.” — ADDRESSED**

- `catalog/merchant_binding.py:123` serves only the three exact public/nonsecret release assets; `catalog/merchant_binding.py:166` permits them without requiring a script request to carry a merchant credential.
- `catalog/merchant_binding.py:218` adds only POST `quote` and bounded GET `quotes/quote-[0-9a-f]{8}.xlsx`. These routes still pass through the existing merchant credential/verification boundary before merchant-specific forwarding; arbitrary filenames and wrong methods are not admitted.
- `tests/test_merchant_onboarding.py:345` sends real HTTP through the gateway to the isolated app, generates/downloads Arabic XLSX, and checks wrong methods/filenames. Cross-merchant credential rejection covers both quote creation and download at `tests/test_merchant_onboarding.py:390` and `:391`.
- `tests/e2e/test_merchant_runtime.py:140` opens the actual management quote control and verifies all 13 options. The browser initialization plus real prefixed quote/download HTTP cover both halves that the original mocked handler test missed.

**I3. “Merchant-prefixed customer list pages now fail before their existing load/edit/export controls initialize.” — ADDRESSED**

- `catalog/merchant_binding.py:136` serves the exact public assets under the customer prefix. `catalog/merchant_binding.py:148` forwards normalized header/query language to the isolated runtime while retaining the customer API allowlist.
- `tests/e2e/test_merchant_runtime.py:120` opens the actual customer-prefixed list, checks unchanged focus/blur causes no PATCH, edits the raw model field, and downloads French XLSX preserving that edit.
- `tests/test_merchant_onboarding.py:403` checks the selected-language expired-link error; the same test checks the display fallback and continued rejection of the customer-prefixed administrative quote path.

**I4. “Translation/cache validation does not enforce the claimed literal and honest-fallback boundary.” — ADDRESSED**

- `catalog/cs_i18n.py:266` centralizes validation. It rejects unchanged nonliteral text and unresolved internal placeholders, then compares the complete protected/numeric/contact/URL inventories in both directions at `:272`.
- Both cache reuse (`catalog/cs_i18n.py:296`) and fresh restored output (`:316`) invoke this validator. Contact handles are included in the literal matcher. Rejected cache rows become missing translations, so they retry or receive the existing localized fallback.
- `tests/test_customer_languages.py:201` covers the previously accepted NEW monetary value in fresh and cached output; `:214` covers unchanged Chinese cache retry/fallback; `:300` covers preserved and newly invented contact handles. These directly match the original bounded counterexamples.

**I5. “Photo replies and on-screen descriptive notes bypass dynamic translation and its fallback notice.” — ADDRESSED**

- `catalog/cs_i18n.py:349` constructs separate `display_fields`/`display_labels` under raw field keys, preserving identity/numeric values and authoritative source data. `:382` projects all notes together. Receipt preparation at `:389` disables cache writes, so callers can enter their later business write transaction cleanly.
- Shop and central photo receipts prepare display text before business writes (`catalog/api.py:1354`, `catalog/userapp.py:307`). Shop/central list endpoints return the projection (`catalog/api.py:1464`, `catalog/userapp.py:361`). Existing-writer/no-provider callers explicitly use cache/fallback (`catalog/cs_i18n.py:372`).
- Static and uni surfaces consume the projections (`static/cs/list.html:64`, `static/tool/index.html:177`, `frontend/src/components/note-table.vue:90`, `frontend/src/pages/tool/tool.vue:123`). Locale changes refresh uni display. No-op edits compare normalized display whitespace, and intentional edits keep raw field keys and reload after the mutation (`static/cs/list.html:79`, `frontend/src/components/note-table.vue:126`).
- `tests/test_customer_languages.py:228` checks successful and failed translation for central/shop photo and list HTTP, unchanged DB evidence, and a real independent SQLite writer during translation. `:276` checks 65 notes across two provider batches in French/Arabic before cache writer acquisition. Static/compiled browser cases check translated French or explicit Arabic fallback and no-op persistence behavior (`tests/e2e/test_customer_languages_browser.py:56`, `:112`).

### New Blocking Findings

- **Critical:** None found.
- **Important:** None found.

### Evidence and Review Limits

- Read the supplied fix diff once, in four contiguous code/test passes; used its line mapping to cite the fixes. Did not rederive git diff, reread untouched implementation, rerun reported suites/builds, or broaden into unrelated lifecycle/ownership work. No unresolved code doubt required another execution probe.
- Inspected `.superpowers/sdd/2026-09-28-h5-trial/task-5-review-r1-final-covering.txt`: **111 passed, 2 warnings in 16.91s**. Its warnings are the already reported FastAPI/Starlette HTTPX and AnyIO deprecations.
- Inspected `task-5-review-r1-npm-final.txt`: 22 session-handler cases plus URL/language/quote/no-op edit checks pass; existing Node module-mode warning remains. Both `task-5-review-r1-h5-final.txt` and `task-5-review-r1-mp-final.txt` end with successful build completion. The report attributes these checks to the fresh source copy and records source/lock parity; no separate full-suite claim is made for this fix commit.
- No source/index/HEAD mutation, subagent, provider/network call, secret access or live action. Only this required ignored review report was written.

### Nonblocking / Outside This Re-review

- Prior M1 dependency/marker/module-mode warning hygiene remains assigned to Task6; this fix is not represented as warning-free.
- Native-language quality, live inference, production proxy/packaging, real photo-corpus acceptance and formal mini-program release remain the previously recorded validation limits. Task4B authenticated history scope and the controller-reserved cancellation work remain pending elsewhere. No new out-of-scope defect is asserted here.
