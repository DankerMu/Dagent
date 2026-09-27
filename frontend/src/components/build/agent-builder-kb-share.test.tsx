import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { builderAgentResponse, builderEmptyMultiSelect, builderOpenConnectMcp, builderResourceResponse, createBuilderToast } from "./agent-builder-test-helpers"
import { apiRequestMock, configureBuilderTestShell } from "./agent-builder-test-shell"

// Regression coverage for handleShareConnectorsAndContinue's KB-promotion 202
// handling: a 202 body that fails isBackgroundJobResponse used to fall through
// silently into the agent-promotion retry, which could re-promote the agent
// while the KB was still personal. The fix throws instead. This test proves
// that (a) the user sees an error toast and (b) the agent promote-team
// endpoint is never called a second time (only the original 422 attempt that
// opened the share dialog).

const toastErrorMock = vi.hoisted(() => vi.fn())

// Mocking @/components/ui/sonner directly (rather than the underlying "sonner"
// package) sidesteps the { duration } second arg toast.error appends, so
// assertions below can check call[0] without worrying about it.
vi.mock("@/components/ui/sonner", () => createBuilderToast(toastErrorMock))

// Radix selects aren't drivable with fireEvent in jsdom, so render the
// ownership control as a native <select> instead. SelectTrigger/SelectValue/
// SelectContent just need to pass their children through so SelectItem's
// <option> nodes land inside the native <select>.
vi.mock("@/components/ui/select", () => ({
  // The custom dropdown (model pickers etc.) is unrelated to this flow; a
  // no-op stub matches the admin-mcp sibling test's approach.
  Select: () => null,
  SelectRadix: ({
    value,
    onValueChange,
    children,
  }: {
    value?: string
    onValueChange: (value: string) => void
    children: React.ReactNode
  }) => (
    <select value={value} onChange={(e) => onValueChange(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: React.ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}))

// The real Radix Dialog is awkward to drive in jsdom; render children
// unconditionally-gated on `open` like sibling tests do (e.g.
// connect-mcp-dialog.test.tsx).
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h1>{children}</h1>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))


// Unrelated to the KB-share flow. It doesn't import React and this project's
// vitest config has no jsx-automatic-runtime plugin, so rendering it for real
// with inTeam: true (required below) throws "React is not defined" -- a
// pre-existing gap unrelated to the fix under test. Stub it out like the
// other sibling panels above.
vi.mock("@/components/build/agent-ssh-bindings", () => ({
  AgentSshBindings: () => null,
}))

import { AgentBuilder } from "./agent-builder"

const AGENT_ID = "5"

function agentResponse() {
  // handleCreate's validation requires the persisted general model.
  return builderAgentResponse(AGENT_ID, {
    instructions: "Do the thing",
    models: { general: 1, small_fast: null, visual: null, compact: null },
    readonly: false,
  })
}

// Routes every apiRequest call by URL. /api/agents/5 is used for both the
// mount-time GET and the save PUT, and both return the same agent shape, so
// the request method doesn't need to be inspected here.
function installApi() {
  apiRequestMock.mockImplementation((url: string) => {
    const resource = builderResourceResponse(url, { agentId: AGENT_ID })
    if (resource) return Promise.resolve(resource)
    // Original 422 that opens the share-connectors dialog. The knowledge-base
    // entry must satisfy sanitizeUnsharedKnowledgeBases (a non-empty `name`
    // string) or the dialog never opens.
    if (url.endsWith(`/api/agents/${AGENT_ID}/promote-team`))
      return Promise.resolve(
        new Response(
          JSON.stringify({ detail: { unshared_knowledge_bases: [{ name: "demo" }] } }),
          { status: 422 },
        ),
      )

    // The bug under test: 202 with a body that fails isBackgroundJobResponse.
    if (url.endsWith("/api/knowledge-bases/demo/promote-team"))
      return Promise.resolve(
        new Response(JSON.stringify({ not_a_valid_job: true }), { status: 202 }),
      )

    if (url.endsWith(`/api/agents/${AGENT_ID}`))
      return Promise.resolve(new Response(JSON.stringify(agentResponse()), { status: 200 }))

    return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
  })
}

const promoteTeamCalls = () =>
  apiRequestMock.mock.calls
    .map(([u]) => String(u))
    .filter((u) => u === "http://api.local/api/agents/5/promote-team")

describe("AgentBuilder KB-promotion 202 handling", () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    // inTeam: true is required for the ownership control to render at all.
    configureBuilderTestShell({
      auth: () => ({
        token: "token",
        user: { id: "1", is_admin: false },
        inTeam: true,
        teamRole: "member",
      }),
      connectMcp: builderOpenConnectMcp.ConnectMcpDialog,
      multiSelect: builderEmptyMultiSelect.MultiSelect,
    })
    toastErrorMock.mockReset()
    installApi()
    ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = vi.fn()
  })

  afterEach(() => cleanup())

  it("surfaces an error and does not re-attempt agent promotion when the KB 202 body is not a valid job", async () => {
    render(<AgentBuilder agentId={AGENT_ID} />)

    // Wait for the agent load to finish before touching the ownership select,
    // otherwise loadAgent's setOwnership(agent.team_id == null ? ...) would
    // stomp our selection once the fetch resolves.
    await waitFor(() =>
      expect(screen.getByPlaceholderText("builds.configForm.name.placeholder")).toHaveValue(
        "Some Agent",
      ),
    )

    // Only the ownership select exists until ownership === "team".
    const ownershipSelect = document.querySelector("select") as HTMLSelectElement
    expect(ownershipSelect).not.toBeNull()
    fireEvent.change(ownershipSelect, { target: { value: "team" } })

    const updateButton = await screen.findByRole("button", {
      name: "builds.editor.header.update",
    })
    fireEvent.click(updateButton)

    // The 422 from the first promote-team attempt opens the share dialog.
    await screen.findByText("builds.configForm.connectorNotShared.title")
    expect(promoteTeamCalls()).toHaveLength(1)

    const shareButton = await screen.findByRole("button", {
      name: "builds.configForm.connectorNotShared.shareAndContinue",
    })
    fireEvent.click(shareButton)

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalled())
    // toast.error's message arg (call[0]) should reflect the unknown-error
    // fallback the fix throws; the second arg is a { duration } object added
    // by the real @/components/ui/sonner wrapper, irrelevant here since it's
    // mocked away.
    expect(toastErrorMock.mock.calls[0][0]).toBe("builds.editor.error.unknown")

    // The unreadable 202 body must abort before reconcileOwnership runs again,
    // so the agent promote-team endpoint is still only called once (the
    // original 422 attempt) -- never a second, silently-continued retry.
    expect(promoteTeamCalls()).toHaveLength(1)
  })
})
