import React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { builderAgentResponse, builderEmptyMultiSelect, builderOpenConnectMcp, builderResourceResponse } from "./agent-builder-test-helpers"
import { apiRequestMock, configureBuilderTestShell } from "./agent-builder-test-shell"

// Exercises the admin cross-user MCP-list path in AgentBuilder's edit-mode
// loadAgent effect: an admin opening another user's agent must fetch that
// owner's MCP servers (?user_id=), a self-owned agent must not, and a load that
// is torn down mid-flight must not fire the owner fetch (active cleanup flag).

const authUser = { current: { id: "1", is_admin: true } }

import { AgentBuilder } from "./agent-builder"

const AGENT_ID = "5"

function agentResponse(ownerId: number, canEdit = true) {
  return builderAgentResponse(AGENT_ID, {
    user_id: ownerId,
    tool_categories: ["mcp:foo"],
    readonly: !canEdit,
    can_edit: canEdit,
  })
}

// Base handler for the mount-time fetchData resources + agent detail. `ownerId`
// controls who owns the loaded agent; `agentPromise` lets a test defer the agent
// response to drive the mid-flight teardown case.
function installApi(
  ownerId: number,
  opts: { agentPromise?: Promise<Response>; canEdit?: boolean } = {}
) {
  apiRequestMock.mockImplementation((url: string) => {
    const resource = builderResourceResponse(url, { agentId: AGENT_ID })
    if (resource) return Promise.resolve(resource)
    if (url.endsWith(`/api/agents/${AGENT_ID}`))
      return opts.agentPromise ??
        Promise.resolve(
          new Response(JSON.stringify(agentResponse(ownerId, opts.canEdit ?? true)), {
            status: 200,
          })
        )
    return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
  })
}

const mcpCalls = () =>
  apiRequestMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/api/mcp/servers"))

describe("AgentBuilder admin cross-user MCP list", () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    configureBuilderTestShell({
      auth: () => ({ token: "token", user: authUser.current }),
      connectMcp: builderOpenConnectMcp.ConnectMcpDialog,
      multiSelect: builderEmptyMultiSelect.MultiSelect,
    })
    authUser.current = { id: "1", is_admin: true }
    ;(globalThis as any).WebSocket = vi.fn()
  })

  afterEach(() => cleanup())

  it("fetches the owner's MCP servers when an admin opens another user's agent", async () => {
    installApi(99) // agent owned by user 99, admin is user 1
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() =>
      expect(mcpCalls()).toContain("http://api.local/api/mcp/servers?user_id=99")
    )
  })

  it("does not scope the MCP fetch when an admin opens their own agent", async () => {
    installApi(1) // agent owned by the admin themselves
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => expect(mcpCalls()).toContain("http://api.local/api/mcp/servers"))
    expect(mcpCalls().some((u) => u.includes("user_id="))).toBe(false)
  })

  it("locks the builder read-only and hides Save when can_edit is false", async () => {
    installApi(99, { canEdit: false }) // admin opening a read-only agent
    const { container } = render(<AgentBuilder agentId={AGENT_ID} />)

    // Read-only badge appears once the agent detail loads.
    await screen.findByText("builds.editor.header.readOnly")
    // The Save/Publish buttons are gone, so there's no "edits but save fails" trap.
    expect(screen.queryByText("builds.editor.header.update")).toBeNull()
    expect(screen.queryByText("builds.editor.header.create")).toBeNull()
    expect(screen.queryByText("builds.editor.header.publish")).toBeNull()

    // The form is actually locked, not just badge-swapped: native fields are
    // disabled via the fieldset and the instructions editor drops contentEditable.
    expect(screen.getByPlaceholderText("builds.configForm.name.placeholder")).toBeDisabled()
    const editor = container.querySelector('div[role="textbox"]')
    expect(editor).not.toBeNull()
    expect(editor).toHaveAttribute("contenteditable", "false")
    expect(screen.queryByTestId("connect-mcp-dialog")).toBeNull()
  })

  it("does not fire the owner-scoped fetch if unmounted before the agent load resolves", async () => {
    let resolveAgent: (r: Response) => void = () => {}
    const agentPromise = new Promise<Response>((resolve) => {
      resolveAgent = resolve
    })
    installApi(99, { agentPromise })

    const { unmount } = render(<AgentBuilder agentId={AGENT_ID} />)
    unmount()
    // Agent detail resolves only after teardown; the active flag must gate it.
    resolveAgent(new Response(JSON.stringify(agentResponse(99)), { status: 200 }))
    await Promise.resolve()
    await Promise.resolve()

    expect(mcpCalls().some((u) => u.includes("user_id=99"))).toBe(false)
  })
})
