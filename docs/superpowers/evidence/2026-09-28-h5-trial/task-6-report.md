# Task 6 implementation report

Status: implementation and offline verification complete; awaiting controller's independent review. Base HEAD3992239; branch codex/h5-trial-20260928. No subagents. Task4B cross-DB history decision remains pending. No production acceptance is claimed.

## Technical decisions

- `catalog/auth_codes.py` migrates delivery/sent_at/attempts, invalidates pre-upgrade codes, and serializes issue/consume with file SQLite BEGIN IMMEDIATE. Default expiry600s/cooldown60s/attempt5; all configurable. Pending generation invalidates predecessors before delivery. Provider runs outside writer transaction. Failed sends mark failed with cooldown retained; crash-pending expires at TTL. Only latest delivered row verifies; failed attempts commit before HTTP400, successful consume+guest/account merge commit together. No daily guest quota. New random codes avoid reuse of still-unexpired historical code hashes.
- Production requires Resend key+from; no plaintext logging or response-body propagation. Explicit USER_APP_ENV=development AND USER_APP_DEV_EMAIL_LOG=1 opts into local0600 code file; no stdout code log. All13 locale mail subjects/bodies include unchanged code and configured seconds. New errors regenerate both language assets. Existing selected language/verified guest merge preserved.
- Photo boundaries validate actual file format (PNG/JPEG/WEBP/BMP/TIFF),20MiB compressed bytes,40M pixels by default. Streamed multipart body limited to payload+64KiB before spooling, including chunked body; one file/eight fields. Existing photo-worker lifetime/model limiter left intact; known direct-cancellation issue remains reserved for consolidated correction.
- Parser capacity is claimed transactionally BEFORE thread creation using count of active import leases. Default1 per controller's conservative target-host decision; configurable per sharedDB. Queue persists without per-document waiting threads. Completion drains by ID and notification/startup recovery retries after crash,90s lease/30s heartbeat retained. Admission cap is perDB, not host-wide across independent tenantDBs;1 parser can use1GiB/1CPU plus OS/service/bridge/photo headroom, capacity unmeasured. Stale surviving processes remain subject to parser timeout; DB fencing is not a process killer. Optional callbacks are not durable; queued jobs rely on durable existing outbox, consistent with crash recovery. No new notification consumer.
- Explicit CATALOG_AGENT_NETWORK adds only a named Docker network, rejects host/bridge/none, retains sandbox mounts/caps/user/memory/pid/time restrictions. Sample shared compose network dangkou-trial-parser DNS litellm:4000 reaches private bridge; host127.0.0.1:4000 is separate address. Alias trial-parser, actual ANTHROPIC_API_KEY contract kept. Upstream model/base/key required with no inferred replacement/provider/model region.
- New templates are NOT observed server configuration. Installer defaults to render, explicit --install required; adds index worker and perDB guest sweep service/timer (15min, central+shop). Existing merchant TG onboarding retained, dead customer TG service files removed. NGINX TLS example /tool entry/assets/proxy and /cs preserved-prefix routing. All expected env/path keys documented. Preflight reads process env only (no dotenv, child, network or DB writes), checks binary names and route/path consistency; outputs names only.
- Frontend/src/package.json scopes ESM mode; avoids root type:module incompatibility with Vite plugin. Canonical JSON remains a backend packaging requirement. real_agent registered without skip/suppression; two upstream warnings unchanged. Browser audit fixture now honors external path with /tmp default, no historical evidence rewrites in final full run.
- OVERNIGHT-PLAN.md historical line7 replaced silently. No old/current credential, removed/context lines or raw diff emitted. Current marker and1added/1removed stat checked; only whitespace/stat checks for that file. No history rewrite, credential use or rotation. Runbook states authorized owner must rotate exposed credential and coordinate history/reuse cleanup.

## Semantic red/green and exact checks

Runner prefix P=`/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python /tmp/dangkou-trial-offline-pytest.py`, cwd `/Users/a1/workSpace/Eryuan/dangkou-v2/.worktrees/h5-trial-20260928`.

- `P tests/test_userapp_auth_limits.py -q > /tmp/task6-auth-red.txt 2>&1`:6failed (resend old code accepted, attempts absent, cooldown absent, concurrent writer/delivery behavior, production logging fallback, photo bounds). After implementation same command to `/tmp/task6-auth-green.txt`:6passed2warnings.
- `P tests/test_import_admission.py -q > /tmp/task6-admission-red.txt 2>&1`:1failed, distinct accepted documents launched workers while cap1 required queue. Green target plus existing lease/recovery tests exercised real separate connections, fake parser, queued drain; added separate Python process claims realSQLite lease, parent cannot take occupied slot, forced expiry recovers both queued jobs.
- `P tests/test_deploy_preflight.py -q > /tmp/task6-deploy-red.txt 2>&1`:3failed1passed. Missing mail/route checks, missing render-only installer, missing bridge files. **Installer boundary mistake is detailed below and must remain in record.** Green preflight/template/render tests:4passed; latest `/tmp/task6-hermetic-green.txt`4passed0warnings.
- `P tests/test_import_admission.py tests/test_import_recovery.py tests/test_userapp_auth_limits.py tests/test_userapp.py tests/test_guest_sessions.py tests/test_agent_isolation.py -q > /tmp/task6-targeted.txt 2>&1`:37passed2warnings.
- `P tests/test_userapp_auth_limits.py tests/test_agent_isolation.py -q > /tmp/task6-bounds.txt 2>&1`:11passed2warnings, including real chunked HTTP byte limit and all13 email/error locales with fake requests.post. Parser command test asserts named network plus original isolation constraints.
- `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task6-audit P -q > /tmp/task6-full-initial.txt 2>&1`:640passed,4failed,3skipped,2warnings,50.60s. Failures: test_current_guest_language_merges_and_account_restores_on_another_device directly inserted old OTP missing delivered state; test_userapp_failed_photo_is_not_committed_by_later_request and two userapp HTTP concurrency tests supplied non-image b'photo'. Corrected fixtures to real generated JPEG, explicit delivered OTP state, and explicit development sender on rollback test. Preserved assertions, no skips removed or failures hidden.
- `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task6-audit P tests/test_import_admission.py tests/test_http_concurrency.py tests/test_h5_transactions.py tests/test_customer_languages.py -q > /tmp/task6-fixtures-green.txt 2>&1`:41passed2warnings2.41s.
- Fresh frontend copy: `/var/folders/z_/9jwqqszn5c99z0dvq7bydy0m0000gn/T/dangkou-task6-fresh-y8adevjn/frontend`. Source copied excluding node_modules/dist, symlink only to previously isolated Task5 node_modules; lock bytes equal Task5 lock SHA256`35f25cc4d4a6c31b1e827fb6fad7a2109cc3718cbd945b0a30d0564761ef2dc8`. Fresh static copy also included (session tests execute real static pages). No node_modules/dist in worktree.
- Initial `npm --prefix "$build_dir" test` failed11 session cases because static sibling wasn't copied; both initial builds failed `uni is not a function` under root type:module. Logs `/tmp/task6-npm.txt`, `/tmp/task6-h5.txt`, `/tmp/task6-mp.txt` preserved. Corrected build staging to copy static and scoped ESM mode to src; no application semantics weakened.
- `npm --prefix "$build_dir" test > /tmp/task6-npm-final.txt 2>&1`:exit0, smoke all pass,22 session tests pass, language/quote/raw-edit behavior tests pass, no MODULE_TYPELESS_PACKAGE_JSON warning. `npm --prefix "$build_dir" run build:h5 > /tmp/task6-h5-final.txt 2>&1` and `... run build:mp-weixin > /tmp/task6-mp-final.txt 2>&1`:both exit0 DONE Build complete.
- `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task6-final-audit DANGKOU_TEST_H5_DIST="$build_dir/dist/build/h5" P -q -rs > /tmp/task6-full-final.txt 2>&1`:647passed,1skipped,2warnings,48.76s. Sole skip audit/test_round4_pages.py:18 requires unavailable real customer photograph dataset. The prior2 compiled-H5 skips are now executed. Remaining warnings: StarletteTestClient httpx deprecation and anyio.abc.BlockingPortal alias deprecation; not suppressed.
- Final focused after render PATH hardening and unused constant removal: `P tests/test_userapp_auth_limits.py tests/test_deploy_preflight.py tests/test_import_admission.py -q > /tmp/task6-final-focused.txt 2>&1`:14passed2warnings.
- `bash -n deploy/wechat-test-services.sh deploy/deploy.sh`:exit0. `.../.venv/bin/python -m py_compile scripts/preflight.py scripts/rebuild_search_index.py catalog/auth_codes.py catalog/upload_limits.py catalog/userapp.py`:exit0. `git diff --check`:exit0. `git diff --numstat -- OVERNIGHT-PLAN.md`:1added1removed, no content emitted.
- `.../.venv/bin/python scripts/preflight.py --dry-run > /tmp/task6-preflight.txt 2>&1`:expected exit2,17missing envnames; environment intentionally unconfigured. Docker/node/soffice present. No original.env read. Docker Compose v5.5.0 `docker compose --env-file <temporary-placeholder.env> -f deploy/compose.litellm.yaml config --quiet`:exit0. YAML loaded with installed PyYAML tests.
- Official image `docker pull nginx:1.28.0-alpine` authorized isolated-validation scope; `/tmp/task6-nginx-pull.txt`, digestsha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284. Temporary one-day self-signed cert generated with openssl; config sample wrapped in events/http, certpaths rewritten only in temporary wrapper. `docker run --rm --network none --read-only --cap-drop=ALL --security-opt=no-new-privileges --tmpfs /var/cache/nginx --tmpfs /var/run --mount type=bind,source=<temporary-wrapper-directory>,target=/check,readonly --entrypoint nginx nginx:1.28.0-alpine -t -c /check/nginx.conf`:initial exit1, syntax OK but cache chown failed without capabilities (`/tmp/task6-nginx-validation.txt`). Wrapper explicitly `user root;` aligns temp cache ownership (no capabilities added); same isolated command exit0 `syntax is ok`/`test is successful` in `/tmp/task6-nginx-validation-final.txt`. No server started, ports published or hostconfig used. One attempted helper looked under /tmp instead of macOS tempfile directory and failed empty glob before container invocation; corrected to tempfile.gettempdir(). Native nginx/systemd-analyze/litellm CLIs unavailable, so no native systemd or LiteLLM startup claimed.

## Installer test boundary incident (preserved)

Before implementing argument handling, new test invoked `subprocess.run(['bash', '<worktree>/deploy/wechat-test-services.sh', '--render', '<pytest tmp_path>'], capture_output=True, text=True)`. Old script ignored --render and attempted its first `sudo install -m 644 /tmp/dangkou-wechat-test-api.service /etc/systemd/system/dangkou-wechat-test-api.service`. It failed exit71 with exact stderr `install: /etc/systemd/system/INS@X8TPrm: No such file or directory`. Script set-e exited there; no daemon-reload/enable step executed. This was an unauthorized attempted installer path, not a valid dry run. Reported immediately to controller; first-failure log retained `/tmp/task6-deploy-red.txt`.

Read-only inventory (also independently confirmed by controller): `/tmp/dangkou-wechat-test-api.service` exists467bytes and is preserved; `/etc/systemd`, `/etc/systemd/system`, intended destination `/etc/systemd/system/dangkou-wechat-test-api.service` and failed temporary destination `/etc/systemd/system/INS@X8TPrm` all absent. No installed host unit or production mutation occurred. No cleanup of unrelated files, sudo retries or service operations.

Correction: script now explicitly handles --render and defaults to render; only an explicit --install branch can call host tools. Test uses `/bin/bash` with hermetic PATH containing executable fail99 sudo/systemctl/install stubs plus only mkdir/cat/sed/dirname/mktemp symlinks. Render output stays in pytest tempdir. Latest hermetic render test passes. The initial mistake is not erased by green checks.

## Live limitations / remaining actions

Actual SSH alias/port/key not supplied; prior connections closed before authentication, not proof of closedport. Intended model configuration previously401; valid authorized model config missing and no alternate provider used. Original18-product Excel missing; existing shaver structural evidence does not validate model accuracy or600s. No real email sent; require configured sender/domain and explicit own-mailbox instruction. No production files/services/databases changed, no live models/messages, no push. Bridge actual DNS/auth/Anthropic compatibility, native systemd checks, live TLS routing remain acceptance gaps. Known photo cancellation issue reserved for controller's consolidated correction. Account central/shop history remains a required unanswered product choice.

## Commit and changed files

Commit filled after staging deliberate implementation files only. Generated audit artifacts, dependencies, temp config/cert/build files and this ignored SDD report are excluded. Controller must sanitize the entire OVERNIGHT-PLAN.md diffsection before review; never expose rawdiff.

Changed deliberate files:
- `.env.example`
- `OVERNIGHT-PLAN.md`
- `audit/test_browser_round2.py`
- `catalog/agent.py`
- `catalog/api.py`
- `catalog/cs_i18n.py`
- `catalog/ingest.py`
- `catalog/userapp.py`
- `deploy/dangkou-cs-bot.service`
- `deploy/dangkou-cs-test-bot.service`
- `deploy/dangkou-merchant-hub.service`
- `deploy/nginx-merchant.conf`
- `deploy/wechat-test-services.sh`
- `frontend/README-frontend.md`
- `frontend/src/customer-catalog.js`
- `frontend/src/customer-languages.json`
- `scripts/preflight.py`
- `static/customer-catalog.js`
- `tests/test_agent_isolation.py`
- `tests/test_customer_languages.py`
- `tests/test_h5_transactions.py`
- `tests/test_http_concurrency.py`
- `tests/test_userapp.py`
- `catalog/auth_codes.py`
- `catalog/upload_limits.py`
- `deploy/compose.litellm.yaml`
- `deploy/dangkou-wechat-test-litellm.service`
- `deploy/litellm.yaml`
- `docs/superpowers/2026-09-28-trial-operations.md`
- `frontend/src/package.json`
- `pytest.ini`
- `tests/test_deploy_preflight.py`
- `tests/test_import_admission.py`
- `tests/test_userapp_auth_limits.py`

Committed `b60fd2d` (`feat: harden trial auth resources and operational delivery`):34files,793insertions,177deletions. Postcommit `git status --short` empty; cached whitespace check passed. No push.

# Fix round 1 (I1–I4), base b60fd2d

Status: addressed blocking review findings; targeted and affected covering verification passed. M1 multipart localization remains explicitly deferred to the final correction wave; M2 dependency warnings remain. No Task4B history decision or unrelated feature work. No subagents, live provider/mail/SSH traffic, host installer/service mutation, original.env access, push or secret/history inspection.

- I1/I2: new `catalog/parser_execution.py` is a shared host authority, required absolute `CATALOG_PARSER_STATE_DIR` (same sample path for ALL services/tenant environments). Default actual capacity1; its persistent capacity.json rejects process configuration disagreement. flock slot plus document execution lock acquired before business claim/thread dispatch, held through actual work independent of lease timestamps, passed into subprocesses. Child still running after parent exit retains inherited locks. Missing configuration fails closed; all tenant provisioning tests assert the host directory is inherited unchanged despite distinct DB paths. Raising capacity requires measured host headroom, stopped/quiescent workers and coordinated capacity.json/all-env change; never unlink live lock files.
- Each Docker launch first fsyncs durable unique name/execution-label marker. Reconciliation queries daemon, verifies exact name+label, removes that specific identity and confirms absence before reuse. Unknown daemon state, foreign identity, failed removal, and crash-time absent-container/dispatch ambiguity fail closed. Only successful original CLI completion plus confirmed daemon absence may clear an absent marker; PID/CLI disappearance alone is insufficient. Both same-document orphan markers and inherited execution locks prevent moving the document to another slot at capacity2. Uncertain conversion markers also block reuse; operator must verify actual quiescence before clearing them. This conservative behavior can require authorized intervention after ambiguous dispatch or supervisor loss, rather than risking concurrent work. No live cleanup invoked.
- Business tenant lease/outbox/checkpoint fencing retained. Old recovery test now explicitly injects an independent committed business winner while the original parser remains alive; its late result cannot overwrite winning stats/ticket or duplicate outbox. It no longer starts an extra real parser merely to test business fencing. Temporary authority fixture isolates every test while child processes inherit the same directory. Direct parser calls acquire the same host boundary.
- I3: explicit `scripts/preflight.py --env-file PATH` safe dotenv data parser; no shell expansion/eval/secret printing, comments/quotes/export supported. Default remains env-only read-only. `deploy/deploy.sh` now supplies `--env-file .env`. Test captures both SSH heredocs through hermetic SSH/rsync stubs, never executes remote payloads, and verifies literal command substitution stays data. Host-mutating tool stubs fail99. No legacy deployment step was invoked against real tools.
- I4: tenant launch now manages API+notifications/import recovery+index+sweep as one group. DB runtime ownership flock is inherited by children, preventing overlap; notify/index keep existing DB-specific locks and sweep adds its own. Parent-owned write pipe gives each same-process role wrapper a EOF lifeline on supervisor death (SIGTERM then8s forced process exit fallback). Partial spawn failure stops started roles; any role failure triggers whole-group stop/retry using existing30s policy. Parent/group shutdown closes lifeline and reaps children. Real sweep test covers expired row cleanup; fake-role supervisor test exercises failure, entire-group shutdown, restart, final shutdown. Duplicate group lock test verifies reacquisition only after stop. Managed tenants no longer depend on manual per-DB background-service installation.

## Fix-round red/green and actual commands

Same cwd and runner P as above.

1. `P tests/test_import_admission.py -q > /tmp/task6-fix1-admission-red.txt 2>&1`:2failed2passed (surviving worker after lease expiry; two DBs admitted simultaneously). After host authority integration: `/tmp/task6-fix1-admission-green.txt`:4passed0.33s. Added capacity2 same-document coverage afterward.
2. `P tests/test_merchant_runtime_workers.py tests/test_deploy_preflight.py -q > /tmp/task6-fix1-runtime-red.txt 2>&1`:4failed4passed3.37s (managed roles/parent lifeline/sweep/safe loader missing). After implementation, `/tmp/task6-fix1-runtime-green.txt`:8passed1.27s.
3. `P tests/test_parser_execution.py tests/test_agent_isolation.py tests/test_import_recovery.py -q > /tmp/task6-fix1-child-initial.txt 2>&1`:2failed14passed6.63s. Old fake Docker returned parser exit9 for every daemon query, so new conservative cleanup reported uncertain termination; updated the external fake-state seam to explicitly confirm terminated container. Old fencing test expected a second live parser; rewritten with explicit committed business winner as described above, preserving winning ticket/single-outbox assertions.
4. `P tests/test_parser_execution.py tests/test_import_admission.py tests/test_import_recovery.py tests/test_agent_isolation.py tests/test_deploy_preflight.py tests/test_merchant_runtime_workers.py -q > /tmp/task6-fix1-focused.txt 2>&1`:31passed1.48s. Includes real supervisor os._exit with surviving child/inherited flock; no provider child, only a bounded local Python process waiting for a temporary release file. All test-created children exited/reaped or released before teardown.
5. `DANGKOU_AUDIT_ARTIFACT_DIR=/tmp/dangkou-task6-fix1-audit P tests/test_parser_execution.py tests/test_import_admission.py tests/test_import_recovery.py tests/test_agent_isolation.py tests/test_deploy_preflight.py tests/test_merchant_runtime_workers.py tests/test_agent.py tests/test_agent_import.py tests/test_partial_import.py tests/test_merchant_onboarding.py tests/test_shop_and_worker.py tests/test_search.py tests/test_search_api.py tests/test_guest_sessions.py tests/e2e/test_merchant_runtime.py -q > /tmp/task6-fix1-covering.txt 2>&1`:107passed1failed2warnings12.86s. Existing executable fake Docker emitted parser-result JSON for `ps`, so cleanup safely refused it. Fake script now explicitly returns successful empty container inventory for `ps`; production behavior was not relaxed.
6. Same covering command redirected to `/tmp/task6-fix1-covering-final.txt`:**108passed,2warnings,14.14s**, exit0. Covers notification loop, embedding retry/recovery, managed tenant environment and E2E runtime routing, import/checkpoint/fencing/resource behavior, real sweep, safe caller. No full suite/frontend rebuild repeated because this fix changes backend/operational ownership only; prior whole-delivery evidence remains above.
7. `git diff --check`:exit0. `bash -n deploy/deploy.sh`:exit0. `/Users/a1/workSpace/Eryuan/dangkou-v2/.venv/bin/python -m py_compile catalog/parser_execution.py catalog/agent.py catalog/ingest.py scripts/preflight.py scripts/run_merchant_runtime.py scripts/run_managed_child.py scripts/run_guest_sweep.py`:exit0. Self-review examined these current sources and bounded diffs; no OVERNIGHT-PLAN content/diff/history was read or emitted this round.

The prior installer incident and failed logs above remain preserved. New runtime container/process behavior is offline-tested with explicit Docker/network/role seams; actual target host Docker/locks/filesystem/systemd/mail/model configuration remains unaccepted. Operator must use one persistent host authority directory and consistent limit across all services; unknown orphan state blocks work instead of making a capacity promise it cannot establish.

Fix-round commit: `0c8e9f8` (`fix: own parser execution across tenants and manage tenant workers`),19files635insertions33deletions. Postcommit worktree clean; no push. Ready for independent re-review.
