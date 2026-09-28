# Dagent Project Context

## Project Identity

“对本项目做改动，同时吸收上游项目的更新”. Dagent is a customized fork of
xorbitsai/xagent. The product remains a web-accessible Agent framework; this
initialization does not rename Python packages or change its execution modes.
Engineering rules and branch policy have one home: `AGENTS.md`.

## Domain Language

Confirmed definitions live only in `openspec/glossary.md`. Consult that file before
naming concepts. In particular, do not treat memory and KB grounding as synonyms.

## Bounded Contexts

| Context | Owns | Boundary / forbidden logic |
|---|---|---|
| Web and identity | HTTP/WebSocket, authentication and user-scoped access | Validate the actor at the boundary; do not trust client-provided ownership |
| Agent execution | `core/agent/service.py`, `runner.py`, `runtime.py`, patterns | Entry points use AgentService; do not add an alternate runtime |
| Tools and workspace | Tool discovery, controlled execution, task files | Use configured paths and authoritative execution scope, not ad-hoc user directories |
| Memory | `core/memory/`, `web/dynamic_memory_store.py` | Keep user isolation; do not route KB collections through the memory store |
| KB/RAG | `core/tools/core/RAG_tools/`, `web/api/kb.py` | Grounding and ingestion follow KB access control and their storage layer |
| Persistence | `web/models/`, `migrations/` | Schema changes require migration and real-database evidence |
| Frontend | `frontend/src/` | UI consumes server contracts; UI hiding is not authorization |

## Core Invariants

- `src/xagent/core/agent/` is the sole runtime. AgentService is the web-facing facade.
- Existing execution modes and builder memory defaults are preserved by this init.
- Shared configuration is owned by `src/xagent/config.py`, which cannot depend on
  core submodules. Environment overrides precede computed defaults.
- Storage roots, uploads and external upload roots are configuration, not literals
  to duplicate in production or tests. Use isolated environment overrides in tests.
- Ownership/authorization checks apply to operations, not merely list visibility.
- Never claim a model or tool completed work solely from its response text; verify
  the task state, resulting data and effects required by the acceptance contract.
- Provider prompt caching depends on stable leading content. Keep agent, file,
  pattern and shared language/time policies in the leading system message; emit
  live time/request/skill/memory evidence in a final system message after complete
  history/tool exchanges. Both retain system authority; do not persist synthetic
  prompt messages into conversation history. Provider support for multiple system
  messages is required. Cache availability and hit ratios remain provider-dependent;
  never replace real calls with cached answers or pad prompts to inflate hit rates.
  ReAct's existing day-level date still changes the prefix at local midnight.

## Public Interfaces and Contracts

| Surface | Authority | Evidence / compatibility |
|---|---|---|
| Web API and WebSocket | `src/xagent/web/api/`, `src/xagent/web/schemas/` | Existing web and E2E suites; additive changes do not imply authorization safety |
| Public versioned API | `src/xagent/web/api/v1/` mounted at `/v1` | Preserve public contract or make a deliberate versioned change |
| Task lifecycle | `src/xagent/web/models/task.py`, `src/xagent/core/agent/runner.py` | Completion, interruption and checkpoint transitions need observable evidence |
| Interaction lifecycle | `src/xagent/web/models/task_interaction.py` | State transitions and concurrent ownership are critical review surfaces |
| Database schema | `src/xagent/migrations/` | Existing SQLite and PostgreSQL migration workflows |
| Tool discovery | `src/xagent/core/tools/` | Preserve existing `get_<tool>_tool` discovery where used; do not invent registration paths |
| Skills loading | `src/xagent/skills/README.md` | Built-in, user, external ordering remains unchanged |

## Implicit Dependencies

- The backend may use SQLite/PostgreSQL, Redis, vector storage, provider APIs and
  sandbox services; installing Python packages does not prove those services work.
- Tests have root fixtures and optional/network markers. Read their fixture scope
  before claiming integration coverage; selected capability proofs must not skip.
- The frontend static export is served by the backend through the configured
  frontend-dist path. Component tests in jsdom are not browser E2E evidence.
- Observability includes stdlib logging and optional Langfuse/OTel. Tests must not
  inherit real tracing credentials or transmit test input to unrelated services.
- `.omp/` is ignored local tooling. A clean clone needs a separate Oh My Pi skill
  installation for subagent-workflow; committed checks must not depend on it.

## Forbidden Logic & Irreversible Operations

Do not deploy to production, reset/delete real data, force-push, bypass checks,
persist credentials or weaken an acceptance gate without separate explicit
approval. Automatic PR merge does not authorize deployment. Test resets must
validate ownership of disposable storage and never accept arbitrary production URLs.

## Open Terminology Questions

The initial five terms are confirmed. Workforce, Run and Interaction have existing
code-level meanings; confirm their domain definitions when a change needs them,
then add them to the glossary rather than guessing or defining them here.

## Known Limitations and Deferred Work

The engineering initialization is not a product architecture rewrite. Full
cross-module runtime invariant coverage, semantic KB retrieval with a real embedding
provider, remote governance settings and production readiness require their own
scoped evidence. Historical findings remain debt until actually removed.
