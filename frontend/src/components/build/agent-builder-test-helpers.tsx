import React from "react"
import { vi, type Mock } from "vitest"
// Every builder suite imports this module before AgentBuilder. Register the
// invariant boundary mocks once; suites register only the seams they exercise.
vi.mock("@/lib/utils", () => mockBuilderUtils())
vi.mock("@/lib/branding", () => builderBranding)
vi.mock("next/navigation", () => builderNavigation)
vi.mock("@/components/kb/knowledge-base-creation-dialog", () => ({
  KnowledgeBaseCreationDialog: () => null,
}))
vi.mock("@/components/chat/FileMentionDropdown", () => ({ FileMentionDropdown: () => null }))
vi.mock("@/hooks/use-file-mention", () => ({ useFileMention: createFileMentionState }))
vi.mock("@/components/build/build-file-preview-sheet", () => ({
  BuildFilePreviewSheet: () => null,
}))

export function createBuilderAppContext(callbacks: Record<string, Mock> = {}) {
  return {
    state: {
      messages: [],
      traceEvents: [],
      currentTask: null,
      isProcessing: false,
      isHistoryLoading: false,
      taskId: null,
      filePreview: { isOpen: false },
      dagExecution: null,
      steps: [],
    },
    setTaskId: callbacks.setTaskId ?? vi.fn(),
    sendMessage: callbacks.sendMessage ?? vi.fn(),
    dispatch: callbacks.dispatch ?? vi.fn(),
    closeFilePreview: callbacks.closeFilePreview ?? vi.fn(),
    pauseTask: vi.fn(),
    resumeTask: vi.fn(),
    openFilePreview: vi.fn(),
    requestStatus: vi.fn(),
  }
}

function createFileMentionState() {
  return {
    checkTrigger: vi.fn(),
    isOpen: false,
    items: [],
    selectedIndex: 0,
    selectItem: vi.fn(),
    close: vi.fn(),
  }
}

const builderBranding = {
  getBrandingFromEnv: () => ({ appName: "Xagent" }),
}

const builderNavigation = {
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}

export interface BuilderAuth {
  token: string
  user?: { id: string; is_admin: boolean }
  inTeam?: boolean
  teamRole?: string
}

export interface BuilderI18n {
  locale: string
  t: (key: string, vars?: Record<string, string>) => string
}

export interface BuilderMultiSelectProps {
  placeholder?: string
  options?: Array<{ value: string }>
}

export const builderRegularAuth = {
  useAuth: () => ({ token: "token", user: { id: "1", is_admin: false } }),
}

// The builder/SSH effects depend on `t`; mirror the real provider's stable identity.
const stableBuilderI18n = {
  locale: "en",
  t: (key: string, vars?: Record<string, string>) =>
    vars?.appName ? `${key}:${vars.appName}` : key,
}

export function createBuilderI18n(): BuilderI18n {
  return stableBuilderI18n
}

export function createBuilderToast(error = vi.fn()) {
  return { toast: { error, success: vi.fn() } }
}

export function BuilderMiddlePanel({ middlePanel }: { middlePanel: React.ReactNode }) {
  return <div>{middlePanel}</div>
}

export function BuilderOptionsSelect({
  placeholder,
  options,
}: BuilderMultiSelectProps) {
  return (
    <div data-testid="multi-select" data-placeholder={placeholder}>
      {(options ?? []).map((option) => option.value).join("|")}
    </div>
  )
}

export const builderNullConnectMcp = { ConnectMcpDialog: () => null }
export const builderOpenConnectMcp = {
  ConnectMcpDialog: ({ open }: { open: boolean }) => (
    <output data-testid="connect-mcp-dialog">{String(open)}</output>
  ),
}
export const builderEmptyMultiSelect = { MultiSelect: () => null }
export const builderEmptySelect = { Select: () => null }

async function mockBuilderUtils() {
  const actual = await vi.importActual<Record<string, unknown>>("@/lib/utils")
  return {
    ...actual,
    getApiUrl: () => "http://api.local",
    getUploadApiUrl: () => "http://api.local",
    getWsUrl: () => "ws://api.local",
  }
}

export function builderAgentResponse(agentId: string | number, overrides: Record<string, unknown> = {}) {
  return {
    id: Number(agentId),
    user_id: 1,
    team_id: null,
    name: "Some Agent",
    description: "",
    instructions: "",
    execution_mode: "balanced",
    models: null,
    knowledge_bases: [],
    skills: [],
    tool_categories: [],
    suggested_prompts: [],
    logo_url: null,
    status: "draft",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    widget_enabled: false,
    allowed_domains: [],
    share_enabled: false,
    share_updated_at: null,
    can_edit: true,
    ...overrides,
  }
}

export function builderResourceResponse(
  url: string,
  options: {
    agentId?: string | number
    skills?: unknown
    tools?: unknown
    models?: unknown
    userDefaults?: unknown
    mcpServers?: unknown
    contentType?: string
  } = {},
): Response | undefined {
  let body: unknown
  if (url.endsWith("/api/kb/collections")) body = { collections: [] }
  else if (url.endsWith("/api/skills/")) body = options.skills ?? []
  else if (url.endsWith("/api/tools/available")) body = options.tools ?? { tools: [] }
  else if (url.endsWith("/api/models/?category=llm")) body = options.models ?? []
  else if (url.endsWith("/api/models/user-default")) body = options.userDefaults ?? []
  else if (options.agentId !== undefined && url.includes(`/api/agents/${options.agentId}/triggers`)) body = []
  else if (url.includes("/api/mcp/servers")) body = options.mcpServers ?? []
  else return undefined
  return new Response(JSON.stringify(body), {
    status: 200,
    ...(options.contentType ? { headers: { "Content-Type": options.contentType } } : {}),
  })
}
