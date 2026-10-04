# COSMOS (pipeline web app) — API contract v1.4 (2026-07-16)

v1.2 addendum (COSMOS access moderation via Telegram):
- The app is named COSMOS (Crew Optimization System for Multi-horizon
  Operational Scheduling); FastAPI title updated, UI/tour/login rebranded.
- Access mode: a visitor whose OTP-verified email is NOT in the catalog
  is registered in the access_requests table (users.db; one row per
  email, idempotent) and the admin is notified ONCE via the Telegram bot
  (webapp/telegram_bot.py, long polling; enabled only when
  CREW_TG_BOT_TOKEN + CREW_TG_CHAT_ID are set). Inline buttons:
  authorize (users.add + approval email), deny (denial email),
  request info (ForceReply question -> email to the visitor; decision
  buttons repeated after sending).
- /unauthorized.html now resolves the visitor's Access JWT and shows the
  request status (pending / info_requested / denied, RU+EN); a signed-in
  user is redirected to /. API 403 responses carry "request_status".
- Outbound mail: webapp/mailer.py (SMTP; enabled when CREW_SMTP_USER +
  CREW_SMTP_PASS are set; CREW_SMTP_HOST default smtp.gmail.com:587).
  Both bot and mailer degrade to no-ops without their env config.

v1.2 changes (onboarding + full-UI i18n):
- GET /api/me now also returns "tour_seen": true|false|null (null = the
  identity has no catalog row — token mode's shared operator).
  POST /api/me/tour-seen marks the intro tour seen for the caller
  ({"ok": true, "tour_seen": true|null}); tour_seen_at column added to
  users.db (migrated in place on first connect).
- Read-only onboarding demos, available to ANY authenticated user (a
  fresh user's workspace is empty — demos must still work):
  GET /api/demo/pipeline    — replay of the showcase month (gerad-1)
    from canonical artifacts: instance, input samples, validation,
    stage1, gate, stage2, roster preview, artifacts. 503 if the
    showcase payload is missing on disk.
  GET /api/demo/comparison  — per-month stage-1 + rostering numbers vs
    published GENCOL references, SPP (29 benchmarks) summary computed
    live from orlib CSVs, COR@L scale block, reference_note.
- note_code companions for translatable server notes: POST /api/jobs on
  seed data adds "note_code": "seed_overwrite" next to "note";
  GET /api/airline-config adds "note_code": "rules_env". Clients render
  known codes in the UI language and fall back to "note" verbatim.
- Frontend is fully bilingual (RU default / EN): dictionary in
  webapp/static/i18n.js (t()/tf(), data-i18n attributes, "crew:lang"
  re-render event), language persisted in localStorage "crewLang" and
  shared with login.html; /unauthorized.html statically bilingual.
  Intro tour (onboarding.js) auto-starts once per user (server flag,
  localStorage fallback); demo tab (demo.js) hosts the two demos.

v1.1 changes (multi-airline tier 1):
- AUTH: everything except /login, /api/login and style.css requires a
  session cookie (single operator token, sha256-hashed in
  webapp/config/auth.json, printed to console on first start).
  POST /api/login {"token"} -> HttpOnly cookie; POST /api/logout revokes.
  401 for /api/* without a session; pages redirect to /login.
- Rules ARE applied: every job subprocess gets CREW_RULES_CONFIG pointing
  at the config that applies TO THAT INSTANCE (webapp/config_scope.py
  resolves defaults -> installation template -> instance -> scenario, most
  specific EXISTING file wins); engines and validators read it via
  rules_config.py / RulesConfig.kt (defaults == historical values); the
  problem fingerprint includes the rule set.
  GET /api/airline-config returns "applied": true + per-field defaults.
- SSIM import: POST /api/import/ssim {"name", "ssim_text", "date_from"?,
  "date_to"?, "bases": [{"airport", "count"}]} -> runs _ssim_to_payload.py,
  saves data/uploads/<slug>.json + <slug>.conversion_check.json, returns
  {"id", "conversion_check"}; adapter failures -> 422 verbatim.
- Meridian import (deterministic, model-free): POST /api/import/meridian
  {"name", "meridian_json", "solver_json"?, "date_from"?, "date_to"?,
  "no_fill"?: bool, "keep_structure"?: bool} -> runs
  _meridian_to_payload.py (optional solver export = aircraft-type/geo/
  UTC-guard reference), same save/return contract as SSIM; raw zone keeps
  BOTH source files; adapter failures and timeouts -> 422 verbatim;
  request body over CREW_MAX_IMPORT_BODY_MB (default 256 MB) -> 413.
  keep_structure=true ALSO emits the airline structure catalog
  final-report/pairing_base_catalog_<label>.json. Each unit carries
  "kind": "chain"|"fill" (chain = a whole trip of the source structure,
  fill = an adapter completion) and, for chains, "sourceLegsMissing":
  int — how many legs of its source trip are absent from the unit.
  "fns" is the whole trip (positioning flights included), "active_fns"
  the operating legs only, "duty_fns" the duty boundaries. Freedom
  levels 1 and 2 of the pairing builder REQUIRE this catalog.
- stage2_available is now catalog-based for ANY instance (best/ or
  canonical selected dump for the label); Instance carries catalog_path.
  Non-numeric instances run the generic stage-2:
  _gerad_stage2_roster.py --payload <p> --catalog <c> --label <slug>.
  Journal rows for generic runs carry {"label": slug, "inst": null};
  artifacts are named by label (gerad_stage2_assignment_<label>.json).
- /api/results/{id}: stage2/validators/roster resolved by label for
  non-numeric instances (trajectory report comes from the certificate
  embedded in the assignment artifact); label-named artifacts included.

Purpose: web UI for the pairing-rostering pipeline: data input (canonical
payload, layer 1) -> stage runs (column generation, completion, rostering,
validators) -> results (gate verdict, tables, exports).

Serving: FastAPI app `webapp.app:app`, port 8090
(`.venv/Scripts/python.exe -m uvicorn webapp.app:app --port 8090`).
Static SPA at `/` from `webapp/static/`. All API under `/api`.
The app NEVER modifies pipeline scripts; it orchestrates them as
subprocesses with cwd = repo root and the venv python.

## Instances

`GET /api/instances` -> `[Instance]`
Instance = {
  "id": str,                  // "gerad-1".."gerad-7" | "bts-day" | "upload-<user>--<slug>" (legacy: "upload-<slug>")
  "label": str,               // colgen label: "inst1".."inst7" | "bts-day" | "<user>--<slug>"
  "source": "gerad"|"bts"|"upload",
  "payload_path": str,        // repo-relative
  "legs": int, "employees": int, "slots": int,
  "has_best_catalog": bool,   // final-report/best/gerad_best_catalog_<label>.json
  "catalog_objective": int|null,
  "stage2_available": bool,   // true only for gerad-1..7 in v1 (runner limitation)
  "last_gate_status": str|null, // from final-report/gerad_phase2_two_stage.json
  "rules_origin": "defaults"|"installation"|"instance"|"scenario"
}
Registry sources: data/gerad/payloads/inst{1..7}_p2.json;
data/bts_schedules/DL_ATL_2024-08-15.json (id bts-day, stage2_available=false);
data/uploads/*.json (validated canonical payloads).

`GET /api/instances/{id}` -> Instance + {"validation": {...}} (re-validated
against the schema on read).

`POST /api/instances/upload` body: {"name": str, "payload": object}
-> 200 {"id", "validation": {"ok": true, "stats": {...}}} and saves to
data/uploads/<slug>.json;  or 422 {"validation": {"ok": false, "errors":
[str, ...]}} (schema errors + referential checks: flight airports exist,
assignment flights exist, employee homeAirport exists; report up to 20).
Nothing is saved on validation failure. Slug: [a-z0-9-], derived from name,
must not collide with existing ids.

`GET /api/schema` -> the JSON Schema (webapp/schema/canonical_payload.schema.json).

## Airline rule config (tier-1 parameters; v1 = stored + displayed)

`GET /api/airline-config` -> {"parameters": {...}, "applied": false,
  "note": str}
`PUT /api/airline-config` body {"parameters": {...}} -> saved to
webapp/config/airline_default.json. Parameters (numbers, minutes unless
noted): min_sit_min=30, max_landings_per_duty=5, max_duty_span_min=840,
min_rest_min=600, max_monthly_block_min=5100, max_pairing_span_min=5760.
This surface is the INSTALLATION TEMPLATE — defaults for NEW instances
(P1-10, 2026-07-26). Editing it does NOT change instances that already
exist: their values were copied into them at creation.

## Per-instance rules and rates (P1-10)

`GET /api/instances/{id}/rules` -> {"instance", "label", "domain":
  "rules", "origin": "defaults"|"installation"|"instance"|"scenario",
  "path", "values": {...}, "installation_values": {...}, "overridden":
  [key], "defaults": {...}, "enforcement": {key: "solver"|"certificate"}}
`PUT /api/instances/{id}/rules` body {"parameters": {...}} — partial edit
  layered over THIS INSTANCE's effective values (an unmentioned parameter
  never silently falls back to a built-in). Validated by
  rules_config.validate_value — the same semantics as the loader and every
  other writer. 409 for a scenario instance (its own, more specific level
  governs; writing here would be silently ineffective).
`DELETE /api/instances/{id}/rules` — rewrite the instance's rules FROM
  today's installation template (a reset, not a live link).

`GET /api/instances/{id}/rates` -> {"instance", "label", "cost": {...},
  "pay": {...}} with the same describe() shape per domain.
`PUT /api/instances/{id}/rates` body {"cost"?: {...}, "pay"?: {...}}.
`DELETE /api/instances/{id}/rates` — reset both to the template.

Owner-or-admin on all six; another user's instance is 404, never 403, so
existence never leaks.

Run freeze: every job snapshots rules AND rates at start into
final-report/runs/config/<run_id>/ and points CREW_RULES_CONFIG at the
snapshot, so an edit made between stage 1 and stage 2 cannot split the two
halves. Both halves stamp rules_fingerprint + run_id into their artifacts;
stage 2 REFUSES loudly, before the solver starts, when the catalog's
fingerprint differs from its own. Catalogs from earlier releases carry no
fingerprint and keep running.

## Jobs

`POST /api/jobs` body: {"type": "stage1"|"complete"|"stage2"|"validate",
  "instance_id": str, "params": {...}} -> 201 {"job_id": str} or 409 if an
identical-type job for the instance is already running.
Params: stage1 {budget_min?: int=75, pairing_level?: 1|2|3,
  pairing_method?: "enumerate"|"columns", solve_s?: int, seed?: int,
  rescue_dh?: int, rescue_per_leg?: int, rescue_s?: int,
  w_night?: int, w_dh?: int} (v1: allowed for source!=gerad only if
label != inst1..7? — allowed for all; WARNING in UI about runtime).
pairing_level 1|2 switch stage 1 to the chain builder and require the
structure catalog from a keep_structure import; level 3 needs no
catalog. pairing_method "columns" = column generation (also reports a
restricted lower bound), "enumerate" = direct enumeration.
stage2 {budget_s: int=600, seed: int=7} (gerad-1..7 only, 400 otherwise);
complete {} (gerad-5..7); validate {} (any instance with artifacts).
Commands (cwd repo root, .venv python):
  stage1:  _gerad_colgen.py <N> --pricer duty --mp 10 --dump-selected
           --dump-pool --cert-budget ... (gerad) or
           _gerad_colgen.py 0 --payload <path> --profile v2 --name <label> (other)
  complete: _gerad_complete_catalogs.py <N>
  stage2:  STAGE2_PORT=<port from pool 9092-9098> _gerad_stage2_roster.py N N <budget_s> <seed>
  validate: _coverage_validator.py --gerad N && _gerad_trajectory_check.py N
Job = {"id", "type", "instance_id", "status": "running"|"succeeded"|"failed"|"stopped",
  "exit_code": int|null, "started_at", "ended_at", "log_tail": [str],  // last 40 lines
  "params": {...}}
Nonzero exit != crash for stage2/direct (gate semantics): map exit_code>0 with
completed artifacts to status "succeeded" + field "gate_nonzero": true.
Same for stage1: exit_code 4 (catalog published, remaining uncovered legs
provably impossible) with a freshly published canonical catalog maps to
"succeeded" + "gate_nonzero": true; exit codes 2/3 stay failures.
`GET /api/jobs` -> newest-first list. `GET /api/jobs/{id}` -> Job (full).
`GET /api/jobs/{id}/log` -> text/plain full log.
`POST /api/jobs/{id}/stop` -> kills the process tree.
Persistence: webapp/jobs/<id>.json + <id>.log; loaded read-only on restart.
The file also carries an "_internal" block ({"_run_id", "_started_ts"}) so the
run identity — the name of the convergence trace file — survives a restart;
it is stripped from every API response (all "_"-keys are). Records written
before this block existed recover their run id from the problem manifest.
Child processes always receive RESOLVED absolute input paths (registry logical
records are data-root-or-repo; the child would resolve them against its own
cwd and miss the file); a missing input is refused at job creation (400).

## Results

`GET /api/results/{instance_id}` -> {
  "stage1": {"objective", "ip_status", "covered", "total_legs",
             "proven_impossible": [str], "source", "fingerprint"} | null,
  "stage2": {"result_status", "stage2_score", "pref_hits", "pref_attainable",
             "vacation_violations", "unassigned", "trajectory_violations",
             "missing_legs", "budget_s", "seed", "wall_s",
             "resource_rules_applied": bool,
             "resource_rules_declared_not_applied": [str]} | null,
  // 2026-07-26: a rule DECLARED in a rule pack that never reached the
  // engine forces result_status != OK — "zero hard violations" from a
  // solver that did not know the rule is a claim about another task.
  // The stage-1 catalog additionally carries "coverage"
  // {complete, uncovered, no_pair_in_horizon, not_built, verdict:
  //  OK|INFEASIBLE_INPUT|INCOMPLETE_SEARCH} and "config_sig"
  // {level, method, ..., rules_effective, rules_fingerprint,
  //  rules_overridden}. stage2_available is decided by that evidence,
  // NOT by the mere existence of the catalog file.
  "validators": {"coverage": row|null, "trajectory": row|null},
  "artifacts": [{"name", "path", "bytes"}]   // whitelisted, see below
}
Sources: final-report/best/meta_<label>.json + gerad_colgen_selected_<label>.json;
final-report/gerad_phase2_two_stage.json; gerad_coverage_check.json;
gerad_trajectory_check.json.

`GET /api/results/{instance_id}/roster.csv` -> CSV export of the stage-2
roster (only when result_status is present): columns
employee,pairing_id,leg,role,dep_airport,dep_utc,arr_airport,arr_utc,is_deadhead
from gerad_stage2_assignment_instN.json + payload times. UTF-8 BOM for Excel.

`GET /api/results/{instance_id}/pairing` -> данные визуализации пейринга
(лента цепочек + карта сети; 2026-08-29), только при готовом каталоге
этапа 1 — иначе 404; тот же owner/shared-гейт, что у скалярных
результатов: {
  "label", "run_id", "ip_status", "objective",
  "meta": {"bases": [str], "t0", "t1", "chains_total",
           "chains_truncated": bool, "legs_total",
           "duty_split": "catalog"|"gap600", "unresolved_legs"},
  "chains": [{"cid", "base", "cost", "nlegs", "ndh", "nights", "span",
              "legs": [{"leg", "dep", "dep_utc", "arr", "arr_utc",
                        "dh": bool, "duty": int}]}],
  "uncovered": [{"leg", "dep", "dep_utc", "arr", "arr_utc",
                 "why": "proven_impossible"|null}],
  "arcs": [{"a", "b", "n", "ndh", "nunc"}],
  "nodes": [{"code", "base": bool}]
}
Источник: gerad_colgen_selected_<label>.json (канонический дамп — тот же
файл, что у карточки этапа 1; фолбэк — реестровый catalog_path) + payload.
chains отсортированы (base, t0) и обрезаются сервером до 600 строк
(chains_truncated); arcs/nodes/uncovered считаются по ПОЛНОМУ набору до
обрезки. Смены (duty) — из duty_fns каталога построителя; для
colgen-каталогов без duty_fns — эвристика разрыва >= 600 мин, честно
помеченная duty_split="gap600". Перегон (dh) = fn из fns вне active_fns.
Непокрытые = обязательства payload (без transportOnly и
coverage.auto_plan_excluded) − объединение active_fns всех цепочек;
why — из proven_impossible_legs каталога.

`GET /api/artifacts/{name}` -> download; whitelist exactly:
gerad_stage1_results.json, gerad_coverage_check.json,
gerad_trajectory_check.json, gerad_phase2_two_stage.json,
gerad_phase2_direct.json, experiment-manifest.json,
gerad_colgen_selected_inst[1-7].json, gerad_stage2_assignment_inst[1-7].json.
404 otherwise; no path traversal (name must match whitelist literally).
PUBLIC reference library, not the per-tenant workspace: this is the published
GERAD/BTS benchmark corpus the onboarding demos serve to every authenticated
user, so — unlike /api/results/{id} (owner-or-admin) — it is intentionally
NOT gated per-owner. The allowlist is the gate: it holds only fixed GERAD/BTS
names and can never contain a tenant upload, so no private data is exposed.
Auth is still required (like every /api route).

## Frontend (webapp/static/: index.html, app.js, style.css; no CDNs)

Russian UI, 4 tabs:
1. «Данные» — instance table (id, source, legs/employees/slots, catalog
   objective, gate badge); upload form (file or textarea JSON + name) with
   validation errors displayed; link to schema view.
2. «Расчёты» — instance picker + stage buttons with params (stage-2: budget,
   seed); running jobs with live log tail (poll /api/jobs/{id} every 2 s);
   stop button; history list.
3. «Результаты» — per instance: gate verdict badge (OK green /
   INFEASIBLE_INPUT_OR_MODEL yellow / INFEASIBLE_INCUMBENT red), stage-1
   card, stage-2 card, validator rows, roster.csv download, artifact links.
4. «Формат и правила» — canonical schema (pretty JSON), airline config form
   (editable, PUT on save) with the honest "не применяется к движкам в v1"
   banner.
Status colors: OK #2e7d32, INPUT_OR_MODEL #f9a825, INCUMBENT #c62828.
Polling only (no websockets). fetch() with error toasts.

v1.3 addendum (COSMOS Evolution — self-improvement pipeline):
- Three installs on legion: Prod (crew-demo, :8090, internal contour),
  Stage (cosmos-stage, :8091, internal), Test (cosmos-test, external
  contour — public data + cloud LLM allowed). Data-contour invariant:
  the Claude CLI (scan + autonomous implementation) runs ONLY with cwd
  inside COSMOS_TEST_DIR; a runtime guard refuses if Test overlaps
  Prod/Stage. Stage/Prod receive code only via git merge of the reviewed
  branch — no AI edits there.
- webapp/problem_manifest.py: единый НЕИЗМЕНЯЕМЫЙ манифест постановки
  (аудит R10, P0-03). start_job после заморозки конфигурации строит
  problem-manifest.json в runs/config/<run_id>/: контент-хэши payload,
  source/resource-rules, base/pin-каталогов, каталога этапа 1, отпечатки
  доменов заморозки, личность сборки решателя, resolved_config;
  manifest_id = sha256 канонического тела, файл пишется O_EXCL, load()
  пересчитывает идентификатор. Отказ манифеста = отказ запуска (503).
  Дочерние процессы получают CREW_PROBLEM_MANIFEST(-_ID); построитель и
  colgen штампуют идентификатор в config_sig каталога, манифест ростера
  несёт problem_manifest_id, манифест этапа 2 записывает
  upstream_manifest_id из каталога (цепочка личностей). Запись задачи
  (GET /api/jobs) несёт problem_manifest_id. Этап 2 требует --catalog-fp
  (контент каталога, сверка от тех же байтов); прямой CLI построителя и
  colgen требуют --source-rules-fp вместе с --source-rules.
- POST /api/evolution/{id}/action: для action=stage|prod тело несёт
  countersign (токен tools/countersign.py); без него или с неверным —
  409 {"error": "countersign_required", "message"}; статус находки при
  отказе не меняется (аудит R10, P0-04).
- webapp/evolution.py: improvements store (users.db table, 15 statuses
  found→…→deployed/rejected). GET /api/evolution (admin) lists them; the
  «Лаборатория» tab renders them read-only.
  Impact scoring (вкладка «Алгоритмы»): колонка impact (JSON-кэш
  {score 0..100, confidence, rationale, published, cache_key, source,
  model}) — оценка эффективности публикации советником по решателям;
  source="llm" — есть score; source="unscored" — score НЕТ, есть error
  (причина: недоступность модели/неразобранный ответ), UI показывает ⚠
  с причиной. POST /api/evolution/score (admin, батч в демон-потоке,
  409 при повторе) + GET /api/evolution/score-status (поллинг); кэш
  инвалидируется по содержимому находки + версии skill.md
  (solver_advisor.impact_cache_key). LLM call-site advisor.impact
  (external-only) в llm_call_registry.
  Колонка published_at (ГГГГ[-ММ[-ДД]]) — дата публикации
  ПЕРВОИСТОЧНИКА (не дата находки агентом): пишется батчем оценки из
  arXiv-идентификатора URL (главный источник) либо из поля published
  ответа модели; заполняется и на кэш-пропуске. Сортировка «по дате
  публикации» на вкладке использует её; строки без даты — в конец, по
  дате находки.
- webapp/evolution_runner.py: nightly scan (CREW_EVOLUTION_SCAN_HOUR:30,
  off unless CREW_EVOLUTION_ENABLED=1) → Telegram finding with buttons;
  run_test_phase (quick battery) / run_stage_phase (full battery) /
  deploy_prod, each serialized by one pipeline lock, gated by the finding
  status (no double-drive), reporting to Telegram. Deploy merges the
  EXACT commit Stage reviewed (pinned sidecar), pytest-gates with hard
  rollback, and restarts only the Prod uvicorn (matched by --port).
- _evolution_bench.py: run --profile quick|full (mirrors webapp/jobs.py
  command lines) + compare (verdict/highlights). No new dependencies.
- Env (all in run-webapp.cmd, off by default): CREW_EVOLUTION_ENABLED,
  CREW_CLAUDE_CLI, COSMOS_PROD_DIR/STAGE_DIR/TEST_DIR, COSMOS_PROD_PORT.

v1.4 addendum (2026-07-16 — tenancy, scenarios, cost/KPI, dashboard, import):
Documents surfaces added after v1.3 and corrects statements the code now
contradicts. Written against the source (webapp/app.py, scenarios.py,
costing.py, kpi.py, runs_dashboard.py, registry.py) — how it IS, not how the
older prose describes it. Where this section and an earlier one disagree, this
section is authoritative.

### Tenancy (cross-cutting) — owner-or-admin, invisibility is 404 not 403
Every instance has an owner (registry.visible_to): the seed benchmarks
(gerad-1..7, bts-day) are owned by the primary admin; uploads/imports/scenarios
by their creator. A caller sees an instance iff it is theirs OR they are admin
(`owner is None` would also count as shared, but no code path produces it now).
- Every per-instance route resolves via registry.get_instance_for(id, user),
  which returns None for BOTH a nonexistent id and one the caller may not see;
  the route then raises 404. Invisibility therefore NEVER yields 403 — another
  tenant's instance is indistinguishable from a missing one, so existence does
  not leak. This holds for /api/instances/{id}, .../llm-scope, .../advise,
  /api/costing/{id}, /api/kpi/{id}, /api/scenarios/{id}/compare|DELETE,
  /api/pay|fairshare|pay-reselect|base-balance|vacation|board|qualifications/{id},
  and the results/jobs routes (jobs use the parallel _job_visible → 404 rule).
- Collection routes are pre-filtered to the caller's visible set:
  `GET /api/instances` → registry.list_instances_for(user);
  `GET /api/scenarios`, `GET /api/kpi` likewise. Admins get everything
  (each row still owner-annotated); a plain user gets only their own + shared.
- `GET /api/instances/{id}` now 404s (not 403) for an instance the caller may
  not see — the v1 prose ("[Instance]", no visibility) predates tenancy.

### Instance object — fields present since tenancy (v1 prose is incomplete)
`GET /api/instances` → `[Instance]`, `GET /api/instances/{id}` → Instance +
`{"validation": {...}}`. Beyond the v1 fields, every Instance now also carries:
- "owner": str|null            // owner email; seed data → primary admin
- "shared": bool               // == (owner is null); false for all real rows
- "catalog_path": str|null     // repo-relative pairing catalog (best catalog,
                               //   else canonical selected dump), or null
- "stage2_available": bool     // == (catalog_path is not null) — TRUE for ANY
                               //   instance with a catalog, NOT just gerad-1..7
                               //   (the v1 "gerad only" note is obsolete)
- "last_gate_status": str|null // last result_status for the label from the
                               //   two-stage journal (later rows win)
- "rules_origin": "defaults"|"installation"|"instance"|"scenario"
                               //   where THIS instance's tier-1 rules come
                               //   from (P1-10). In the LISTING so the UI can
                               //   label every row without a per-row call;
                               //   the count of params differing from the
                               //   template needs parsing and lives in
                               //   GET /api/instances/{id}/rules instead
- "llm_scope": "local-only"|"any"  // per-dataset LLM eligibility (see below);
                               //   uploads default local-only, seed → any
- "country": "INTL"|<ISO-3166 alpha-2>  // regulatory jurisdiction (default INTL)
Ownership is stored in webapp/config/ownership.json; llm_scope in
llm_scope.json; country in instance_country.json (all atomic writes; a present
but malformed file fails loud rather than silently treating owned data as
shared).

### LLM scope + solver-advisor
`PUT /api/instances/{id}/llm-scope` body {"scope": "local-only"|"any"}
  → 200 {"id", "llm_scope"}. Owner-or-admin (404 if invisible). 422 if scope
  not in {"local-only","any"}. Governs which backend a solver consult may use.
`POST /api/instances/{id}/advise` body {"job_type"?|"type"? (default
  "complete"), "backend"?} → owner-or-admin (404 if invisible). ADVISORY only —
  never applied. 400 if job_type not in {"stage1","complete","stage2",
  "validate"}; 400 on llm_backend.ScopeViolation (cloud backend requested for a
  local-only dataset) or ValueError. Response:
  {"instance_id", "job_type", "scope", "backend", "model", "descriptor"
   (the NON-PII payload actually sent to the LLM), "skill_version",
   "source": "llm"|"fallback", "recommendation": {...},
   "llm_error": str|null}. On any LLM failure a deterministic fallback
  recommendation is returned with source "fallback" (still HTTP 200).

### Universal import (LLM-assisted mapping)
`POST /api/import/analyze` body {"scope"? (default "local-only"),
  "files": [{"name", "text"|"content_b64"}, ...], "backend"?} → step 1, saves
  NOTHING. 422 if scope invalid or files not a non-empty list; 422
  {"errors":[str]} on ImportError_ (bad file / bad plan / no usable data);
  400 on ScopeViolation. 200 response:
  {"token" (pending id, 30-min TTL, bound to the caller), "plan" (the
   sanitized mapping plan), "stats", "preview", "validation" (canonical
   schema+referential), "semantic": {"errors","warnings"}, "warnings",
   "scope", "backend", "model", "source": "llm"|"fallback"|"canonical",
   "llm_error", "files":[{"name","kind","rows"}]}. The model only ever sees a
  sample and returns a column-mapping plan; every value written to the payload
  is read from the user's file by deterministic code (security boundary).
`POST /api/import/commit` body {"token", "name", ...} → step 2, saves the
  pending payload as a NEW instance owned by the caller with the scope chosen
  at analyze time. 422 if token/name invalid; 404 if the pending session is
  unknown/expired (or belongs to another user); 422 if the derived id already
  exists; 422 {"validation"} if canonical validation failed; 422 {"errors"} if
  semantic ERRORS remain (warnings are advisory). 200 → {"id", "llm_scope",
  "validation"}.

### Airline rule config — CORRECTION: applied is true
`GET /api/airline-config` (any authenticated user) →
  {"parameters": {min_sit_min, max_landings_per_duty, max_duty_span_min,
   min_rest_min, max_monthly_block_min, max_pairing_span_min},
   "applied": true, "note", "note_code": "rules_env",
   "defaults": {...per-field defaults...}}.
  Supersedes the §"Airline rule config" body: "applied" is TRUE (has been since
  the CREW_RULES_CONFIG wiring — every stage subprocess gets the config and the
  problem fingerprint includes the rule set), not false; the response also
  carries "note_code" and "defaults". The «Формат и правила» "не применяется"
  banner is likewise stale.
`PUT /api/airline-config` body {"parameters": {...}} → ADMIN-ONLY (403 for a
  non-admin — closed 2026-07-15; pay-and-credit now derives money from
  min_rest_min, so an ungated write could inflate payroll). 422 on an unknown
  parameter, a non-number, or a non-positive value. 200 → same shape as GET.

### Cost config (allowances layer)
`GET /api/cost-config` (any authenticated user) → the effective config merged
  over defaults: {"currency", "block_hour_rate", "per_diem_hour",
  "hotel_default_rate", "hotel_rates": {STATION: rate, ...}, "deadhead_factor"}.
`PUT /api/cost-config` body = partial or full config → ADMIN-ONLY (403 for a
  non-admin — gated from day one). MERGE semantics: only the keys present in
  the body overwrite the stored config, unknown keys are dropped, hotel_rates
  station codes are upper-cased; returns the full effective config. 422 on:
  currency not a 1–8-char string; block_hour_rate/per_diem_hour/
  hotel_default_rate/deadhead_factor negative or non-numeric; hotel_rates not
  an object, or a station code not 2–5 chars, or a negative/non-numeric rate.

### Costing report
`GET /api/costing/{id}` → owner-or-admin (404 if invisible). Two 200 shapes:
- No pairing catalog yet: {"available": false, "reason": "no_catalog"}
  (HTTP 200 — this is a normal "nothing to cost yet" state, not an error).
- Catalog present: {"available": true, "instance", "label", "currency",
  "config": {block_hour_rate, per_diem_hour, hotel_default_rate}, "pairings",
  "skipped", "totals": {block_h, tafb_h, pay_cost, per_diem_cost, hotel_cost,
  total_cost, nights}, "top_pairings": [{pairing, legs, block_h, tafb_h,
  pay_cost, per_diem_cost, hotel_cost, total_cost, nights} …top 15 by cost],
  "hotel_by_station": [{station, nights, rate, cost}],
  "rooms_forecast": [{date, stations: {STATION: rooms}, rooms}],
  "note_utc": true}. Scenario cost-rate overrides, when set, replace the
  headline rates for that instance. Money is UTC-approximate (documented note).

### Fleet KPIs
`GET /api/kpi/{id}` → owner-or-admin (404 if invisible). {"available": false,
  "instance", "reason": "no_catalog"} at HTTP 200 when there is no stage-1
  catalog; otherwise the KPI record: {"available": true, "instance", "label",
  "currency", "total_cost", "unit_cost_block_h", "cost_mix_pay_pct",
  "cost_mix_perdiem_pct", "cost_mix_hotel_pct", "block_h", "tafb_h",
  "productivity_pct", "pairings", "avg_block_per_pairing_h",
  "nights_per_pairing", "roster_available", "coverage_pct", "unassigned",
  "vacation_violations", "pref_hits", "gate_status",
  "crew_utilization_block_h", "employees"}. Roster-side fields are null before a
  stage-2 run (roster_available=false).
`GET /api/kpi` → {"rows": [KPI record, ...]} over the caller's visible
  instances (admins: all; users: own), sorted by total_cost desc; instances
  without a stage-1 catalog are skipped (nothing to measure).

### What-if scenarios
A scenario is an isolated clone of a base instance (own payload copy under a
unique label, own tier-1 rule set, optional cost-rate overrides). Being a
first-class instance, it also appears in `GET /api/instances` and every
per-instance route works on it.
`GET /api/scenarios` → {"scenarios": [{"id", "name", "base_instance", "owner",
  "created_at", "rules" (full tier-1 param set), "cost_overrides",
  "has_catalog", "last_gate_status"}, ...]} — only scenarios visible to the
  caller, newest first.
`POST /api/scenarios` (201) body {"name" (non-empty str), "base_instance_id",
  "rules"? (object; unknown/ non-positive params rejected), "cost_overrides"?
  (subset of {block_hour_rate, per_diem_hour, hotel_default_rate})} →
  {"id", "name", "base_instance", "owner", "created_at", "rules"
  (base's effective global rules with the overrides applied), "cost_overrides"}.
  Errors: 422 if "name" is missing/empty; 404 if base_instance_id is missing,
  nonexistent, or not visible to the caller (never 403); 422 on
  scenarios.ScenarioError (invalid rules/cost_overrides, or id collision).
`DELETE /api/scenarios/{id}` → {"ok": true}. Owner-or-admin: 404 when the id is
  not a scenario OR is not the caller's (a scenario you may not delete is
  indistinguishable from one that does not exist).
`GET /api/scenarios/{id}/compare` → side-by-side of the scenario vs its base:
  {"scenario": side, "base": side, "name", "rules_diff": [{param, base,
  scenario}], "cost_diff": [{param, base, scenario}]}, where side =
  {"instance", "missing", "rules", "catalog_objective", "last_gate_status",
  "kpi": {...}|null}. 404 if the id is invisible/nonexistent, or resolves to a
  real instance that is not a scenario.

### «История задач (расширенный вид)» dashboard — admin-gated on BOTH HTTP and websocket
(До 2026-09-07 вкладка называлась «Прогоны»; имя отдано списку прогонов
пакетного пути — раздел «COSMOS runs» ниже. Легаси-вкладка истории задач
теперь «История задач» во всех пяти языках интерфейса.)
The rich comparison view is a Panel document mounted IN-PROCESS at
/runs-dashboard (same port and Cloudflare Access edge; offline BokehJS/
Tabulator, no CDN). It streams ALL tenants' runs and fleet KPIs, so it is
strictly ADMIN-ONLY, enforced on two transports because the Panel mount
bypasses normal route dependencies:
- HTTP: the require_identity middleware returns 403 {"detail": "admin role
  required"} for any /runs-dashboard* request from a non-admin.
- WebSocket (/runs-dashboard/ws): an ASGI middleware (RunsDashboardWSGuard)
  authenticates the handshake and closes non-admin sockets with code 1008
  before Panel serves a document — @app.middleware("http") never sees a
  websocket scope, so the HTTP gate alone would not cover it.
The mount is boot-optional: if panel/bokeh-fastapi are absent from the venv the
app still starts (logs a WARNING) and the SPA simply omits the rich view.

### Activity journal (admin) — who signed in/out and which sections opened
- Store: TWO gitignored JSONL files — webapp/config/activity.jsonl
  (login/logout; cap 50 000 -> trim 25 000) and .../activity_nav.jsonl
  (nav; cap 20 000 -> trim 10 000). Splitting them is a SECURITY property:
  the nav endpoint is open to every authenticated user, so a nav flood
  must never evict another user's login/logout history (audit 2026-07-16).
  A per-email nav throttle (>=1 s) further bounds nav volume. Each row
  carries a per-run `seq` tiebreaker so the merged newest-first view is
  correct within a second. Recording NEVER raises into the request path;
  corrupt lines are skipped on read. The nav `tab` is validated against
  the fixed leaf-tab allow-list (not a charset — str.isalnum is
  Unicode-wide and a regex still admits prototype names).
- Events: "login" (access mode: first request of a fresh JWT — same signal
  as users.record_login; token mode: successful POST /api/login), "logout"
  (token mode: POST /api/logout; access mode: the frontend fires
  POST /api/activity/logout BEFORE redirecting to /cdn-cgi/access/logout,
  because the CF edge logout never reaches the app), "nav" (SPA tab-switch
  beacon; sent only when the tab changes).
- POST /api/activity/nav {"tab": slug<=24 [a-z0-9_-]} — any authenticated
  user; 400 on a malformed tab. POST /api/activity/logout — any
  authenticated user. GET /api/activity?limit=&email=&event= — ADMIN ONLY
  (403 otherwise); newest first, exact-email and event filters, limit
  clamped to 1..1000. Frontend: admin group tab «Журнал» (panel-activity).

### Day-of-operations plan vs fact (2026-07-17, gap #17)
Per-instance FACT store (webapp/config/ops_log.json, gitignored): one fact
per flightNumber {cancelled, diverted, dep_delay_min, arr_delay_min, cause
in {carrier,weather,nas,security,late_aircraft}, note}. Convention: NO fact
= the flight operated as scheduled. plan_fact.py (pure) matches plan
(payload flights) to fact by flightNumber and computes KPIs (completion
factor, OTP dep/arr within 15 min, avg delay, cause split, plan/operated
block hours). All endpoints owner-or-admin (get_instance_for; foreign
instance = 404):
- GET  /api/ops/{id} -> {summary, deviations}. deviations sorted
  cancelled>diverted>delayed>ontime>scheduled then by sched dep.
- POST /api/ops/{id}/fact {flightNumber, dep_delay_min?, arr_delay_min?,
  cancelled?, diverted?, cause?, note?} -> the stored fact; 400 on unknown
  flight/cause. DELETE /api/ops/{id}/fact/{flightNumber} reverts to plan.
  POST /api/ops/{id}/clear drops all facts.
- POST /api/ops/{id}/import-bts -> streams the bundled BTS on-time CSV
  (ops_replay.DEFAULT_BTS_CSV; 400 if absent, e.g. prod without the data),
  lifts actual delay/cancel/divert/cause matched by the canonical key
  ({Airline}{Flight}-{local dep HHMM}); the operating day is the
  FlightDate covering the most instance flights (no UTC/local assumption).
  Gated to source==bts instances; a non-blocking lock 409s concurrent
  scans. bulk-upserts. Returns {imported, summary}.
- POST /api/ops/{id}/generate-disruptions {delay_threshold_min?=15} ->
  idempotently turns facts into «Сбой» events (kind "flights" for
  cancellations, "delay" grouped by shift), clearing prior ops:auto events
  first. Returns per-kind counts. The neighbourhood rebuild then runs
  unchanged. Frontend: opt-group tab «Операция» (panel-ops) with KPI
  tiles, a time scrubber (client-side reveal by scheduled departure) and
  the deviations table.
NOTE: import-bts streams ~280 MB synchronously (~8 s) — acceptable for a
manual admin action on Test; move to a background job before routine prod
use.

### Strict mode: masking + contour gates + AI call-site registry (2026-07-17, formalization roadmap #8)
- Universal import: the mapping-plan SAMPLE (headers + <=30 rows) is
  masked shape-preservingly (letter->X/x, digit->9, separators kept)
  whenever it would reach the CLOUD backend; CREW_IMPORT_MASK_SAMPLES=1
  forces masking for the local backend too. analyze response gains
  "samples_masked": bool (shown in the import result).
- CR implementer (POST /api/cr/{id}/implement) now REFUSES on the
  internal contour: when this checkout is prod/stage per
  COSMOS_PROD_DIR/COSMOS_STAGE_DIR — or those dirs cannot be resolved
  (fail-closed) — the call 422s; suggestion texts never reach the cloud
  CLI from a customer perimeter. change_requests._agent_backend is
  fail-closed the same way.
- GET /api/llm-registry — ADMIN ONLY. The registry of every model call
  site {id, module, function, routing in {llm_scope, contour-guard,
  external-only}, backends, data, purpose} + "routings". Guarded by
  tests/test_llm_call_registry.py: a webapp module calling
  llm_backend.complete()/claude_cli() without a registry entry fails the
  suite. Frontend: admin group tab «ИИ-контур» (panel-llmreg).

### Raw zone + history anomalies (2026-07-17, formalization roadmap #7)
- Immutable raw zone (Ф3): data/raw/<inst_id>/ keeps the EXACT source
  bytes + manifest.json {instance, created_at, by, files: [{name,
  stored_as, sha256, bytes}]}. Write-once — a second store for the same
  instance raises; a failed commit discards the zone. Filled by universal
  import commit and SSIM import (canonical-JSON uploads are their own
  byte-exact source). File names are sanitized (basename + transliterating
  slugify + extension whitelist).
- GET /api/instances/{id}/raw-manifest -> {instance, manifest|null} —
  owner-or-admin (404 for foreign instances).
- GET /api/instances/{id}/anomalies -> {instance, warnings: [ru-string],
  stats} — owner-or-admin. Deterministic history-anomaly report (Ф6):
  block-time IQR outliers, duplicate ids, namesakes, dead souls, mixed
  months, unavailable-most-of-horizon. Advisory only, never blocks.
  The same report rides in the import analyze response as "anomalies"
  (block «Аномалии истории» in the import result).

### Canonical format v2 (2026-07-17, formalization roadmap #2)
$id crew-demo/canonical-payload/v2; formatVersion enum ["1","2"]; every
v1 payload stays valid. NEW OPTIONAL top-level arrays (referential checks
in registry.validate_payload):
- qualifications: {employee, kind in {licence, medical, visa, passport,
  type_rating, recency}, code<=40; dated kinds REQUIRE validTo (date),
  recency REQUIRES requiredEvents/recentEvents/withinDays}. employee must
  exist. The qualifications feature reads these directly when its own
  store is empty (compliance response gains "records_source":
  store|canonical|none); explicit submissions always win.
- contracts: {employee (unique), type in {lineholder, reserve, parttime,
  contractor}, monthlyGuaranteeHours<=300, blockHourRate, dutyRig 1..10,
  tripRig 1..10, currency} — absent fields fall back to the airline-level
  pay config.
- dutyHistory: {employee, month YYYY-MM (unique per employee),
  blockMinutes, dutyMinutes, creditMinutes, carryInMinutes} — the
  month-boundary carry-over cumulative-limit rules need.
- costComponents: {code, amount, unit in {per_block_hour, per_duty_hour,
  per_diem_day, per_landing, per_deadhead, fixed_month, fixed_trip},
  note}.

### Rule packs + extractor (2026-07-17, formalization roadmap #1/#4)
Rule as an object with full provenance: source document -> formal
statement -> executable constraint (tier-1 parameter) -> positive AND
negative tests -> signed approval. Store webapp/config/rule_packs.json
(gitignored). Discipline: both test kinds required to approve; a failing
executable test blocks approval; ANY edit resets it; the pack approves
only when every rule is approved; import always lands as a NEW DRAFT
(approvals never travel).
- GET /api/rule-packs (any authenticated) -> {items, parameters};
  GET /api/rule-packs/{id} -> full pack + live test_report per rule +
  compile {parameters, conflicts, outside_tier1} (this JSON IS the
  export/country template).
- ADMIN: POST /api/rule-packs; DELETE .../{id}; POST .../import;
  POST .../{id}/rules; PUT|DELETE .../{id}/rules/{rid};
  POST .../{id}/rules/{rid}/approve; POST .../{id}/approve;
  POST .../{id}/apply-config (approved + conflict-free -> writes the
  airline config through the same validation as PUT /api/airline-config).

### Rules import receivers (zone 2; 2026-07-24, extended same day)
`POST /api/import/rules` (ADMIN) body {"format", "file_text",
"source_name"?, "name"?, "country"?} -> runs
_rules_import.import_rules_text (deterministic, model-free) -> creates
a NEW DRAFT rule pack; returns {"pack_id", "pack_name",
"imported_rules", "errors"}. "file_json" is the legacy alias of
"file_text" (still accepted). format="auto" server-side sniff: JSON
structure / XML root / Rave markers / CSV header synonyms.
"country" (INTL or ISO-3166 alpha-2) is the pack's jurisdiction:
REQUIRED for formats whose files carry no jurisdiction
(meridian-limits, solver-format5) — omitting it answers 422 in words
(an empty country would make the pack apply to EVERY dataset); for
formats that carry a country in the file (cosmos-rules-json) an
explicit value that contradicts the file is refused, a matching one is
fine; other formats keep their previous behaviour (country from the
file or empty).
Implemented formats: solver-format5, meridian-limits, cosmos-rules-json
(JSON); jeppesen-rave (TEXT: subset parser — rule…end blocks with valid
clause, %p% = parameter with bounds/remark, body comparisons; unparsed
statements are counted and sampled honestly, no semantic guessing),
norm-table (TEXT: CSV `name;value;unit;window[;parameter;description]`
with RU/EN header synonyms, or JSON rows; canonical names map onto
tier-2b templates and — window permitting — tier-1 parameters; units
h/min/days auto-converted but REQUIRED unless the value is H:MM;
zero/negative/inf values and unit conflicts land in an honest
problems record, never guessed), roster-xml (TEXT: adjacent-domain
containers, e.g. nurse-rostering / Staff Roster Solutions; numeric
Max*/Min* leaves become resource records — unit taken ONLY from an
explicit Minutes/Hours/Days morpheme in the tag (hours converted to
minutes, bare *Time tags stay structural notes); skipped values are
counted with samples; DOCTYPE/ENTITY inputs are rejected fail-closed
across the WHOLE document, deep nesting handled iteratively). Closed formats sabre-altitude / netline-crew /
aims still answer 422 with the exact materials needed.
Rule constraint kinds now: "parameter" (tier 1, executable),
"note" (structural), "resource" (tier 2b, 2026-07-24): {resource,
window{min,max — at least one, non-negative Int32}, unit
minutes|count|days, level leg|duty|pairing|day|week|month|rolling28|
year|roster, extension}. resource rules are data-only (not solved yet)
and are listed under compile.outside_tier1. Template library:
_rule_templates.py (GERAD resource-extension formalism).
- POST .../{id}/extract {text, sensitivity: public|confidential,
  document, backend?} — two-contour AI extractor: public may use the
  cloud, confidential is local-only (ScopeViolation on cloud request);
  candidates land as DRAFTS with auto-generated numeric tests; unusable
  candidates are returned in errors. Frontend: tab «Пакеты правил» in
  the data group (read for all, actions admin-gated).

### Roster comparison / acceptance act (2026-07-17, roadmap #5)
- GET /api/roster-compare/{id} (owner-or-admin) -> {ours, historical,
  delta, notes}. ours = stage-2 assignment + board moves (FULL set, no
  board truncation); historical = payload flightAssignments[].employee
  chains split at gaps >= min_rest_min. Metrics per side: coverage,
  pairings, employees_used, block_h, credit_h (pay_credit per pairing),
  paid_h (max(credit, guarantee)), cost_total (costing), violations
  (board validator + intra-chain leg_overlap). Unavailable sides carry
  {available:false, reason: no_stage2_roster|no_historical_assignments}.
- GET /api/roster-compare/{id}/act.md -> markdown acceptance act with
  method limitations and signature lines. Frontend: card «Сравнение
  ростеров» on the «Операция» tab.

### Delta intake (2026-07-17, roadmap #6)
Upload-source instances only (seeds are reference data). Two-step: the
default is a PREVIEW; apply=true persists (payload re-validated — an
invalid result is never saved), journals to
webapp/config/schedule_deltas.json and rejects a repeated identical
delta by SHA-256.
- POST /api/instances/{id}/deltas/schedule {text, apply?} — supported
  ASM/SSM subset (documented in schedule_deltas.py): NEW/CNL/TIM/EQT,
  flight line "SU100/01MAR26", flights matched by number AND departure
  date; CNL drops the flight and its assignment slots; unparsed input is
  a loud 422.
- POST /api/instances/{id}/deltas/crew {name, text|content_b64, apply?}
  — CSV/JSON with CANONICAL employee columns; diff by id: add/update
  only, crews missing from the feed are LISTED (missing_in_feed), never
  silently deleted.
- GET /api/instances/{id}/deltas -> journal. Frontend: card
  «Дельта-обновления» on the data tab.

### Formalization Studio (2026-07-17, roadmap #3) — ADMIN
Sessions in webapp/config/studio_sessions.json (gitignored). Fixed
questionnaires ship in formalization_studio.py: source inventory (Ф1)
and semantics glossary (Ф5); every answer is recorded with author+date
(a decision, not a note). No AI in the studio.
- GET /api/studio -> {sessions, questionnaires}; POST /api/studio;
  GET|DELETE /api/studio/{id}; PUT .../answer {part, question_id, text}
  (empty text deletes); PUT .../mapping {instance_id?, plan?, notes?};
  PUT .../rule-pack {pack_id|null};
  GET .../package -> Customer Mapping Package: portable versioned JSON
  (inventory+glossary answers, mapping plan, raw-zone manifest, FULL
  rule pack) — the reusable country template. Frontend: admin tab
  «Студия».

### App Settings (2026-07-18) — SUPER ADMIN ONLY
A THIRD permission tier above admin: the super admin is a single email
(settings.SUPER_ADMIN_EMAIL — the FIRST address of env CREW_SUPER_ADMIN,
comma-separated, no default in code; unset/empty → None and the Settings
surface answers 403 for everyone; the token-mode operator counts too). It is an EMAIL compare, not a DB role
(the users.db role CHECK only allows admin|user). Gated server-side by
app._super_admin (403 "super admin only"). GET /api/me now carries is_super.
Two gitignored files under webapp/config/: app_settings.json (non-secret) and
claude_api_key (the raw Anthropic key — a usable secret, NEVER returned by any
endpoint; a GET reports only {configured, hint(last-4), source in file|env|none}).
- GET /api/settings -> {is_super, super_admin, api_key{configured,hint,source},
  usages[{key,cli}], models{usage->model-id|null}, default_backend,
  ollama_model, ollama_url, mask_samples, models_cache, effective{...},
  backends}. Use cases: coding (cli), text, ask_ai, advisor, agents.
- PUT /api/settings {models?, default_backend?, ollama_model?, ollama_url?,
  mask_samples? in cloud|always|null} — validated (422 on a bad model id /
  backend / mask mode).
- POST /api/settings/api-key {key} — stores the sk-ant-… key (422 otherwise);
  DELETE clears it (env fallback untouched).
- POST /api/settings/models/refresh {key?} -> live GET /v1/models via httpx
  (x-api-key), filtered to claude-*; caches + returns {fetched_at, models}.
  Sends NO customer data — contour-safe.
Contour invariant PRESERVED: the API key powers a CLOUD path that only fires
where llm_scope/contour already permits cloud. llm_backend.complete gained
usage=/model= — cloud+chosen-model+key routes through the Messages API, else
the agentic CLI (with --model). Coding stays on the agentic CLI (CR implementer
+ evolution) with --model injected via settings.cli_model_args. ollama_model /
ollama_url / default_backend now consult Settings before env. data_import
sample masking: cloud ALWAYS masked; Settings only tightens the local side.
Frontend: super-admin-only nav group «Установки» (panel-settings), gated by
is_super in renderNav/showGroup/showTab.

### Task-package channel (2026-08-29, owner directive: direct VMP+MG intake)

- POST /api/task-package/receive — MACHINE path (bypasses user identity via
  _MACHINE_PATHS, NOT in PUBLIC_PATHS). Body: raw zip bytes of the task
  package. Headers: X-Package-Sha256 (required; sha256 over the zip BYTES,
  verified on receipt), X-Spec-Version, X-Source-Run (cross-checked against
  the manifest — mismatch is named in detail), X-Channel-Token (shared secret
  env CREW_CHANNEL_TOKEN; constant-time compare; no env -> 403 "channel off"
  in words, nothing stored; wrong token -> plain 403). Body cap:
  CREW_CHANNEL_MAX_MB (default 64) -> 413. Producer gate runs in-process +
  readiness matrix is re-verified immediately; the solver is NOT started.
  Short verdict: {verdict: OK|GATE_RED|MISMATCH|REFUSED, mg: {pairing,
  rostering}, mismatch, detail, sha, spec_version, source_run,
  already_accepted}. Accepted packages land in data_root()/task-inbox/<sha>/
  (atomic tmp+replace) + current.json pointer + receipts.jsonl append-only
  journal; refused packages store NOTHING. Idempotency: repeat of an
  ACCEPTED sha returns the same verdict with already_accepted=true without
  re-running the gate. Channel is intra-contour only: packages carry
  customer data and never leave the machine without the owner's word.
- (2026-09-07, прогоны COSMOS — раздел «COSMOS runs» ниже.) Квитанция обеих
  веток (первичный приём и повтор того же sha) получает ключ `cosmos_run` —
  прогон COSMOS, принявший ЭТОТ приём; прежние пять ключей и их порядок не
  меняются (CDPM `cdpm/channel.py` копирует пять ключей и новый пока
  игнорирует — показ `cosmos_run` в отметке доставки идёт заявкой соседней
  сессии CDPM). Повтор того же sha после смены текущего прогона законно
  называет другой `cosmos_run` (пакет присоединён и к новому прогону).
  Порядок публикации приёма: `package.zip` → `verdict.json` (маркер
  завершённого приёма) → присоединение к ТЕКУЩЕМУ прогону (текущего нет —
  прогон создаётся автоматически, канал не отказывает) → зеркало
  `current.json` → журнал. Сбой присоединения приём не рушит:
  `cosmos_run: null`, `run_note_code`, пояснение в `detail`, зеркало на
  месте; повторная отправка того же sha присоединяет пакет. `receipts.jsonl`
  — ключ `cosmos_run` у новых строк. При выключенном инбоксе (пустое
  `CREW_TASK_INBOX`) шаг присоединения пропускается — стенды без изменений.
- GET /api/task-package/current — card data for the Conveyor VMP panel
  (авторизованный пользователь): resolver (env CREW_TASK_PACKAGE has
  PRIORITY; else channel pointer) + sha256, manifest summary, rules by
  zone, criterion with rates version, rule_maps count, MG verdict, source
  {kind: file|channel, at}, receipts tail. No package -> {present: false}.
- (2026-09-07) `GET /api/task-package/current` — плюс `run: {run, title,
  implicit} | null` (прогон COSMOS текущего пакета) и верхнеуровневый `sha`;
  `source.kind` ∈ `file | channel | upload` (загрузка человеком подписана
  честно — прежде показывалась как канал). Резолвер текущего пакета:
  `CREW_TASK_PACKAGE` (приоритет сохраняется; неявный прогон по
  `holder.json` базового рабочего каталога) → при заведённом хранилище
  прогонов — пакет ТЕКУЩЕГО прогона (`cosmos-runs/current.json`) → иначе
  прежнее чтение зеркала `task-inbox/current.json` (площадка до первого
  действия с прогонами работает как прежде). Пакет утерян — `path: null`,
  карточка называет причину кодом `package-bytes-missing`.
- Sender-side convention: CREW_CHANNEL_URL carries the ORIGIN only
  (e.g. http://127.0.0.1:8090); the sender appends
  /api/task-package/receive itself — do not double the path.
- POST /api/task-package/upload (2026-09-06/07, owner's word: drag-and-drop
  onto the flow block) — HUMAN path (session OR X-Channel-Token via
  _user_or_channel): multipart form, field `file` (zip). Same producer
  gate + readiness matrix as /receive; the package becomes current; the
  solver is NOT started. Refusals in words: 400 not zip / no file field,
  413 over CREW_CHANNEL_MAX_MB (three tiers: Content-Length sieve BEFORE
  the form is parsed — only when the header is present; a body without
  it (Transfer-Encoding: chunked — token clients, curl) is parsed by
  Starlette part by part onto disk and refused by the part's size
  (UploadFile.size) BEFORE the file is read into memory; the read itself
  is capped at limit+1 bytes, an extra byte gives the same 413. The file
  is taken via request.form(), not an UploadFile parameter, which would
  make FastAPI parse the whole body first; the form is closed in
  `finally` so the spooled temp file does not outlive the request;
  Russian detail), 422 GATE_RED/REFUSED
  brief. Both UI entries — the panel button «Загрузить пакет постановки
  (zip)» and drag-and-drop onto the «Постановка (ВМП)» block or the
  panel's upload row — call this one endpoint (flowUploadPackage).
- GET /api/flow carries `upload_max_bytes` (= CHANNEL_BODY_MAX_BYTES) so
  the client refuses oversized files in words before any request and
  localises a server 413; when the field is absent (older server) the
  client skips the size check and the server judges.

### Выгрузки решений пакета (2026-09-07, слово владельца 06.09: известные форматы, в том числе Meridian)

- Вход: сессия ЛИБО токен канала X-Channel-Token — путь
  `/api/task-package/current/export` входит в `_MACHINE_PREFIXES` (он же
  покрывает export-mps), поэтому в режимах token/access запрос с токеном
  без сессии доходит до обработчика; без токена — обычная личность.
- GET /api/task-package/current/export — указатель доступности:
  `{present, package_sha, roster_path: cold|on-pairing|null, solutions:
  {pairing: {present, certified, established, verdict, attempt, formats:
  {csv|xlsx|json|meridian: {ok, href | reason, needs_confirm?,
  form_status?}}}, roster: {… + ics, employees: [табельные]}}}`; без
  пакета — `{present: false}`. Причины — словами сервера; Meridian
  строится пробно. `href` — уже с префиксом `/api/…` (клиент берёт как
  есть). Базовая проверка — та же, что у файла: решение есть И штамп
  выдачи установлен (журнал кнопки), иначе все форматы `ok: false` с этой
  причиной. Вторая форма записи решения — когда источников нет (нет
  выдачи, нет package.zip, повреждённый JSON): `{present: false, reason,
  formats: {<fmt>: {ok: false, reason}}}` — без certified/established/
  verdict/attempt, причина на уровне решения продублирована по каждому
  формату. `roster.employees` — табельные номера ростера для панели:
  список выбора сотрудника рядом с чипом iCal (ссылка
  `…/roster/ics?employee=<табельный>`).
- GET /api/task-package/current/export/{solution}/{fmt} — файл.
  `solution` ∈ pairing | roster; `fmt` ∈ csv | xlsx | json | meridian
  (pairing), csv | xlsx | json | ics | meridian (roster). Параметры:
  `attempt` (снимок попытки), `path=cold|on-pairing` (ростер; умолчание —
  новейшая выдача), `employee` (iCal одного сотрудника), `allow_uncertified=1`
  (Meridian несертифицированного с пометкой), `utc=1` (перевод меток в UTC
  по объявленной шкале пакета; умолчание — метки как есть, как в наборе
  CDPM), `roles=gate|source` (алфавит роли участка Meridian: Р/П гейта BA
  либо Р / D/H писателя и импортёра COSMOS; умолчание — gate, для пакетов
  из источника Meridian — source), `dataset_id`, `preset_id` (явные
  идентификаторы Meridian; иначе объявление источника в манифесте, иначе
  отпечаток пакета / sha256 model.json).
  Ответ: тело файла как есть (CSV с BOM), `Content-Disposition:
  attachment; filename="cosmos-<solution>-<sha12>[-<attempt>][-<employee>]
  [-uncertified][-meridian].<ext>"` (+ `filename*=UTF-8''…` при не-ASCII),
  заголовки `X-COSMOS-Certified: true|false`, `X-COSMOS-Package`,
  `X-COSMOS-Solver-Run`, `X-COSMOS-Time-Scale` (`base-local;zone=<IANA>` |
  `utc[;zone=…]` | `shifted;offset=<мин>[;zone=<IANA>]` — шкала
  объявлена пакетом со сдвигом, перевод в UTC не определён контрактом |
  `unknown`), `X-COSMOS-Created-Scale` (`machine-local` |
  `utc`), `X-COSMOS-Form-Status: draft` (Meridian-ростер — проект формы),
  `Cache-Control: no-store`. iCal: `DTSTAMP` всегда в форме UTC (RFC
  5545 §3.8.7.2); при `machine-local` — метка журнала без перевода и
  заметка `X-COSMOS-DTSTAMP-NOTE` в шапке календаря. XLSX: записи zip с
  фиксированными датой, `create_system` и правами — одни байты на любой
  площадке.
- Причина → класс → код (vmp/export_solutions.py):
  ExportNotFound → 404: нет пакета; нет выдачи («решения пейринга ещё нет —
  нажмите «Пейринг»»); снимка попытки нет / в нём нет артефактов;
  неизвестный `solution` или `fmt` (перечень словами); неизвестный
  `employee`. ExportRefused → 409: выдача-остановка / без решения; в выдаче
  нет журнала (штамп created не установлен); формат не выражает решение
  (сквозной ростер → Meridian; division не определим; перевозчик не
  определим; цепочка разделена между сотрудниками; смены цепочки разбиты
  по-разному; места каталога не покрывают спрос; нет рейсов-обязательств);
  Meridian несертифицированного без `allow_uncertified=1`.
  ExportBadRequest → 400: `path` или `roles` вне словаря, кривой `attempt`.
- Детерминированность: одинаковые байты при повторном запросе того же
  решения (created — из журнала выдачи, не «сейчас»); выгрузки не
  хранятся. Персональные данные: только табельный номер.

### COSMOS runs (2026-09-07, owner's word: runs like CDPM; passport = reproduction recipe; replay from journal / replay fresh — this queue, owner's word 11:4x)

- Смысл. Список прогонов COSMOS в том же виде, что у CDPM (создать /
  выбрать текущим / отметить / архив / сортировка / ручной порядок);
  пакет из машинного канала и загрузки ложится в ТЕКУЩИЙ прогон; прогон
  хранит пакеты (по отпечаткам SHA-256), попытки нажатий с проверками по
  ВМП и ИДАК и выгрузками и ПАСПОРТ — рецепт воспроизведения (входы, код
  панели, решатели с параметрами по каждой попытке, модели по ролям,
  обмены с моделями, журнал внешних вызовов). Хранилище —
  записи-указатели `data_root()/cosmos-runs/` (`storage.json`,
  `counter.json`, `order.json`, `current.json`, `.lock`, `adoption.jsonl`,
  `run-NNNN/run.json`; архив — `cosmos-runs-archive/`) над `task-inbox/`
  (байты пакетов) и `package-runs/` (нажатия, снимки попыток; второй
  держатель того же sha — `package-runs/<sha>/runs/<run>/`; держатель —
  маркер `holder.json`); ничего не переезжает. Руководство — часть 3,
  разделы 12л, 12о, 12р, 12ц, 12я; справочник — часть 9, разделы 2.3, 3.1,
  4, 5, 6. Код — `webapp/cosmos_runs.py`, `webapp/task_channel.py`,
  `webapp/package_runs.py`, `webapp/flow.py`, `vmp/run/llm_exchanges.py`,
  `vmp/run/call_journal.py`, `vmp/run/replay_compare.py`.
- Вход. Сессия ЛИБО токен канала `X-Channel-Token` — только у перечисленных
  пар «метод + путь» (`_MACHINE_ROUTES`; `_MACHINE_PREFIXES` не
  расширяется): `GET /api/cosmos-runs`, `POST /api/cosmos-runs`,
  `GET /api/cosmos-runs/current`, `GET /api/cosmos-runs/run-NNNN`,
  `GET /api/cosmos-runs/run-NNNN/attempts/{attempt}` с хвостами
  `/file/{alias}`, `/exchanges[/{n}]`, `/export[/{solution}/{fmt}]`,
  `/replay-report`. Все прочие ручки прогонов — только сессия: с токеном
  без сессии промежуточное ПО отвечает 401/403, не 500. Параметры пути
  проверяются до обращения к диску: `run-\d{4}` (иначе 404 «прогон … не
  существует»), идентификатор попытки по маске (иначе 400).
- GET /api/cosmos-runs — `{runs: [...], current: "run-0003" | null,
  storage: {state: off | uninitialized | ready, legacy: {packages,
  current_sha12, dirs_without_state} | null}, env_package,
  inbox_disabled}`. Запись списка: `{run, title, current, position,
  created_at, origin: manual | auto-receive | auto-upload | adoption,
  state_kind: new | accepted | blocked | lost | working | done | stalled |
  refused | interrupted, state, package: {sha, sha12, source_run, source,
  spec_version, verdict, received_at, launch: {pairing, rostering, as_of:
  live | received}} | null, packages_count, attempts: {total, pairing,
  rostering, running, last: {id, task, verdict, progress, error,
  finished_at, replay_of?} | null}, checks: {vmp: {pairing, rostering},
  idak: {pairing, rostering}}, busy: {task, progress, sha12} | null,
  passport_summary: {models, solvers, code, catalog, state_code}}`.
  Нечитаемый `run.json` — запись с `state_kind: null`, `error_code:
  run-unreadable`; список не падает. Чтение в хранилище прогонов не пишет
  и прежние данные не оформляет (единственная запись читающих путей на
  диск — кэш реестра страницы в рабочем каталоге текущего пакета, как у
  схемы). У выключенного хранилища `storage.cause: inbox-off |
  runs-dir-empty` — какая переменная его выключила.
  Отказы всех ручек прогонов — `{detail: <слова>, reason_code: <код>}`
  (у `storage-off` ещё `cause`), включая проверки параметров пути и
  режима: `bad-run-id` (404 в пути, 400 в теле), `bad-attempt-id` (400),
  `bad-mode` (400), `not-found` (404, прогон не существует; у `PUT order`
  — 400 «прогон … не существует — порядок не записан», дословно как у
  CDPM), `run-unreadable` (409), `attempt-unknown` (404), `alias-unknown`
  (404), `artifact-missing` (404), `exchange-unknown` (404),
  `title-too-long` (400), `bad-order` / `bad-runs` (400), `run-busy` /
  `current-run` (409, архив), `archive-failed` (500); срок файлового
  замка хранилища и ошибка записи у пишущих ручек — 503 `storage-busy` /
  `storage-io` словами (не 500).
- POST /api/cosmos-runs — тело `{title?, make_current? = true}` → 200
  `{…run.json…, current}`; заводит хранилище (оформление прежних данных)
  при первом вызове. 400 — название длиннее 120 символов; 409
  `storage-off`; 409 `env-package` (при `make_current`); 409
  `current-busy` (при `make_current`, в текущем прогоне идёт нажатие;
  клиент предлагает создать без переключения).
- POST /api/cosmos-runs/init — `{initialized, adoption: {…}}`;
  идемпотентно; 409 `storage-off`. То же — команда
  `python -m webapp.cosmos_runs init [--data-dir …] [--dry-run]`.
- GET /api/cosmos-runs/current — `{run | null, package | null, storage,
  env_package, hint_code: no-current-run | storage-not-initialized |
  storage-off | env-package | run-unreadable | null}`; нечитаемая запись
  текущего прогона — `run: {run, title: null, state_kind: null,
  error_code: run-unreadable, error: <слова>}`, `package: null` (не
  молчание «прогона нет»). PUT /api/cosmos-runs/current — тело
  `{run}`; 400 «недопустимый номер прогона»; 404 «прогон … не существует»;
  409 `env-package`; 409 `current-busy`; пишет `cosmos-runs/current.json` и
  зеркало `task-inbox/current.json` (прогон без пакета — зеркало
  удаляется); ответ как у GET.
- GET /api/cosmos-runs/order и PUT /api/cosmos-runs/order — дословно как у
  CDPM: тело `{order: [...]}`; 400 «в теле запроса нужен список order» /
  «недопустимый номер прогона» / «прогон … не существует — порядок не
  записан»; повторы схлопываются; ответ `{order}`.
- POST /api/cosmos-runs/archive — тело `{runs: [...]}`, всё или ничего:
  400 (пустой список / недопустимый номер), 404 (прогон не существует —
  ничего не перенесено), 409 (нечитаемый `run.json`; занят нажатием —
  «дождитесь его конца»; текущий прогон — «сначала сделайте текущим
  другой»); переносы `os.rename` в
  `cosmos-runs-archive/run-NNNN[-ГГГГммдд-ЧЧММСС]`, `order.json` чистится,
  счётчик номеров поднимается; срыв → 500 с перечнем уже перенесённых;
  ответ `{archived, archive_dir, left}`. `task-inbox`, `package-runs` и
  снимки ИДАК не затрагиваются.
- PUT /api/cosmos-runs/{id}/title — тело `{title}`; пробелы по краям
  снимаются; длиннее 120 → 400; пусто — ключ снимается; ответ `{run,
  title | null}`.
- GET /api/cosmos-runs/{id} — карточка: `{…run.json…, state_kind, state,
  current, packages: [+ live_mg у текущего], attempts: [запись попытки +
  package_sha, kind, files: [{alias, href}], exports: {index_href},
  exchanges_href, journal, replay: {journal: {available, reason_code,
  reason_words}, fresh: {…}, of}, replay_report_href], passport}`.
  Доступность повтора считается при чтении легко (запись, два `stat`,
  хвост индекса, существование файла пакета без хеша, чтение настроек
  площадки — ни одного тела, ни одного зондирования сертификатора) и не
  хранится.
- GET /api/cosmos-runs/{id}/attempts/{attempt} — `{attempt, recipe, files,
  aliases}`; рецепт: `attempt`, `package`, `params`, `params_reconstructed`,
  `reproducibility`, `pairing`, `rostering`, `models` (`certifier` с
  `calls_code: live | replayed-from-journal`, `source_check_reader`,
  `arbiter` — всегда `none-in-cosmos`, `cdpm` — `made_by.models` пакета),
  `code`, `template_catalog`, `registry`, `checks: {vmp, idak}`,
  `solvers` (решатели попытки с параметрами фактом; + `solvers_code`),
  `lineage` (происхождение входов: `package_sha256`, `source_run`,
  `parent_attempt`, `record_id`), `resources` (расход попытки в токенах:
  `models[]` по роли и модели, `solver_seconds`; денег в записи нет —
  решение владельца 10.09.2026),
  `artifacts` (относительные пути снимка; нет файла — null). 404 «попытка …
  неизвестна в прогоне …».
- GET /api/cosmos-runs/{id}/attempts/{attempt}/file/{alias} — артефакт
  снимка попытки по `ALLOWED_FILES` (+ алиасы `реестр-попытки`,
  `обмены-с-моделью`, `обмены-с-моделью-пейринга`,
  `обмены-с-моделью-на-пейринге`, `показания-решателя`,
  `показания-решателя-на-пейринге`, `статус-прогона-решателя`,
  `статус-прогона-решателя-на-пейринге`, `журнал-внешних-вызовов`,
  `журнал-внешних-вызовов-пейринга`, `журнал-внешних-вызовов-на-пейринге`,
  `отчёт-повтора`); `.jsonl` — `text/plain; charset=utf-8` inline.
  GET /api/cosmos-runs/{id}/attempts/{attempt}/exchanges[/{n}] — строки
  `index.jsonl` либо `{n, role, request, reply, error}`; 404 с кодом
  причины. GET /api/cosmos-runs/{id}/attempts/{attempt}/export и
  `…/export/{solution}/{fmt}` — как `/api/task-package/current/export…` с
  рабочим каталогом попытки (необязательный аргумент `wd=` у
  `export_index`, `export_solution`, `_export_root`, `export_sources`;
  прежние вызовы не меняются).
- POST /api/cosmos-runs/{id}/attempts/{attempt}/replay — ТОЛЬКО сессия
  (машинный вход — по слову владельца, когда появится раннер гейта
  воспроизводимости); тело `{mode: journal | fresh}` → `resolve_attempt`
  → `package_runs.press(…, replay={mode, run, source_attempt})`, внутри
  которого — единственная полная проверка доступности
  (`replay_availability(deep=True)`: отпечатки тел журнала и байтов
  пакета; зонд сертификатора `certifier_identity` — только у «заново»),
  ручка её не повторяет → 202 — возврат `press` в режиме повтора плюс
  `attempt`: `{attempt_id, attempt: "<тот же новый id>", run, workdir,
  state: "кнопка: идёт" (состояние из `state.json` рабочего каталога),
  done: false, mode, source_attempt, task}`. 400 «mode вне словаря:
  journal, fresh» (`bad-mode`); 404 прогон / попытка; 409 `{"detail":
  "<слова>", "reason_code": "<код>"}` — `run-busy`, `attempt-running`,
  `journal-incomplete`, `pre-journal`, `disabled-by-site`,
  `replay-of-replay`, `snapshot-missing`, `package-bytes-missing`,
  `params-not-recorded`, `pairing-source-missing`,
  `source-would-be-trimmed`, `solver-url-missing`,
  `certifier-unavailable`, `storage-off`; отказ `press` — 409 его словами
  и `reason_code` как есть (`run-busy`, `journal-mismatch`,
  `workdir-not-prepared`, `source-would-be-trimmed`, иначе
  `press-refused`). «Из журнала» (`journal`): внешние стороны
  подставляются журналом источника (решатель, построитель, модель не
  вызываются; `CREW_SOLVER_URL`, CLI и ключ API не нужны), гейт байт в байт
  по всем файлам снимков с явным списком нормализации полей времени
  (нормализованный файл записывается форматом писателя — порядок ключей,
  отступы и переводы строк сохраняются, их смена остаётся различием);
  собственный журнал повтора — ссылки `served_from` без копий тел.
  «Заново» (`fresh`): внешние стороны заново с параметрами источника,
  полный журнал, сравнение по итогам (вердикты, счётчики, цепочки,
  стоимость, счёт, назначения по сотрудникам) с пояснением класса
  воспроизводимости (`wall-clock-legit-difference` |
  `deterministic-expected-same`). Повтор идёт в рабочем каталоге и на
  пакете попытки-источника (прогон не обязан быть текущим), живая выдача
  задачи в рабочем каталоге заменяется, снимок источника и его место в
  пределе попыток неприкосновенны. Ручка
  `POST /api/task-package/current/press` режим повтора не принимает.
- GET /api/cosmos-runs/{id}/attempts/{attempt}/replay-report — `{attempt,
  source_attempt, mode, report: {…report.json…}}`; 404 `not-a-replay`
  («попытка … не является повтором») / `report-missing` («отчёт повтора
  не записан: …»). Схема отчёта `cosmos-replay-report/v1`: `mode`, `run`,
  `source_attempt`, `attempt`, `task`, `kind`, `at`, `verdict: same |
  differs | not-comparable` (fresh: `same-outcome | differs |
  not-comparable`), `words`, `first_difference: {file, reason_code, path |
  line | offset, source, replay} | null`, `files: {compared, same,
  normalized_same, different: [{file, reason_code: bytes-differ |
  json-differs | text-differs | only-in-source | only-in-replay,
  first_difference}]}`, `outcomes: [{name, source, replay, same,
  source_file}]`, `pairing_diff`, `roster_diff`, `reproducibility:
  {class, explanation_code, deterministic_gate}`, `journal: {recorded,
  served, unconsumed, served_from, mismatch}`, `idak: {source, replay,
  note_code: not-run-for-replay | null}`, `environment: {source, replay,
  differs}`, `normalization`. Сводка в записи попытки (`replay.of.report`):
  `{verdict, words, first_difference, files_compared, outcomes_differ,
  environment_differs}`.
- Прежние ручки. `POST /api/task-package/receive` и
  `POST /api/task-package/upload` — квитанция + `cosmos_run`
  (+ `run_note_code` при сбое присоединения: `storage-off`,
  `package-bytes-missing`, `run-not-written`, `run-unreadable`, `bad-sha`,
  `runs-error`; см. «Task-package channel»); строки `receipts.jsonl` несут
  `source: channel | upload`. Повтор уже принятого пакета на площадке без
  хранилища сначала оформляет все принятые пакеты (по прогону на каждый,
  датой первого приёма) и лишь затем присоединяет повтор к текущему
  прогону — исключение sha из оформления действует только у первичного
  приёма. `GET /api/task-package/current` — + `run`,
  `sha`; `source.kind` ∈ `channel | upload | file`; без пакета из-за
  нечитаемой записи текущего прогона — `{present: false, note_code:
  run-unreadable, run: {run, title, implicit}, error}`.
  `GET /api/task-package/current/press` — + `run: {run, title, workdir,
  implicit} | null`, `workdir`, `packages_in_run`; `?attempt=` — + `recipe`;
  без пакета по-прежнему ровно `{present: false}`.
  `POST /api/task-package/current/press` — + `deterministic: int = 0`,
  `seed: int = 0`, `stepLimit`, `unimprovedStepLimit`, `deterministicTime`
  (профиль Д1 оркестратора; умолчание — стенные часы, не меняется).
  `GET /api/flow` — + `run: {run, title, state_kind, package_sha12,
  packages_count, implicit, busy_previous: {sha12, task, progress} | null}
  | null` (нечитаемая запись текущего прогона — `{run, title: null,
  state_kind: null, error_code: run-unreadable, error, …}`) и
  `runs_hint_code: no-current-run | storage-not-initialized |
  storage-off | env-package | run-unreadable | null`; без прогона и без пакета — легаси-блоки
  плюс подсказка; прогон без пакета — десять блоков в покое с параметром
  `run` у блока ВМП. `POST /api/source-check/run` — в `binding` и в снимках
  `running`/`error` — `cosmos_run`, `workdir`; схема отсекает чужой снимок
  сначала по `package_sha`.
- Настройки площадки: `CREW_COSMOS_RUNS_DIR` (корень хранилища; пусто —
  выключено), `CREW_ATTEMPTS_KEEP` (предел попыток, умолчание 20; 0 — без
  предела; счётчик `attempts_trimmed`), `CREW_LLM_EXCHANGES` (0 — без
  обменов с моделью), `CREW_CALL_JOURNAL` (0 — без журнала внешних вызовов;
  в режимах повтора журнал пишется всегда). При пустом `CREW_TASK_INBOX`
  хранилище выключено вместе с инбоксом: чтения пусты
  (`inbox_disabled: true`), пишущие ручки — 409 `storage-off`.
- Инварианты. Читающие пути (список, карточка, `/api/flow`, резолвер
  текущего пакета) в хранилище прогонов не пишут и не оформляют прежние
  данные (кэш реестра страницы в рабочем каталоге текущего пакета
  ведётся, как у схемы); `_lifespan` каталог `cosmos-runs` не создаёт; оформление
  прежних данных — только из пишущих действий (создание, приём/загрузка,
  `PUT current`, `POST init`, CLI), идемпотентно по состоянию, пишет только
  новые файлы (`cosmos-runs/*`, `holder.json`), `state.json` не трогает;
  `run.json` пишется одним помощником атомарной замены под замком
  процесса и файловым `.lock`; текущий пакет = пакет текущего прогона
  (`cosmos-runs/current.json` — правда после заведения,
  `task-inbox/current.json` — зеркало, пишется последним); первый
  держатель sha остаётся в `package-runs/<sha>/` (пины стендов держатся);
  `CREW_TASK_PACKAGE` сохраняет приоритет (неявный прогон по
  `holder.json`); запись попытки несёт `run`, `params`, `reproducibility`,
  `certifier`, `code`, `template_catalog`, `solver`, `engine_versions`,
  `registry`, `exchanges`, `summary`, `source_check`, `journal`, `replay`
  — все новые ключи необязательны, попытки прежних версий читаются как
  `pre-passport` / `pre-journal`; в снимок попытки снимаются
  `llm-exchanges/`, `external-calls/`, `registry/`, `replay/report.json`,
  `solver-status.json`, `applied-rules.json`.

### Каталог шаблонов: заявки и CR с ключом записи (08.09.2026, шаг 6 каталога)

Заявка каталога — ЗАПИСЬ раздела `requests` каталога, а не текст в
очереди CR. Решение по заявке принимает ТОЛЬКО владелец, и принимает его
в службе каталога (`catalog_service`, контракт `catalog_service/API-CONTRACT.md`).
COSMOS зеркалит это решение в свою очередь изменений; второй отмашки в
очереди нет по замыслу.

- `GET /api/cr/catalog` (админ) — вид «Каталог» вкладки «Изменения»:
  ```json
  {"service": {"api_url": "http://127.0.0.1:8105"|null, "ui_url": "…"|null,
               "token": true|false, "mode": "edit"|"view"|null,
               "same_catalog": true|false|null},
   "catalog": {"version", "fingerprint", "path"},
   "requests": [{"request_id", "state", "section", "record_id",
                 "submitted_by", "source_kind", "ref", "kinds",
                 "cr_key", "cr_key_expected", "cr_key_words", "cr_status",
                 "link"}],
   "crs": [строки очереди с agent ∈ (catalog-executor, vmp-gap, template-base)],
   "candidates": [{"path", "cr_key", "branch", "evidence_commit", "where"}],
   "outbox": {"pending": N|null, "path": "…", "words": null|"…"},
   "auto": true|false|null}
  ```
  Токен машинного канала в ответе НЕ передаётся — только признак «задан».
  Каталог недоступен — 503 словами. Заявка с номером не по закону ключа
  (`req-<20 цифр>`) не роняет вид: у строки `cr_key_expected: null` и
  `cr_key_words` со словами, сверка кладёт те же слова в `problems` и
  строку очереди по ней не открывает.
- `POST /api/cr/catalog/sync` (админ) — «Сверить с каталогом»:
  `{"opened": [ключи CR], "keyed": [...], "rejected": [...],
  "refreshed": [...], "skipped": [...], "problems": ["слова"],
  "service": {"mode", "same_catalog"}}`. Сверка ИДЕМПОТЕНТНА: повтор
  ничего не меняет ни в очереди, ни в каталоге. Ключ CR пишется в
  каталог (`PUT /api/requests/{id}/cr_key`) только там, где служба в
  режиме правки И смотрит в тот же каталог; на площадках — слова один
  раз. Внутренний контур строк не открывает (реализация CR зовёт
  облачную модель) — только отчёт. Та же сверка идёт нитью
  `catalog-sync` при старте (при заданном `CREW_CATALOG_API_URL`) и раз
  в сутки в нити планировщика CR.
- `POST /api/cr/catalog/deliver` (админ) — повторная доставка файлов
  очереди заявок COSMOS: `{"delivered": [...], "failed": [...],
  "skipped": [...], "problems": [...]}`.
- `POST /api/task-package/current/press/catalog-requests` (админ) —
  «Заявить в каталог» по артефакту последнего нажатия
  (`vmp-rule-gaps.json`, ключ `catalog`). Тело
  `{"attempt": null|"<id попытки>", "rule_ids": null|["R-…"]}`; ответ —
  квитанция `{"form": 1, "run": "run-NNNN", "rows": {…}, "problems": […]}`,
  она же пишется артефактом `catalog-requests.json`
  (алиас `заявки-каталога-пейринга`). Ключ заявки пересчитывается по
  закону читателя и сверяется с номером из артефакта.
- `POST /api/template-base/change` — ОСТАВЛЕНА ради совместимости и
  всегда отвечает 409 `{"error": "…словами…", "link": "<карточка записи
  в службе>"|null, "dependents": []}`: правка и снятие записи каталога
  идут в службе (заявка каталога у существующей записи бывает только на
  вид исполнителя, правило R02 читателя).
- `GET /api/template-base` дополняется блоками
  `catalog_service: {"url": "<CREW_CATALOG_URL>"|null}` и
  `requests: {"open": N, "states": {"<состояние>": N}, "cr_keys": N}`, а у
  каждой записи — `executor_requests: {"<вид>": {request_id, state,
  submitted_by, cr_key, link}}` (заявка, из-за которой клетка «заявлен»;
  `link` — карточка заявки в службе, той же формулой, что у вида
  «Каталог»). Клетки без заявки в блоке не значатся.
- `GET /api/flow` — у клеток матрицы готовности рядом с числом
  `registry_gaps` идёт перечень `registry_gap_rows`
  (`[{rule_id, kind, note, form, request: {request_id, state, link}|null}]`,
  пустой при реестре прежней формы). `form` — форма пробела по
  классификатору (`kind` — заявка на вид исполнителя, `new` — на новую
  запись, `edit`, `defect`, `none`), `request` — заявка, если она уже
  есть: номер, состояние и `link` на её карточку в службе каталога (без
  заявки — `null`, а панель ведёт на ручную подачу). Панель показывает
  их столбцами «форма» и «ссылка»; оба поля берутся у той же строки
  классификатора (`vmp.catalog_requests.classify_gaps`), второго
  источника правды для них нет.

Строка очереди CR каталога (`webapp/config/change_requests.json`):
`kind: "catalog-executor"`, `id` = `cr_key_of(request_id)`
(детерминирован — тот же CR открывается на любой машине), блоки
`record` (раздел, запись, номер и форма заявки, кто подал, версия и
отпечаток каталога), `spec` (черновик либо запись целиком, виды к
реализации и отложенные виды решателя, формы стендовых ожиданий, закон
приёмки), `work` (белый список путей, запреты, кандидат, команды),
`journal`, `impl.gates_report`. Ключа `rule` (машинная форма правила
авиакомпании) и слов решения владельца в строке нет по построению.
Отклоняется такая строка ТОЛЬКО зеркалом решения владельца (ручка
`reject` отвечает 409 словами).
