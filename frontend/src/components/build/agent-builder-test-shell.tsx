import React from "react"
import { vi } from "vitest"
import { mockApiWrapper } from "@/lib/test-api-wrapper"
import {
  BuilderMiddlePanel,
  BuilderOptionsSelect,
  builderNullConnectMcp,
  builderRegularAuth,
  createBuilderAppContext,
  createBuilderI18n,
  createBuilderToast,
  type BuilderAuth,
  type BuilderI18n,
  type BuilderMultiSelectProps,
} from "./agent-builder-test-helpers"

export const apiRequestMock = vi.fn()
vi.mock("@/lib/api-wrapper", () => mockApiWrapper(apiRequestMock))

type BuilderShellOptions = {
  auth: () => BuilderAuth
  i18n: BuilderI18n
  connectMcp: React.ComponentType<{ open: boolean }>
  multiSelect: React.ComponentType<BuilderMultiSelectProps>
}

const defaults: BuilderShellOptions = {
  auth: builderRegularAuth.useAuth,
  i18n: createBuilderI18n(),
  connectMcp: builderNullConnectMcp.ConnectMcpDialog,
  multiSelect: BuilderOptionsSelect,
}
let shellOptions = defaults

// Each suite chooses only the seams it exercises. Factories delegate at call
// time because AgentBuilder's static import evaluates before suite setup.
export function configureBuilderTestShell(options: Partial<BuilderShellOptions> = {}) {
  shellOptions = { ...defaults, ...options }
}

export const builderToastErrorMock = vi.fn()
const builderToast = createBuilderToast(builderToastErrorMock)
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => shellOptions.auth() }))
vi.mock("@/contexts/i18n-context", () => ({ useI18n: () => shellOptions.i18n }))
vi.mock("sonner", () => builderToast)
vi.mock("@/components/mcp/connect-mcp-dialog", () => ({
  ConnectMcpDialog: (props: { open: boolean }) =>
    React.createElement(shellOptions.connectMcp, props),
}))
vi.mock("@/components/ui/multi-select", () => ({
  MultiSelect: (props: BuilderMultiSelectProps) =>
    React.createElement(shellOptions.multiSelect, props),
}))

// The builder suites that exercise the editor rather than its preview
// share inert neighboring panels and the ordinary builder context.
vi.mock("@/contexts/app-context-chat", () => ({
  useApp: () => createBuilderAppContext(),
}))
vi.mock("@/components/layout/resizable-three-column-layout", () => ({
  ResizableThreeColumnLayout: BuilderMiddlePanel,
}))
vi.mock("@/components/task/task-conversation-panel", () => ({
  TaskConversationPanel: () => null,
}))
vi.mock("@/components/build/agent-builder-chat", () => ({
  AgentBuilderChat: () => null,
}))
