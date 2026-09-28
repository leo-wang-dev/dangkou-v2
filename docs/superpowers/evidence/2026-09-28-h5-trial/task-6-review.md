# Task 6 review — 3992239..b60fd2d

## Spec compliance

**❌ Needs fixes.** The task provides substantial OTP, upload, queue and operational coverage, but it does not bound actual parser work across lease expiry or across merchant databases, and the changed preflight contract breaks the existing deployment caller. New multipart-limit errors also escape the selected-language handling.

**⚠️ Not a live acceptance verdict.** SSH/key/port, authorized working model credentials, original 18-product workbook, actual model accuracy/600-second performance, authorized own-mailbox delivery, native systemd, bridge startup/DNS/auth, TLS and device behavior remain unverified. Task 4B central/shop history remains a pending product decision. The independently confirmed direct-photo cancellation defect remains reserved for the consolidated correction; it is not counted as a new Task 6 finding.

## Strengths

- `catalog/auth_codes.py:25–48` reserves a generation and invalidates predecessors in a short writer transaction, releases that transaction before mail delivery, and only activates a still-pending generation after success. Failure marks the new generation failed without returning provider bodies or codes. `catalog/userapp.py:67–91` requires configured production mail and explicit development logging opt-in.
- `catalog/auth_codes.py:51–63` commits wrong-attempt bookkeeping before returning HTTP400, while leaving successful consumption open. The existing `catalog/userapp.py:137–184` request transaction and `:430–448` verified guest/account merge then commit or roll back that success together. No normal guest usage quota was added.
- `catalog/ingest.py:49–97` claims capacity before a worker thread is created; queue draining avoids one waiting thread per accepted document. The existing fenced final transaction at `:210–224` retains atomic ticket/outbox outcomes. These are useful improvements despite the actual-work bounds below.
- `catalog/api.py:485`, `:1418`, and `catalog/userapp.py:292` bound multipart file/field counts; bounded reads and actual image validation occur before model work. `catalog/upload_limits.py:10–24` also bounds chunked multipart bodies before unlimited spooling. The image middleware does not change the existing JSON/path-based `/import` contract (`catalog/api.py:496–550`).
- `deploy/wechat-test-services.sh:3–11,64–70` defaults to rendering and makes installation explicit. The final test uses fail stubs for host-mutating tools (`tests/test_deploy_preflight.py:29–42`). Index and central/configured-shop guest-sweep units are present. Merchant TG onboarding remains; obsolete customer TG units are removed.
- `catalog/agent.py:144–149`, `deploy/compose.litellm.yaml:7–19`, and the runbook distinguish container DNS from host localhost and preserve parser isolation. `scripts/preflight.py:47–48` checks canonical language JSON packaging. The 13-language canonical additions preserve `{code}` and `{seconds}` placeholders. Marker registration and the external audit-artifact default address repository hygiene without blanket skips or warning suppression.

## Issues

### Critical

None found in this task-scoped review.

### Important

**I1 — An expired lease admits replacement work while the original parser is still running.**

- **Location:** `catalog/ingest.py:49–64` (`_claim`), `:140–147` (`recover`); related worker/timeout interfaces at `:159–169` and `catalog/agent.py:150–158`.
- **Evidence:** Capacity counts only `lease_until > now`; a stale owner is immediately replaceable. A bounded fake-parser probe set capacity to1, held the first parser alive, expired its lease, and called recovery. Result: `simultaneous_fake_parser_calls=2`, `attempts=2`. Both workers were joined afterward. The existing new cross-process test covers an exited claimant, not a surviving parser. Heartbeat failure/loss does not cancel the parser; final commit fencing occurs only after parsing. Moreover, the Docker timeout/cleanup resides in the supervising Python `subprocess.run`; it is not an independent container deadline if that supervisor dies.
- **Impact:** The promised default one-active-parser bound can be exceeded during recovery, exactly when the host may be unhealthy. Stale commit fencing prevents duplicate business outcomes but does not protect memory/CPU. A runbook disclaimer does not fulfill actual-work admission.
- **Fix:** Track actual execution ownership and retain a slot until termination is confirmed, or enforce a supervisor/container lifetime that reliably terminates stale work before reclaim. Fail closed during uncertain ownership. Add the surviving-worker recovery regression using fake provider/child seams and retain existing fencing/outbox tests.

**I2 — Capacity is isolated per merchant DB, so the deployment can run several default-cap parsers on the same host.**

- **Location:** `catalog/ingest.py:54–55`; `.env.example:72` (`CATALOG_IMPORT_WORKERS=1`); concrete caller boundary `scripts/run_merchant_runtime.py:26,56–61,119`.
- **Evidence:** The admission query reads only the current connection's `import_doc`. Merchant runtime provisions distinct `catalog.db` paths, copies the same environment into each child, and can run five active merchants by default. Each independent database can therefore acquire its own capacity1 lease. The runbook (`docs/superpowers/2026-09-28-trial-operations.md:29`) acknowledges this but supplies no shared admission mechanism.
- **Impact:** The controller's conservative default one active parser can become five or more 1GiB parser containers, plus the explicitly required service/image/bridge headroom, without any host measurement or operator raising the parser setting.
- **Fix:** Use a shared host-level admission authority/worker queue for all merchant databases on the host, with explicit shared configuration and crash-safe execution ownership. Keep document leases in their tenant DBs for fencing. Add a two-database concurrent-admission test; raising capacity should be a measured explicit host choice.

**I3 — The new env-only preflight breaks the existing deployment script.**

- **Location:** `scripts/preflight.py:10–22`; unchanged concrete caller `deploy/deploy.sh:19–23`.
- **Evidence:** The old preflight imported `catalog.config`, which loaded the checkout `.env`. The new implementation intentionally never loads it and requires process variables. `deploy/deploy.sh` merely checks that `.env` exists, then invokes `.venv/bin/python scripts/preflight.py` in the remote shell without loading/exporting its entries. Its later generated `EnvironmentFile` applies only to services, not this earlier shell invocation.
- **Impact:** The documented normal `DEPLOY_HOST=... bash deploy/deploy.sh` path exits under `set -e` before service installation even when the server `.env` is valid, unless unrelated ambient SSH environment happens to contain all required values.
- **Fix:** Update this caller to pass the configured environment through a deliberate safe loader/wrapper before env-only preflight, without printing secrets or executing arbitrary dotenv text. Keep standalone preflight read-only. Test the caller contract with a temporary placeholder file and hermetic SSH/install/systemctl/child seams; do not run the installer against host tools.

### Minor

**M1 — Multipart file/field-limit failures return untranslated framework messages.**

- **Location:** `catalog/userapp.py:292`, `catalog/api.py:1418`; exception registration at `catalog/cs_i18n.py:79–98`.
- **Evidence:** An in-process HTTP probe sent two files to `/photo` with `X-Customer-Language: ar`. The response was400 with `{"detail":"Too many files. Maximum number of files is 1."}`. Starlette multipart parsing raises its own HTTPException; the custom language handler registers FastAPI's subclass only. The same new `max_fields=8` boundary has this mismatch.
- **Impact:** These new fixed customer-facing resource errors do not meet the 13-language UI/error contract, despite the runbook's localized-resource-error claim.
- **Fix:** Translate known parser-limit failures at the endpoint/shared parser boundary, or register and map the appropriate Starlette exception safely, preserving status and a stable machine code. Cover both limits on central and CS entrypoints with a non-English selected locale.

**M2 — Existing dependency warnings remain a validation limitation, not a repository marker failure.**

- **Location:** Reported TestClient imports (for example `tests/test_userapp_auth_limits.py:7`) and existing Starlette/AnyIO integration.
- **Evidence/impact:** The implementation report records two upstream deprecations; the focused HTTP probe likewise emitted the Starlette/httpx deprecation. `pytest.ini:1–3` correctly registers `real_agent`; neither warning was suppressed. These do not invalidate the new behavioral evidence, but output is not warning-free.
- **Fix:** Track a compatible upstream dependency update separately and verify compatibility when undertaken; do not hide warnings or weaken coverage.

## Explicit verification and scope limits

- Reviewed only `.superpowers/sdd/2026-09-28-h5-trial/review-3992239..b60fd2d.sanitized.diff` as the Task6 change package. The large generated catalog lines truncated one display; the truncated deployment/docs/canonical-JSON portion was recovered with generated monoline payloads omitted. Canonical additions were reviewed; generated-asset parity is reported test evidence rather than independently regenerated here.
- No raw diff/history/credential content, original `.env`, git mutation, source edit, installer, service operation, live email/model/network call or full suite/build rerun was performed. The only checkout write is this report.
- Focused unchanged/truncated-function inspections: `ingest._run` and `agent._run_container` for stale execution versus leases; userapp request transaction/identity/verify tail for failed attempts and successful merge rollback; API `_do_import` and form call sites for actual upload entrypoints; `scripts/run_merchant_runtime.py` for DB scope; notifications/index/main recovery entrypoints and guest CLI for service lifecycle; `deploy/deploy.sh` for preflight/environment compatibility; `cs_i18n.install_errors` for multipart exception localization. No whole-branch crawl was used.
- Ran only two bounded probes: temporary SQLite/fake-parser surviving-lease recovery, and temporary SQLite/in-process multipart localization. Subprocess execution was replaced with a fail stub; the second probe used a model object that fails on access. No parser/container or real provider ran; temporary artifacts were removed and background import workers joined.
- Reported 647 passes/1 real-dataset skip/2 dependency warnings, final14 focused passes, frontend tests/builds, Compose config and isolated `nginx -t` are implementer evidence, not independent reruns. Native systemd/LiteLLM/runtime acceptance remains open.
- The trial renderer schedules one configured catalog DB plus central DB. The retained merchant runtime launches only each tenant API (`scripts/run_merchant_runtime.py:79–94`), and the runbook instructs adding tenant sweeps manually. Deployment of notification polling, index repair and periodic sweep for every actual tenant DB is therefore **not established** by this single-DB template. The controller must verify the intended tenant deployment inventory; no live inventory was available or inferred.
- The earlier installer-test incident remains preserved: the red test invoked unsupported `--render` on the old installer; its first `sudo install` failed before any `systemctl`. The worker/controller recorded that `/etc/systemd`, `/etc/systemd/system`, the intended service destination and failed temporary destination were absent. I did not repeat that operation or treat its later corrected green test as erasing the incident.
- The historical credential section is intentionally redacted from this package. Current removal/count evidence is controller/worker evidence; rotation/reuse review and any shared-history coordination remain explicit authorized-owner actions.

## Assessment

**Task quality: Needs fixes.** OTP transactions, request validation, offline seams and operational documentation are materially improved. I1–I3 block approval because resource admission does not bound actual host work and the deployment caller no longer satisfies its preflight contract; M1 should be included in the consolidated correction.
