import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { builderAgentResponse, builderEmptySelect, builderResourceResponse } from "./agent-builder-test-helpers"
import { apiRequestMock, configureBuilderTestShell } from "./agent-builder-test-shell"

// Issue #976: the remove-logo button should let a logo be cleared without a
// replacement, and issue #975's cache-busting fix must not mark an agent
// dirty for a logo it never had (upload-then-remove on a logo-less agent).

vi.mock("@/components/ui/select", () => builderEmptySelect)

import { AgentBuilder } from "./agent-builder"

const AGENT_ID = "5"

function agentResponse(logoUrl: string | null) {
  return builderAgentResponse(AGENT_ID, {
    name: "Logo Test Agent",
    instructions: "You are a test agent.",
    models: { general: "10" },
    logo_url: logoUrl,
  })
}

function installApi(logoUrl: string | null) {
  apiRequestMock.mockImplementation((url: string, opts?: { method?: string; body?: string }) => {
    if (opts?.method === "PUT") {
      const body = JSON.parse(opts.body || "{}")
      const nextLogoUrl = body.logo_base64 === "" ? null : logoUrl
      return Promise.resolve(
        new Response(JSON.stringify(agentResponse(nextLogoUrl)), { status: 200 })
      )
    }
    const resource = builderResourceResponse(url, { agentId: AGENT_ID })
    if (resource) return Promise.resolve(resource)
    if (url.endsWith(`/api/agents/${AGENT_ID}`))
      return Promise.resolve(new Response(JSON.stringify(agentResponse(logoUrl)), { status: 200 }))
    return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
  })
}

const updateButton = () => screen.getByText("builds.editor.header.update")
const logoFileInput = (container: HTMLElement) =>
  container.querySelector('input[type="file"][accept="image/*"]') as HTMLInputElement
const removeLogoButton = () =>
  screen.queryByRole("button", { name: "builds.configForm.logo.remove" })
const pngFile = (name: string) => new File(["fake-bytes"], name, { type: "image/png" })

beforeEach(() => {
  apiRequestMock.mockReset()
  configureBuilderTestShell()
  ;(globalThis as any).WebSocket = vi.fn()
})

afterEach(() => cleanup())

describe("AgentBuilder logo removal (issue #976)", () => {
  it("does not show a remove button when the agent has no logo", async () => {
    installApi(null)
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() =>
      expect(screen.getByDisplayValue("Logo Test Agent")).toBeInTheDocument()
    )
    expect(removeLogoButton()).toBeNull()
  })

  it("clears an existing logo and sends logo_base64: '' on save", async () => {
    installApi("/uploads/agent_logos/agent_5_abcd1234.png")
    const { container } = render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => expect(removeLogoButton()).toBeTruthy())
    expect(updateButton()).toBeDisabled()

    fireEvent.click(removeLogoButton()!)

    // Removing a logo the agent actually had is a real change.
    expect(updateButton()).not.toBeDisabled()
    expect(removeLogoButton()).toBeNull()

    fireEvent.click(updateButton())

    await waitFor(() => {
      const putCall = apiRequestMock.mock.calls.find(
        ([, opts]) => (opts as any)?.method === "PUT"
      )
      expect(putCall).toBeTruthy()
      const body = JSON.parse((putCall![1] as any).body)
      expect(body.logo_base64).toBe("")
    })

    void container
  })

  it("does not enable Update for upload-then-remove on a logo-less agent", async () => {
    installApi(null)
    const { container } = render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() =>
      expect(screen.getByDisplayValue("Logo Test Agent")).toBeInTheDocument()
    )
    expect(updateButton()).toBeDisabled()

    fireEvent.change(logoFileInput(container), { target: { files: [pngFile("logo.png")] } })
    await waitFor(() => expect(removeLogoButton()).toBeTruthy())
    expect(updateButton()).not.toBeDisabled()

    fireEvent.click(removeLogoButton()!)

    // Net effect is a no-op: this agent never had a logo to begin with.
    expect(updateButton()).toBeDisabled()
  })
})
