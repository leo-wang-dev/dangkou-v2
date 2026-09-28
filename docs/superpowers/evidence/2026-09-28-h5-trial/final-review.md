# Whole-branch final review — 354b96f..472bcc1

Reviewed the binding spec, plan, final brief/notes, progress rulings, supplied sanitized whole-branch package, relevant task evidence and current implementation. This is the single consolidated whole-branch pass, not a replacement for the completed task gates.

## Behaviors considered but declined to judge as outside this review

- Selecting central-only versus central-plus-shop authenticated history: a required unanswered product choice (Task4B), not reviewer discretion. No selection or full-product completion is inferred.
- Actual provider accuracy, native-language fluency, original workbook logical counts and 600-second import target: require authorized live providers and the intended datasets. Structural rows and fake-model tests cannot decide these.
- Actual deployed DNS/TLS/proxy configuration, native systemd, Docker/bridge authentication, target-host memory/flock permissions, real mailbox delivery and physical-device behavior: no live environment access authorized/available. The supported code/configuration routing defect below is nevertheless judged; it is not deferred as an environment uncertainty.
- Shared historical credential purge/rotation: current-file removal is in scope; shared history rewrite and credential use/rotation are not authorized. No raw historical section was opened.
- Formal mini-program release: explicitly a separate milestone; retained build compatibility is the present requirement.
- Unrelated product expansion, new pricing/history policy, alternate parser providers, and exhaustive baseline security audit: outside the accepted H5 trial scope. No additional behavior was silently excluded because a prior task reviewer had approved it.

## Verdicts

**Spec compliance: Needs fixes for the implemented scope.** Two Important integration/lifecycle defects and one Minor fixed-error localization defect remain. The branch substantially implements the independently approved work, but cannot yet satisfy the H5 transaction/resource and supported tenant entry requirements. **Overall product remains pending Task4B**, independently of these corrections.

**Code quality / merge readiness: Needs fixes.** Consolidate I1, I2 and M1 into the final correction wave and scoped re-review. No Critical defect established. Do not label this branch production-accepted after offline corrections: named live acceptance remains open.

## Findings

### I1 — Important: downstream task cancellation ends resource ownership before the real worker exits

- **Locations:** `catalog/api.py:122–129`, `catalog/userapp.py:128–135`; cleanup trusts that event at `catalog/api.py:137–179` and `catalog/userapp.py:144–186`.
- **Trigger:** Direct native `asyncio.Task.cancel()` targets the downstream endpoint task while its `anyio.to_thread.run_sync` callback is still inside model work. This is distinct from cancelling the outer HTTP task or an AnyIO scope.
- **Evidence:** `cancellation-probe.md` records both real registered photo routes, file SQLite and bounded held model callbacks at b114f34. Before releasing either actual worker, the async-wrapper event was set, the request connection closed and the model limiter token became borrowable; both workers then raised `ProgrammingError: Cannot operate on a closed database`. Current HEAD retains the same faulty `finally: done.set()` wrapper, so the causal boundary is unchanged. I did not duplicate the preserved probe or settled outer-cancellation tests.
- **Impact:** Use-after-close and failed customer work; the apparent four-model cap admits replacement work while cancelled callbacks still run. Persisted corruption or real-world cancellation frequency is not claimed. The shared helper also serves text/mode/auth/edit operations; correction belongs at the common ownership boundary, not only the two photo call sites.
- **Smallest correction:** Couple completion to actual callback exit, with a pending/running/cancelled-before-dispatch handshake. Keep the limiter-owning operation alive until actual completion (for example, a separately owned/shielded runner task with cancellation-resistant join); moving only `done.set()` into the callback does not preserve the permit. Re-raise cancellation after worker completion and rollback, before resource closure. Cover both registered photo surfaces with direct downstream cancellation, repeated native cancellation, queued cancellation, unchanged AnyIO behavior, no post-close DB access and a replacement borrower blocked until real completion. Retain bounded waits and join all test workers.

### I2 — Important: dynamic merchants have no functioning public H5 chat route

- **Locations:** `catalog/merchant_binding.py:130–138`, `:217–223`; `static/index.html:715–719`; `static/cs/chat.html:110–150`; `frontend/src/api.js:37–43`; `deploy/nginx-merchant.conf:31–40`. Runtime tenant selection is `scripts/run_merchant_runtime.py:49–65`.
- **Trigger:** A supported dynamic merchant opens its authenticated `/merchant/manage/<mid>/` page and uses the customer-chat share action, or tries to open its tenant chat through `/merchant/customer/<mid>/...`.
- **Evidence:** Management JS requests tenant-prefixed `POST /cs/chat-token`, but the management allowlist rejects it. Even if the token is obtained separately, `shareChat` constructs root `/cs/chat/<token>`. The new NGINX sample sends every root `/cs/` request to the fixed trial API at **19010**, whose `_chat_conn` checks its own shop DB (`catalog/api.py:1249–1253`), rather than the dynamic merchant's port/DB from the hub row. Without that sample, the supplied hub root handler likewise checks the hub app's DB; there is no token-to-tenant dispatcher. The customer gateway only accepts GET/PATCH list/link routes; chat pages and POST session/message/photo/mode/actions are absent. Static chat and default uni clients also build root CS URLs, so merely widening the page allowlist would not repair subsequent requests.
- **Independent bounded probe:** `/tmp/test_h5_final_tenant_routing.py`, executed once with the required offline runner from `/tmp`: one temporary real hub SQLite database, a synthetic authorized/running merchant on port19002 with a distinct tenant DB, and actual registered FastAPI gateway routing. Merchant verification was stubbed true solely to reach the authorized allowlist. All socket connect/DNS and subprocess launch operations failed closed; config was replaced before import to prevent any `.env` read. Results: authorized management `POST .../cs/chat-token` **404**; tenant customer `GET .../cs/chat/shop-token` **404**; customer `POST .../session` **405**. **1 passed, 2 known upstream warnings** means the probe asserted the broken chain, not product success. No upstream/network request was made.
- **Impact:** Newly managed tenants can run their API/background workers yet cannot issue or use their public customer chat through the delivered gateway. A normal tenant token sent to fixed19010 is rejected as invalid. No cross-tenant data disclosure is asserted. Existing fixed single-shop `/cs/` use can still work; this finding concerns supported dynamic merchants.
- **Smallest correction:** Add a narrow authenticated chat-link generation route and one coherent tenant-aware public chat route family. Preserve the selected merchant prefix throughout static/uni chat, session/mode, photos, language, list and export URLs; forward multipart bodies with their real content type and bounded size, customer language and appropriate timeout. Alternatively a validated token dispatcher can preserve root URLs, but it must select the correct tenant DB/port for every related capability. Do not expose admin APIs or accept caller-selected upstream hosts. Add two-tenant gateway/browser coverage through the actual route chain, with negative cross-tenant token/capability checks and fixed-single-shop compatibility.

### M1 — Minor, required final correction: multipart count errors bypass customer localization

- **Locations:** `catalog/cs_i18n.py:79–98`, `catalog/userapp.py:292`, `catalog/api.py:1418`.
- **Trigger:** Exceed `max_files=1` or `max_fields=8` on either customer photo endpoint while a non-Chinese language is selected.
- **Evidence:** Task6's preserved real central Arabic HTTP probe returned400 with raw English `Too many files. Maximum number of files is 1.` The current code still registers the FastAPI HTTPException subclass, whereas Starlette multipart parsing raises the framework base class; both count limits still use that parser.
- **Impact:** The new fixed resource error violates the complete 13-language customer-error contract and lacks the intended stable machine code. Upload rejection itself remains effective.
- **Smallest correction:** Handle the appropriate Starlette exception at a safe shared boundary or translate the known parser limits locally. Preserve status, add stable distinct machine codes, and keep merchant administration Chinese. Cover both limits on both routes with Arabic/another selected locale and ensure unrelated HTTP exceptions retain their behavior.

## Concrete strengths and covering evidence

- Short SQLite text planning/replay avoids holding a business writer across model/remote waits; snapshot version validation and cache-only replay are explicit (`catalog/cs_chat.py:113`). Task1 retained file-backed commit/rollback, independent writer/read, replan, bounded conflict and outer/repeated cancellation evidence. I1 is a separate remaining lifetime boundary, not dismissal of those results.
- Supplier/model/spec identity, conservative ambiguity rejection, category-wide stale approval snapshots and explicit no-delist semantics compose coherently. Task2's final78 affected passes at48ff1b9 cover multiline supplier disambiguation and legacy quote supplier fallback. Decimal half-up pricing and requested-versus-carton quantity remain in API/Excel/plugin behavior.
- Import sources and attempts are isolated; partial failures remain visible alongside valid drafts; checkpoint hashes and business fencing protect retries/outbox. Task3's final90 related passes at8c1af2f include real XLSX/PNG boundaries. Added image-field association is covered by `tests/test_partial_import.py:79`; semantic model accuracy is still separate.
- Issued hashed guest capabilities, expiry/end revocation, verified central account merge and owner-scoped batches avoid client-email ownership guesses. Task4A's final124 related plus32 focused and22 client cases, and actual browser error/reload/retry/discard flow, close its prior pending-card, lost-photo, candidate and worksheet defects. Full587 belongs to7142217, not HEAD.
- Canonical 13-language resources, RTL, machine actions, protected display/export projections and literal/cache rejection have meaningful actual workbook/browser coverage. Task5 final111 affected passes and fresh frontend22/session-semantic, H5/mp and prefixed static/compiled browser evidence belong to071bc10. Full614 belongs to9423870. These tests covered list/quote prefixes, not the tenant chat chain in I2.
- Actual parser host/document flock ownership and durable child markers correct the earlier lease-only/per-DB capacity gap. Tenant API/notification/index/sweep roles are supervised together with a parent lifeline. Task6 scoped re-review approved I1–I4 at0c8e9f8 using reported108 affected passes; earlier647 full passes/one real-dataset skip belong tob60fd2d. That evidence does not establish public tenant chat accessibility or live host behavior.

## Deferred and parked-item triage

1. Direct-endpoint cancellation: **open I1**, mandatory correction; previous Task1 outer/AnyIO successes do not close it.
2. Multipart localization: **open M1**, include in the same final wave, not another indefinite deferral.
3. Task4B authenticated history: **required decision pending**. Current central merge is implemented; no shop history integration is inferred. OverallTask4/project remains incomplete until decision, any selected implementation and independent review.
4. Earlier Task1 import timing/unjoined fixture warning and speculative-photo orphan concern: import fixture/worker lifecycle was addressed in Task3; rollback-created-file removal and periodic reference-aware sweep now cover ordinary cleanup. Do not reopen settled failure counts as current failures; I1 still exposes its distinct live-worker cleanup hazard.
5. Task2 actual added-image extraction handoff: **offline association covered** by Task3's real PNG regression; actual model/source image semantics remain live acceptance. Task2 supplier disambiguation and quote fallback are closed by its scoped rereview.
6. Task3 plugin phase/mode mismatch, partial/crash/retry/outbox and malformed/image-only evidence gaps: **closed within offline scope** by Task3 report/rereview. No row-targeted retry or model-correctness claim is made.
7. Task4 prior five Important/two Minor items (including HTTP export coverage and grouped-sheet behavior): **closed by Task4 rereview**, retained browser/route evidence; not exempt from I1/I2 integration corrections.
8. Task5 prior five Important issues (TDZ list rendering, quote/assets gateway, customer locale assets/header, literal/cache acceptance, descriptive projections): **closed by scoped rereview**. Canonical JSON release packaging is included in operational delivery; actual installed-release verification remains open. I2 is the uncovered chat route family, not a reopened list/quote finding.
9. Task6 earlier lease overlap, per-DB capacity bypass, preflight caller and unmanaged tenant workers: **closed within scoped offline evidence** by0c8e9f8. Shared authority configuration, ambiguous orphan manual recovery and target-host capacity remain explicit operational obligations, not silently proven facts.
10. Marker registration, generated artifact location and Node module-type hygiene: **addressed**. The two upstream TestClient/AnyIO deprecations remain **nonblocking maintenance**, observed again in this narrow probe; update compatible dependencies separately without suppression or weakened checks.
11. Current tracked credential removal: recorded in sanitized delivery evidence; **historical purge/rotation separately pending authorized shared-environment work**. No historical secret was viewed or used in this review.
12. Installer verification incident: **preserved safety limitation**, not a harmless dry run or approval rejection. Initial legacy `--render` reached `sudo install`, failed71 because native systemd destination was absent; no subsequent systemctl/installed service observed. Corrected hermetic fail99 stubs/render branch and preflight caller evidence address repeatability; do not claim an actual safe host installation was tested.
13. Missing original18 workbook, authorized working provider configuration (prior401), SSH/key/port/alias (connections closed before authentication), publicURL and explicit own-mailbox email test: **live acceptance pending**, not repository-fix failures and not satisfied by structural shaver30 rows/52 images or fake model/mail. Include actual Docker/bridge/systemd/TLS/phone/native-language and real photo-corpus acceptance here. Formal mini-program release remains separate.

## Ten recorded rulings and their costs

In ledger order, all remain visible in the final handoff:

1. In-memory snapshot plus cached replay: accepted short-writer strategy; costs DB copy/memory and replan consistency complexity, bounded by recorded tests.
2. Under-specified historical pending import tickets require reimport: safe conflict rejection; costs merchants repeating unapproved imports, while approved catalog survives.
3. Durable completion event even after inline result: preserves disconnect recovery; costs possible inline-plus-push presentation, requiring deduplication/truthful status.
4. Retry entire failed sheet/document with successful checkpoints: truthful supported parser granularity; costs repeated model latency rather than pretending targeted-row recovery.
5. Configurable24-hour guest inactivity: no use quota; costs idle users starting blank and server retention until expiry/sweep. Browser close does not prove immediate server deletion.
6. Minimal exact-product merchant quote action: makes output-language choice usable; costs additional admin UI maintenance, without changing pricing policy.
7. Translate export display prose/labels while preserving raw evidence/literals: meets customer-language need; costs provider latency and explicit original-text fallback on failure.
8. Earlier per-DB one-parser default: conservative but insufficient across tenants; its capacity rationale is **superseded/refined by ruling9**, not an independent host guarantee.
9. One actual host-wide execution authority plus document ownership: correct resource boundary; costs cross-merchant queuing, a shared state dependency and possible manual recovery for uncertain child ownership.
10. Managed dynamic tenant background roles: necessary supported lifecycle; costs additional processes/restart coordination and requires live lifecycle acceptance. Public routing remains I2 despite this ruling.

## Review limits and execution record

- No suite/build rerun, installer/service action, provider/model/mail/network call, original `.env`, raw credential history or original unsanitized diff access. No source/index/HEAD/branch mutation or subagents. The only checkout write is this report.
- Read the supplied sanitized package with focused current-code continuity checks; large generated catalogs/test assets are supported by the task parity/behavior evidence, not independently regenerated or linguistically certified. This pass concentrated on the requested cross-task architecture rather than repeating every settled test.
- One new bounded, in-process gateway probe ran as described under I2; DBs were confined to pytest-managed temporary storage. Its script remains at the stated `/tmp` path for reproducibility. No background provider/parser workers were started.
- No aggregate test total is assigned to472bcc1. Revision-specific counts above are preserved reported evidence, not executions by this reviewer. Current direct-cancellation and multipart findings use prior reproduced evidence plus unchanged HEAD mechanism inspection; I2 has new independent HTTP evidence.
