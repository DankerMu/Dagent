# Dagent Engineering Contract

Repo-local rules for the Xagent fork. Project boundaries live in `CONTEXT.md`;
confirmed domain definitions live in `openspec/glossary.md`.

## Code Canonicality

- Extend the existing implementation before adding another. No parallel runtime,
  copied modules, commented-out implementations, or `_v2`/`_new`/`_backup` variants.
  Protocol versions and migration identifiers are not implementation alternatives;
  any mechanical exemption must name its target and reason.
- `src/xagent/core/agent/` is the only Agent runtime. Do not introduce
  `agent_v2` or `agent_runtime` paths.
- Do not commit scratch directories, credentials, test runtime data or backups.
  Git is the rollback mechanism; do not discard another contributor's work.
- New gates must accept valid inputs and reject representative violations.
  Never relax gates, regenerate baselines, delete tests or hide failures to pass.

## Project Identity

Goal: “对本项目做改动，同时吸收上游项目的更新”.
Dagent is the maintained customization of `xorbitsai/xagent`, not a package rename.
Python imports and application names remain `xagent` unless a separate change
explicitly migrates all consumers.

- Profile: **L3**, explicitly selected; legacy debt is frozen and ratcheted.
- Tooling: Oh My Pi. `.omp/` is intentionally local and ignored, not a CI dependency.
- Humans accept functionality. Implementation review follows `subagent-workflow`;
  do not claim that an AI review is human code review.
- Thresholds, deviations, ownership and verification commands: `constraints.yaml`.

## Stack & Versions

Python/FastAPI/SQLAlchemy backend; TypeScript/Next.js frontend; pytest and Vitest.
Use `uv.lock` and `frontend/package-lock.json`; `make setup` installs the chosen
local development environment. `.tool-versions` records the local toolchain;
existing CI compatibility jobs remain authoritative for their own versions.

- Preserve existing Ruff, isort, mypy, ESLint and TypeScript checks.
- Source Python uses relative imports; tests use absolute `xagent` imports.
- Do not upgrade unrelated dependencies or hand-edit lockfiles. A dependency
  change needs its manifest, resolved lockfile and a concrete justification.
- Semgrep is isolated from application dependencies by `make setup-security-tools`;
  its pinned version lives in the Makefile and is installed identically in CI.
  Knip ignores the `jscpd` dependency because Python's engineering adapter invokes
  that installed CLI; it is not a frontend import.

## Directory Map

| Path | Responsibility |
|---|---|
| `src/xagent/web/` | HTTP/WebSocket boundary, auth, persistence and workers |
| `src/xagent/core/agent/` | AgentService, runner, runtime and execution patterns |
| `src/xagent/core/tools/` | Tool implementations and adapters |
| `src/xagent/core/memory/` | Agent/user memory, separate from KB/RAG |
| `src/xagent/core/tools/core/RAG_tools/` | Knowledge ingestion and retrieval |
| `src/xagent/config.py` | Shared configuration and path authority |
| `src/xagent/migrations/` | Database schema evolution |
| `frontend/src/` | Web UI and its component/contract tests |
| `tests/` | Behavioral, integration, architecture and E2E verification |
| `scripts/engineering/` | Repo checks and isolated runtime verification |

## Development Workflow

### Branch strategy

- `origin` is `DankerMu/Dagent`; `upstream` is `xorbitsai/xagent`.
- `main` is a clean upstream baseline; `dagent` integrates custom development.
- Create `feat/*`, `fix/*` or `chore/*` from `dagent`, and target `dagent` in PRs.
- Sync by fetching upstream, fast-forwarding `main`, then merging `main` into
  `dagent` through a verified integration change. Never rebase/force-push shared
  long-lived branches. Upstream contributions branch separately from clean `main`.
- Local branches do not prove GitHub default-branch or protection settings exist.

### Commands

| Task | Command |
|---|---|
| Install locked development dependencies | `make setup` |
| Start development runtime | `make dev` |
| Static checks and configured regression checks | `make check` |
| Lint / typecheck | `make lint` / `make typecheck` |
| Format intended files | `make format` |
| Python and frontend tests | `make test` |
| Real database integration | `make test-integration` |
| Build shipping artifacts | `make build` |
| Explicit diff scope | `make change-scope BASE=dagent` |

Commands must fail when their required tool, credential, baseline or report is
missing. A collection/setup failure is not a test failure and neither is a pass.
Full-suite runs are bounded; report the command, scope, exit and first failure.

Full local tests also need the document-processing extra, Cairo, and
`pptxgenjs@4.0.1` (install with `npm install -g --prefix "$HOME/.local" pptxgenjs@4.0.1`).
Set `NODE_PATH` to that prefix's `lib/node_modules`. Warm the real tokenizer cache
before network-isolated tests: `.venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'`.
Use the project venv followed by `/usr/bin:/bin:/usr/sbin:/sbin` in `PATH` for
trusted system Bash and Boxlite's `sysctl` virtualization probe.
On macOS, system Make/shell can strip `DYLD_*`; pass Cairo's library directory
at the Python boundary, e.g. `make test-python PYTHON="env DYLD_FALLBACK_LIBRARY_PATH=$(brew --prefix)/lib .venv/bin/python"`.
Use `PYTHON_DOTENV_DISABLED=1` and an isolated `XAGENT_STORAGE_ROOT`; do not test
against developer credentials or a real user database.

## Verification Matrix

| Surface | Command | Evidence |
|---|---|---|
| Engineering constraints | `make check` | Separate gate results; no unknown baseline treated as zero |
| Gate discrimination | `make test-guardrails` | Valid fixtures pass; each violation fails for its intended reason |
| CI failure propagation | `make test-ci-contracts` | Failed/cancelled/skipped required work cannot become green |
| Structured logging | `make test-logging` | Parsed JSON, levels, exceptions and credential redaction |
| Build | `make build` | Backend package and Next static export |
| Health and API behavior | `make smoke` | Real HTTP status/body, login and isolated resource lifecycle |
| Browser behavior | `make verify-ui` | Actual interactions, screenshots and no unexpected console errors |
| Real model task | `make verify-real-model` | Xagent task reaches terminal success using the specified provider/model |
| Real database behavior | `make test-integration` | Disposable database; relevant integration outcomes |

Existing widget per-file coverage floors must never decrease. New code must meet
L3; historical coverage requires an actual measured baseline, not a small smoke
suite presented as repository coverage. Discovery and skipped tests are reported;
a capability-proving command fails if it performs no proof.

## Runtime Lifecycle

- `make dev`, `make dev-stop`, `make dev-status`, `make logs` own the isolated
  development runtime. Do not kill a process based on an unverified stale PID.
- Runtime tests use disposable storage and owned processes, not `~/.xagent` data.
  Seed/reset operations must refuse unowned or external database destinations.
- Use `make seed` and `make db-reset` only for the owned test environment.
- Real model verification is **local only**. Supply `DMXAPI_KEY` privately;
  do not print it, write it to tracked files, or inherit unrelated credentials.
  The selected model is `deepseek-v4.1-flash`; no silent fallback is permitted.
- No fixed paid-call count cap was requested. Requests, tasks and processes still
  have deadlines; repeated identical failures require diagnosis, not blind retry.
- KB collection CRUD does not prove document ingestion or semantic retrieval;
  those require a separately configured embedding model and their own evidence.

### Rehabilitation gate

Measured baselines and the runtime verifier are established; initial harness
stabilization is complete. This does not authorize broad code cleanup: use one
scoped issue per remediation and preserve every verification gate.

### Legacy baseline

Remove each temporary baseline exemption when its finding reaches the L3 target.
Baselines are keyed by finding identity and severity; no new or worsened finding
is allowed, even if another old finding disappears. CI never regenerates them.
The counts, reference revision, next milestone targets and owner live in
`constraints.yaml` and its named baseline artifact. Unknown means unmeasured.
Capture only with explicit approval using `make baseline-capture REFERENCE=<SHA>`;
CI never invokes this write operation. Scanner installation is part of `make setup`.

Coverage uses `.engineering/coverage-floors.json`, measured from `baseline.rev`.
`COVERAGE_BASE` defaults to that frozen revision; `BASE` remains the moving PR
base for diff/static-artifact checks. All post-bootstrap source stays subject
to the new-file floor. Reference test failures are recorded in measurement
metadata, not presented as a passing historical suite.
Coverage commands also require `--artifact-base` (Make `BASE`) to bind floor
bytes to the actual PR base; the frozen source revision cannot provide that lock.
Explicit recapture keeps public digest annotations outside `files`, using a
same-line `_..._note` JSON sibling containing `# pragma: allowlist secret`.
Recompute each annotated digest; never exempt the entire artifact from scanning.

| Gate | Baseline findings | Next milestone target | Owner |
|---|---:|---|---|
| Size | 418 | ≤417 at first release; no new findings | DankerMu |
| Complexity | 525 | No new or worsened findings | DankerMu |
| Function length | 531 | No new or worsened findings | DankerMu |
| Duplication | 2992 | No new or worsened findings | DankerMu |
| Dead code | 193 | No new findings | DankerMu |
| Naming | 2 | No new findings | DankerMu |
| Secret detector / SAST | 593 / 32 | No new findings; findings are not confirmed vulnerabilities | DankerMu |
| Historical coverage | 1343 measured source files | No regression against frozen measured floors | DankerMu |

## Important Development Notes

- 2026-09-25: Upstream verification workflows originally targeted only `main`;
  preserve validation of `dagent` when absorbing upstream CI updates.
- 2026-09-25: `.omp/` is a local ignored skill installation. Durable checks must
  run on a clean clone without it; the eng-init renderer is a local init tool.

## Conventions

- Use short conventional commit/PR prefixes: `feat:`, `fix:`, `enh:`, `ref:`,
  `chore:`; descriptive task-oriented branch names, not generic agent names.
- New configuration belongs in `src/xagent/config.py`; environment override first,
  computed default second. Keep this module independent of core submodules.
  Update `example.env` and configuration behavior tests with new settings.
- Do not hardcode storage/upload paths. Use config helpers and isolated test env.
- Generated outputs are changed through their generator, not hand-patched.
- Behavior/config/API changes update their owning docs in the same change.
  Mechanical doc checks prove links/commands/registered facts, not semantic truth.
- Exceptions require an existing target, reason and removal condition. Do not add
  fake Known Limitations prose solely to make a doc gate green.

## Code Review Self-Check

- Explain the behavior changed, preserved invariants and nearest alternative.
- Select verification from the actual diff; never shrink scope to hide failures.
- New non-trivial behavior follows red/green/refactor; test behavior, not wiring.
- Record tests run, skips, tool/environment failures and unverified surfaces.
- Preserve the user's work and keep unrelated refactoring/formatting out.

## Architecture Discipline

- Web entry points use `AgentService`; preserve runner/runtime ownership of
  interruption, checkpointing, tool execution, tracing and compaction.
- Modes: `flash` → single_call; `balanced` → ReAct; `think` → DAG;
  `auto` → runtime routing. Builder agents default to memory disabled.
- Memory and KB grounding are separate; definitions belong only in the glossary.
- Model-visible inputs must be reconstructable through the owned trace/log
  contract, with secrets redacted. New capabilities name provider and consumers.
- Registration side effects need owned cleanup; configuration defaults must be
  explicit. Invalid security-sensitive configuration must not silently degrade.
- Deployment tunables go through configuration. Durable schema/version changes
  require explicit compatibility or migration decisions, not hidden fallbacks.
- Invariant companions, typed boundary identifiers and middleware delegation are
  review obligations where applicable, not claims that static lint proves them.

## Critical Paths

Auth, permissions, public API contracts, data deletion, schema migrations,
concurrency and sandbox boundaries require risk-scaled **independent AI review**
and targeted behavioral evidence under `subagent-workflow`. There is no mandatory
human code-review gate; the user explicitly chose functional acceptance only.
Do not replace implementation review with a happy-path screenshot.

## Agent Operating Rules

- Implement one ready issue at a time via the locally installed
  `subagent-workflow`: OpenSpec fixture → fixture review/validation → implementer
  → independent risk-scaled review → adjudication → CI → automatic merge.
- The orchestrator owns fixture, verification and Git/PR integration. One writer
  at a time in the current checkout; no nested delegation or new worktree in that
  workflow. Follow its mechanical two-fix-pass gate; do not invent another loop.
- Humans approve scope and accept functionality. Stop on the workflow's gate lock,
  unresolved intent, missing prerequisites or real infrastructure blockers.
- Ensure `openspec`, authenticated `gh`, required agent roles and skill are installed;
  local documentation does not install them or prove a review took place.
- No production deployment, real-data reset/deletion, force-push, `--no-verify`,
  credential persistence or gate weakening without separate explicit authority.
  Automatic merge is not permission to deploy.
- Decisions live in OpenSpec proposal/design and PR evidence, not parallel ADR trees.
- PRs include runtime evidence with exact commands and limits. Do not claim success
  from a model's self-report: inspect actual files, HTTP responses and task state.

## Enforcement Index

| Control | Authority | Checked by | Level |
|---|---|---|---|
| Existing lint, format and types | `.pre-commit-config.yaml`, `pyproject.toml`, `frontend/tsconfig.json` | `make lint`, `make typecheck` | block |
| L3 thresholds and frozen findings | `constraints.yaml`, `scripts/engineering/check.py` | `make guardrails` | block |
| Coverage report integrity / floors | `scripts/engineering/check.py` | `make coverage` | block |
| Secret scanning / SAST | `scripts/engineering/check.py` | `make security` | block |
| Docs, command mirror and exemptions | `scripts/engineering/check.py` | `make docs-check` | block |
| Gate self-proof | `scripts/test-guardrails.sh` | `make test-guardrails` | block |
| PR size | `constraints.yaml` | `make diff-check BASE=dagent` | block |
| CI aggregation | `.github/workflows/ci.yml`, `.github/workflows/test-migrations.yml` | CI Summary / Migrations Summary | block |
| API / UI / real-model behavior | `tests/e2e/` | `make smoke`, `make verify-ui`, `make verify-real-model` | block when selected |
| Review, TDD evidence, architecture and functionality | `AGENTS.md` | local subagent-workflow and PR evidence | review-only |
| GitHub required checks and branch protection | External setting | Not configured by this initialization | external gap |

Coverage proves execution, not assertion strength. File/link checks do not prove
semantic accuracy. A green local run is not a remote CI result or deployment proof.

## Known Limitations and Deferred Work

- Remote branch protection/default branch and required checks need separate setup.
  GitHub issues are disabled on `DankerMu/Dagent`; enable them separately before
  using that repository as the `subagent-workflow` issue source.
- Real-model proof is local; CI receives no DMX credential from this initialization.
- Historical debt is not repaired by freezing it; unknown baselines block claims.
- Static scanner findings are frozen against the pristine upstream revision.
  Historical coverage floors cover 1051 Python and 292 frontend source files;
  measurement does not establish assertion strength or a green historical suite.
  Local verification does not establish remote CI results or merge eligibility.
  The user approved one oversized initialization PR. Its base commit and every
  changed file's content are bound by `.engineering/bootstrap-approval.json`;
  changed content, extra files or a later base invalidate that approval.
  Security still scans this file: only hex-digest findings in a verified snapshot
  or byte-identical committed approval are treated as public hashes. Editing it
  after landing re-exposes findings; remove an expired approval rather than waive them.
  The normal 400-line limit and all other gates remain unchanged.
  Initial Python type errors and formatting violations have been repaired.
- General PII detection, complete runtime invariants and KB semantic retrieval are
  not established by the engineering scaffold.
- Runtime proofs are local, not production supervision. Interrupted startup before
  its state write can leave an ephemeral Redis process. Reset covers the documented
  db/uploads/storage/lancedb roots, not every materialization or legacy vector path.
