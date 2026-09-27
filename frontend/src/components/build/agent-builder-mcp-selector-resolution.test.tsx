import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { builderAgentResponse, builderEmptyMultiSelect, builderEmptySelect, builderResourceResponse } from "./agent-builder-test-helpers"
import { apiRequestMock, configureBuilderTestShell } from "./agent-builder-test-shell"

vi.mock("@/components/ui/select", () => builderEmptySelect)

import { AgentBuilder } from "./agent-builder"

const AGENT_ID = "5"
const initialSelector = "mcp:Local Server"
const resolvedSelector = "mcp:local-server"

function agentResponse(toolCategories: string[]) {
  return builderAgentResponse(AGENT_ID, {
    instructions: "Do the thing",
    models: { general: 1, small_fast: null, visual: null, compact: null },
    tool_categories: toolCategories,
    readonly: false,
  })
}

let putBody: { tool_categories: string[] } | undefined
const originalWebSocket = globalThis.WebSocket

beforeEach(() => {
  putBody = undefined
  apiRequestMock.mockReset()
  configureBuilderTestShell({ multiSelect: builderEmptyMultiSelect.MultiSelect })
  globalThis.WebSocket = vi.fn() as unknown as typeof WebSocket
  apiRequestMock.mockImplementation((url: string, init?: RequestInit) => {
    const resource = builderResourceResponse(url, {
      agentId: AGENT_ID, mcpServers: [{ id: 1, name: "local-server" }],
    })
    if (resource) return Promise.resolve(resource)
    if (url.endsWith(`/api/agents/${AGENT_ID}`) && init?.method === "PUT") {
      putBody = JSON.parse(init.body as string)
      return Promise.resolve(new Response(JSON.stringify(agentResponse(putBody!.tool_categories))))
    }
    if (url.endsWith(`/api/agents/${AGENT_ID}`)) {
      return Promise.resolve(new Response(JSON.stringify(agentResponse([initialSelector]))))
    }
    return Promise.resolve(new Response(JSON.stringify({})))
  })
})

afterEach(() => {
  cleanup()
  globalThis.WebSocket = originalWebSocket
})

describe("AgentBuilder custom MCP selector", () => {
  it("saves the connected server name and clears unsaved changes after success", async () => {
    render(<AgentBuilder agentId={AGENT_ID} />)

    const nameInput = await screen.findByPlaceholderText("builds.configForm.name.placeholder")
    await waitFor(() => expect(nameInput).toHaveValue("Some Agent"))
    // The connector card displays the canonical row name only after its
    // mount-time MCP request has committed, not merely after agent load.
    expect(await screen.findByText("local-server")).toBeInTheDocument()
    expect(screen.queryByText("tools.mcp.mcpUnavailable")).not.toBeInTheDocument()
    const updateButton = screen.getByRole("button", { name: "builds.editor.header.update" })
    expect(updateButton).toBeDisabled()

    fireEvent.change(nameInput, { target: { value: "Some Agent Renamed" } })
    await waitFor(() => expect(updateButton).not.toBeDisabled())
    fireEvent.click(updateButton)

    await waitFor(() => expect(putBody).toBeDefined())
    expect(putBody?.tool_categories).toContain(resolvedSelector)
    expect(putBody?.tool_categories).not.toContain(initialSelector)
    await waitFor(() => expect(updateButton).toBeDisabled())
  })
})
