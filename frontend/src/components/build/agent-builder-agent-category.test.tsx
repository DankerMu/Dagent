import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { resolveTranslation, type TranslationKey } from "@/i18n/translations"
import { builderAgentResponse, builderEmptySelect, builderResourceResponse } from "./agent-builder-test-helpers"
import { apiRequestMock, builderToastErrorMock, configureBuilderTestShell } from "./agent-builder-test-shell"

// Issue #802: the `agent` tool category (multi-agent delegation) is a
// Workforce concern and must not be assignable from the agent builder.
// Covers the two frontend guarantees: the category never appears in the
// tool selector, and a legacy agent that still has it saved neither
// shows it as selected nor writes it back on save.

vi.mock("@/components/ui/select", () => builderEmptySelect)

import { AgentBuilder } from "./agent-builder"

const AGENT_ID = "5"

const AVAILABLE_TOOLS = [
  { name: "calculator", description: "", category: "basic", enabled: true },
  { name: "retired_search", description: "", category: "web_search", enabled: true },
  { name: "agent_7", description: "", category: "agent", enabled: true },
  { name: "misc_tool", description: "", category: "other", enabled: true },
]

function agentResponse(toolCategories: string[]) {
  return builderAgentResponse(AGENT_ID, {
    name: "Legacy Agent",
    instructions: "You are a legacy agent.",
    models: { general: "10" },
    tool_categories: toolCategories,
  })
}

function installApi(
  toolCategories: string[],
  putResponse: () => Response = () =>
    new Response(JSON.stringify(agentResponse(toolCategories)), { status: 200 }),
  toolsBody: unknown = { tools: AVAILABLE_TOOLS },
  skills: unknown[] = []
) {
  apiRequestMock.mockImplementation((url: string, opts?: { method?: string }) => {
    if (opts?.method === "PUT")
      return Promise.resolve(putResponse())
    const resource = builderResourceResponse(url, {
      agentId: AGENT_ID, skills, tools: toolsBody,
    })
    if (resource) return Promise.resolve(resource)
    if (url.endsWith(`/api/agents/${AGENT_ID}`))
      return Promise.resolve(
        new Response(JSON.stringify(agentResponse(toolCategories)), { status: 200 })
      )
    return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
  })
}

const toolCategorySelector = () =>
  screen
    .getAllByTestId("multi-select")
    .find((el) => el.getAttribute("data-placeholder") === "builds.configForm.tools.placeholder")

beforeEach(() => {
  apiRequestMock.mockReset()
  // Render each MultiSelect's option values so tests can assert on what the
  // tool-category selector actually offers (the shell's default fixture).
  configureBuilderTestShell({
    i18n: {
      locale: "en",
      t: (key: string, vars?: Record<string, string>) =>
        key === "builds.configForm.tools.alwaysAvailable"
          ? resolveTranslation("en", key as TranslationKey, vars)
          : vars?.appName ? `${key}:${vars.appName}` : key,
    },
  })
  builderToastErrorMock.mockReset()
  ;(globalThis as any).WebSocket = vi.fn()
})

afterEach(() => cleanup())

describe("AgentBuilder agent tool category (issue #802)", () => {
  it("does not offer unassignable categories even when such tools exist", async () => {
    installApi(["basic"])
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => {
      const selector = toolCategorySelector()
      expect(selector).toBeTruthy()
      expect(selector!.textContent).toContain("basic")
    })
    const offered = toolCategorySelector()!.textContent!.split("|")
    expect(offered).not.toContain("agent")
    expect(offered).not.toContain("other")
    expect(offered).not.toContain("web_search")
  })

  it("saves a legacy agent without writing the agent category back", async () => {
    installApi(["basic", "agent", "web_search"])
    render(<AgentBuilder agentId={AGENT_ID} />)

    // Wait for the agent to load (name lands in the header input).
    await waitFor(() =>
      expect(
        screen.getByPlaceholderText("builds.configForm.name.placeholder")
      ).toHaveValue("Legacy Agent")
    )

    fireEvent.click(screen.getByText("builds.editor.header.update"))

    await waitFor(() => {
      const putCall = apiRequestMock.mock.calls.find(
        ([, opts]) => (opts as any)?.method === "PUT"
      )
      expect(putCall).toBeTruthy()
      const body = JSON.parse((putCall![1] as any).body)
      expect(body.tool_categories).toEqual(["basic"])
    })
  })
})

async function expectRejectedUpdate(expected: string) {
  render(<AgentBuilder agentId={AGENT_ID} />)
  const nameInput = await screen.findByPlaceholderText(
    "builds.configForm.name.placeholder"
  )
  fireEvent.change(nameInput, { target: { value: "Updated Agent" } })
  fireEvent.click(screen.getByText("builds.editor.header.update"))
  await waitFor(() =>
    expect(builderToastErrorMock.mock.calls.at(-1)?.[0]).toBe(expected)
  )
  expect(screen.getByDisplayValue("Updated Agent")).toBeInTheDocument()
}

describe("AgentBuilder update error handling (issue #956)", () => {
  it.each([
    {
      name: "a plain string detail",
      payload: { detail: " Agent update failed with string detail " },
      expected: "Agent update failed with string detail",
    },
    {
      name: "a top-level message",
      payload: { message: " Agent update failed with top-level message " },
      expected: "Agent update failed with top-level message",
    },
    {
      name: "a detail object without a readable message",
      payload: { detail: { code: 123 } },
      expected: "builds.editor.error.unknown",
    },
    {
      name: "a detail array without readable entries",
      payload: {
        detail: [1, true, null, { msg: " " }, { message: " " }],
      },
      expected: "builds.editor.error.unknown",
    },
  ])("handles $name without unmounting the builder", async ({ payload, expected }) => {
    installApi(
      ["basic"],
      () =>
        new Response(JSON.stringify(payload), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        })
    )
    await expectRejectedUpdate(expected)
  })

  it("renders a structured detail message without unmounting the builder", async () => {
    installApi(
      ["basic"],
      () =>
        new Response(
          JSON.stringify({
            detail: {
              message: "Agent update failed",
              context: [],
            },
          }),
          { status: 422, headers: { "Content-Type": "application/json" } }
        )
    )
    await expectRejectedUpdate("Agent update failed")
  })

  it("uses the localized fallback for an empty response without unmounting the builder", async () => {
    installApi(["basic"], () => new Response(null, { status: 500 }))
    await expectRejectedUpdate("builds.editor.error.unknown")
  })

  it("renders FastAPI validation detail messages without unmounting the builder", async () => {
    installApi(
      ["basic"],
      () =>
        new Response(
          JSON.stringify({
            detail: [
              { msg: " Name must be 200 characters or fewer " },
              " Invalid model selection ",
              { message: " Unsupported execution mode " },
              { msg: " " },
            ],
          }),
          { status: 422, headers: { "Content-Type": "application/json" } }
        )
    )
    await expectRejectedUpdate(
      "Name must be 200 characters or fewer; Invalid model selection; Unsupported execution mode"
    )
  })
})

describe("AgentBuilder extra built-in tools (issue #306)", () => {
  const PREFIX = "The agent may also use these built-in tools: "
  const block = () => screen.queryByText((text) => text.startsWith(PREFIX))
  const toolsBody = (tools: Record<string, unknown>[]) => ({
    tools: [
      { name: "calculator", description: "", category: "basic", enabled: true, always_available: false },
      ...tools,
    ],
    skill_loader_tool: "skill_loader_x",
  })

  it("lists backend-flagged tools and adds the skill loader only once a skill is selected", async () => {
    installApi(
      ["basic"],
      undefined,
      toolsBody([{ name: "clock_a", description: "", category: "other", enabled: true, always_available: true }]),
      [{ name: "writer" }]
    )
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => expect(block()?.textContent).toBe(`${PREFIX}clock_a`))

    fireEvent.click(document.getElementById("selectAllSkills")!)

    await waitFor(() => expect(block()?.textContent).toBe(`${PREFIX}clock_a, skill_loader_x`))
  })

  it("lists an always-available tool even when an admin disabled it", async () => {
    installApi(
      ["basic"],
      undefined,
      toolsBody([{ name: "clock_off", description: "", category: "other", enabled: false, always_available: true }])
    )
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => expect(block()?.textContent).toContain("clock_off"))
  })

  it("hides the block for an agent saved with zero tools", async () => {
    installApi(
      [],
      undefined,
      toolsBody([{ name: "clock_a", description: "", category: "other", enabled: true, always_available: true }])
    )
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => {
      expect(screen.getByPlaceholderText("builds.configForm.name.placeholder")).toHaveValue("Legacy Agent")
      expect(toolCategorySelector()?.textContent).toContain("basic")
    })
    expect(block()).toBeNull()

    fireEvent.click(document.getElementById("selectAllTools")!)

    await waitFor(() => expect(block()).not.toBeNull())
  })

  it.each([
    { source: "a knowledge base", patch: { knowledge_bases: ["kb1"] } },
    { source: "an MCP connector", patch: { tool_categories: ["mcp:foo"] } },
  ])("lists always-available tools when only $source configures tools", async ({ patch }) => {
    installApi(
      [],
      undefined,
      toolsBody([{ name: "clock_a", description: "", category: "other", enabled: true, always_available: true }])
    )
    const baseImpl = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation(async (url: string, opts?: { method?: string }) =>
      url.endsWith(`/api/agents/${AGENT_ID}`)
        ? new Response(JSON.stringify({ ...agentResponse([]), ...patch }), { status: 200 })
        : baseImpl(url, opts)
    )
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => expect(block()?.textContent).toBe(`${PREFIX}clock_a`))
  })

  it("lists only the skill loader for a zero-tool agent with a skill selected", async () => {
    installApi(
      [],
      undefined,
      toolsBody([{ name: "clock_a", description: "", category: "other", enabled: true, always_available: true }]),
      [{ name: "writer" }]
    )
    render(<AgentBuilder agentId={AGENT_ID} />)

    await waitFor(() => {
      expect(screen.getByPlaceholderText("builds.configForm.name.placeholder")).toHaveValue("Legacy Agent")
      expect(toolCategorySelector()?.textContent).toContain("basic")
    })

    fireEvent.click(document.getElementById("selectAllSkills")!)

    await waitFor(() => expect(block()?.textContent).toBe(`${PREFIX}skill_loader_x`))
  })
})
