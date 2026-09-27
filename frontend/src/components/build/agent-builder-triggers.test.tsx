import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { builderAgentResponse, builderEmptyMultiSelect, builderEmptySelect, builderNullConnectMcp, builderResourceResponse, createBuilderAppContext, createBuilderToast } from "./agent-builder-test-helpers"
import { testTrigger } from "./agent-triggers-test-helpers"
import { apiRequestMock } from "@/lib/test-api-request-shell"

// Stable references across renders keep useCallback/useMemo dependencies stable.
const translateMock = vi.hoisted(() => (key: string) => key)


vi.mock("@/contexts/app-context-chat", () => {
  const context = createBuilderAppContext()
  return { useApp: () => context }
})

vi.mock("@/contexts/auth-context", () => ({
  useAuth: () => ({ token: "token" }),
}))

vi.mock("@/contexts/i18n-context", () => ({
  useI18n: () => ({
    locale: "en",
    t: translateMock,
  }),
}))


vi.mock("@/components/ui/sonner", () => createBuilderToast())

vi.mock("@/components/layout/resizable-three-column-layout", () => ({
  ResizableThreeColumnLayout: ({ middlePanel, rightPanel }: { middlePanel: React.ReactNode; rightPanel: React.ReactNode }) => (
    <div>
      <div data-testid="middle-panel">{middlePanel}</div>
      <div data-testid="right-panel">{rightPanel}</div>
    </div>
  ),
}))

vi.mock("@/components/task/task-conversation-panel", () => ({
  TaskConversationPanel: () => null,
}))

vi.mock("@/components/build/agent-builder-chat", () => ({
  AgentBuilderChat: () => null,
}))


vi.mock("@/components/mcp/connect-mcp-dialog", () => builderNullConnectMcp)
vi.mock("@/components/ui/multi-select", () => builderEmptyMultiSelect)
vi.mock("@/components/ui/select", () => builderEmptySelect)


import { AgentBuilder } from "./agent-builder"
import type { AgentTrigger } from "@/lib/agent-triggers-api"

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

function makeTrigger(overrides: Partial<AgentTrigger> & { id: number }): AgentTrigger {
  return testTrigger({ webhook_token: "tok", ...overrides })
}

const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"

const triggerAgent = builderAgentResponse(42, {
  name: "Trigger agent",
  visibility: "team",
})


describe("AgentBuilder trigger summary cards", () => {
  const originalWebSocket = globalThis.WebSocket

  beforeEach(() => {
    apiRequestMock.mockReset()
    globalThis.WebSocket = vi.fn() as unknown as typeof WebSocket

    let triggers = [makeTrigger({ id: 9, name: "API / Webhook" })]

    apiRequestMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "http://api.local/api/agents/42") {
        return Promise.resolve(jsonResponse(triggerAgent))
      }
      if (url === TRIGGERS_URL) {
        return Promise.resolve(jsonResponse(triggers))
      }
      if (url === `${TRIGGERS_URL}/9` && init?.method === "PATCH") {
        triggers = triggers.map((item) => ({ ...item, enabled: false }))
        return Promise.resolve(jsonResponse(triggers[0]))
      }
      return Promise.resolve(
        builderResourceResponse(url, { contentType: "application/json" }) ?? jsonResponse({}),
      )
    })
  })

  afterEach(() => {
    cleanup()
    globalThis.WebSocket = originalWebSocket
  })

  it("disables the trigger type in place when its card switch is toggled off", async () => {
    render(<AgentBuilder agentId="42" />)

    // The webhook summary card shows up once the trigger list loads.
    expect(await screen.findByText("triggers.cards.webhook.title")).toBeInTheDocument()

    const cardSwitch = screen
      .getAllByRole("switch")
      .find((el) => el.getAttribute("aria-checked") === "true")
    expect(cardSwitch).toBeDefined()
    fireEvent.click(cardSwitch!)

    // Toggling off patches the trigger directly instead of opening the dialog.
    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        `${TRIGGERS_URL}/9`,
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ enabled: false }),
        }),
      )
    })
    expect(screen.queryByText("triggers.subtitle")).not.toBeInTheDocument()

    // The card disappears after the refreshed summary reports 0 enabled.
    await waitFor(() => {
      expect(screen.queryByText("triggers.cards.webhook.title")).not.toBeInTheDocument()
    })
  })

  it("resyncs the summary via refreshTriggerSummary when a batch disable partially fails", async () => {
    let triggers = [
      makeTrigger({ id: 9, name: "Hook A" }),
      makeTrigger({ id: 10, name: "Hook B" }),
    ]
    let getCallsAfterFailure = 0
    let patchAttempted = false

    apiRequestMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "http://api.local/api/agents/42") {
        return Promise.resolve(jsonResponse(triggerAgent))
      }
      if (url === TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        if (patchAttempted) getCallsAfterFailure += 1
        return Promise.resolve(jsonResponse(triggers))
      }
      if (url === `${TRIGGERS_URL}/9` && init?.method === "PATCH") {
        triggers = triggers.map((item) => (item.id === 9 ? { ...item, enabled: false } : item))
        return Promise.resolve(jsonResponse(triggers[0]))
      }
      if (url === `${TRIGGERS_URL}/10` && init?.method === "PATCH") {
        patchAttempted = true
        return Promise.reject(new Error("boom"))
      }
      return Promise.resolve(builderResourceResponse(url))
    })

    render(<AgentBuilder agentId="42" />)

    const cardSwitch = (
      await screen.findAllByRole("switch")
    ).find((el) => el.getAttribute("aria-checked") === "true")
    expect(cardSwitch).toBeDefined()
    fireEvent.click(cardSwitch!)

    // One PATCH in the batch rejected: disableTriggerType's catch resyncs via
    // refreshTriggerSummary (a fresh GET) instead of trusting the optimistic
    // merge, which would otherwise wrongly report both hooks disabled.
    await waitFor(() => {
      expect(getCallsAfterFailure).toBeGreaterThan(0)
    })
  })
})

describe("AgentBuilder trigger summary cards (agent not created yet)", () => {
  const originalWebSocket = globalThis.WebSocket

  beforeEach(() => {
    apiRequestMock.mockReset()
    globalThis.WebSocket = vi.fn() as unknown as typeof WebSocket

    apiRequestMock.mockImplementation((url: string) =>
      Promise.resolve(
        builderResourceResponse(url, { contentType: "application/json" }) ?? jsonResponse({}),
      ),
    )
  })

  afterEach(() => {
    cleanup()
    globalThis.WebSocket = originalWebSocket
  })

  it("disables a staged trigger type in place, without any network call, before the agent exists", async () => {
    render(<AgentBuilder />)

    // Stage an enabled webhook trigger via the dialog (no agentId yet, so
    // creation only touches the parent-owned staged list, no API call).
    fireEvent.click(await screen.findByText("triggers.builder.open"))
    await screen.findByText("triggers.cards.webhook.title")
    const [webhookCardSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookCardSwitch)
    // Toggle-on opens a draft editor; Save is what stages it (enabled).
    await screen.findByLabelText("triggers.form.name")
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))
    fireEvent.click(await screen.findByRole("button", { name: "common.done" }))

    // The summary card appears once the staged trigger is enabled.
    expect(await screen.findByText("triggers.cards.webhook.title")).toBeInTheDocument()
    apiRequestMock.mockClear()

    const cardSwitch = screen
      .getAllByRole("switch")
      .find((el) => el.getAttribute("aria-checked") === "true")
    expect(cardSwitch).toBeDefined()
    fireEvent.click(cardSwitch!)

    // disableTriggerType's `!localAgentId` branch patches stagedTriggers
    // directly — no PATCH/GET to any trigger endpoint.
    await waitFor(() => {
      expect(screen.queryByText("triggers.cards.webhook.title")).not.toBeInTheDocument()
    })
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/triggers"),
      expect.anything(),
    )
  })
})
