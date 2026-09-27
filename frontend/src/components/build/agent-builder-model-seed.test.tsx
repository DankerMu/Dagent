import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { builderAgentResponse, builderResourceResponse } from "./agent-builder-test-helpers"
import { apiRequestMock, configureBuilderTestShell } from "./agent-builder-test-shell"

// Server-side creation paths persisted no model config, so opening such an
// agent in the builder rendered "--" and the required-model guard refused to
// save. The edit-mode seed fills the slot from the owner's own default, and
// must not count as a user edit (that would disable Publish on open).

const authUser = {
  current: { id: "1", is_admin: false },
}

vi.mock("@/components/ui/select", () => ({
  Select: ({ value, onValueChange, options }: any) => (
    <select
      data-testid="model-select"
      value={value ?? ""}
      onChange={(e) => onValueChange(e.target.value)}
    >
      {(options || []).map((o: any) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}))

import { toast } from "sonner"

import { AgentBuilder } from "./agent-builder"

const AGENT_ID = "5"
const DEFAULT_MODEL_ID = 42

function agentResponse(models: unknown, canEdit = true) {
  return builderAgentResponse(AGENT_ID, {
    name: "Seed Test Agent",
    instructions: "You are a test agent.",
    models,
    can_edit: canEdit,
  })
}

function lanLlm(id: number, modelName: string) {
  return {
    id,
    model_name: modelName,
    model_provider: "openai-compatible",
    base_url: "http://lan-llm/v1",
    category: "llm",
  }
}

const userDefault = (modelId: number) => [
  { config_type: "general", model: lanLlm(modelId, "seeded-llm") },
]

type Gate = { release: () => void }

function installApi(opts: {
  models: unknown
  userDefaults?: unknown[]
  llms?: unknown[]
  canEdit?: boolean
  gateAgent?: Gate
  gateDefaults?: Gate
  gateOwnerMcp?: Gate
}) {
  const defer = (gate: Gate | undefined, value: Response) => {
    if (!gate) return Promise.resolve(value)
    return new Promise<Response>(resolve => {
      gate.release = () => resolve(value)
    })
  }
  apiRequestMock.mockImplementation(
    (url: string, o?: { method?: string; body?: string }) => {
      if (o?.method === "PUT") {
        const sent = JSON.parse(o.body || "{}")
        return Promise.resolve(
          new Response(
            JSON.stringify(agentResponse(sent.models ?? opts.models)),
            { status: 200 }
          )
        )
      }
      if (url.endsWith("/api/models/?category=llm"))
        // The default general model is always in this list for real: both go
        // through the same visibility filter.
        return Promise.resolve(
          new Response(
            JSON.stringify(
              opts.llms ?? [lanLlm(DEFAULT_MODEL_ID, "seeded-llm")]
            ),
            { status: 200 }
          )
        )
      if (url.endsWith("/api/models/user-default"))
        return defer(
          opts.gateDefaults,
          new Response(
            JSON.stringify(opts.userDefaults ?? userDefault(DEFAULT_MODEL_ID)),
            { status: 200 }
          )
        )
      if (url.endsWith(`/api/agents/${AGENT_ID}`))
        return defer(
          opts.gateAgent,
          new Response(
            JSON.stringify(agentResponse(opts.models, opts.canEdit ?? true)),
            { status: 200 }
          )
        )
      if (url.includes("/api/mcp/servers?user_id="))
        return defer(
          opts.gateOwnerMcp,
          new Response(JSON.stringify([]), { status: 200 })
        )
      const resource = builderResourceResponse(url, { agentId: AGENT_ID })
      if (resource) return Promise.resolve(resource)
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
    }
  )
}

const generalSelect = () =>
  screen.getAllByTestId("model-select")[0] as HTMLSelectElement
const updateButton = () => screen.getByText("builds.editor.header.update")
const publishButton = () => screen.getByText("builds.editor.header.publish")
const nameBox = () => screen.getByDisplayValue("Seed Test Agent")
const loaded = () =>
  waitFor(() => expect(screen.getByDisplayValue("Seed Test Agent")).toBeInTheDocument())

async function renderLoadedBuilder() {
  render(<AgentBuilder agentId={AGENT_ID} />)
  await loaded()
}

const savedModels = async () => {
  await waitFor(() => {
    const put = apiRequestMock.mock.calls.find(([, o]) => (o as any)?.method === "PUT")
    expect(put).toBeTruthy()
  })
  const put = apiRequestMock.mock.calls.find(([, o]) => (o as any)?.method === "PUT")
  return JSON.parse((put![1] as any).body).models
}

async function saveAfterRenaming(models: unknown) {
  installApi({ models })
  await renderLoadedBuilder()
  fireEvent.change(nameBox(), { target: { value: "Renamed" } })
  fireEvent.click(updateButton())
  return savedModels()
}

async function saveSeededWithoutOtherChanges() {
  await waitFor(() => expect(updateButton()).not.toBeDisabled())
  fireEvent.click(updateButton())
  return savedModels()
}

beforeEach(() => {
  apiRequestMock.mockReset()
  configureBuilderTestShell({ auth: () => ({ token: "token", user: authUser.current }) })
  authUser.current = { id: "1", is_admin: false }
  ;(globalThis as any).WebSocket = vi.fn()
})

afterEach(() => cleanup())

describe("AgentBuilder edit-mode general-model seed", () => {
  it("keeps an existing LAN openai model visible and selected in the builder", async () => {
    installApi({
      models: { general: DEFAULT_MODEL_ID },
      llms: [{
        id: DEFAULT_MODEL_ID,
        model_id: "local/assistant",
        model_name: "LAN assistant",
        model_provider: "openai",
        base_url: "http://lan-llm/v1",
        category: "llm",
      }],
      userDefaults: [{
        config_type: "general",
        model: { id: DEFAULT_MODEL_ID, model_provider: "openai", base_url: "http://lan-llm/v1" },
      }],
    })
    await renderLoadedBuilder()

    await waitFor(() => {
      expect(generalSelect()).toHaveValue(String(DEFAULT_MODEL_ID))
      expect(generalSelect().querySelector(`option[value="${DEFAULT_MODEL_ID}"]`)).toBeInTheDocument()
    })
  })

  it("saves an agent whose stored config is null", async () => {
    expect((await saveAfterRenaming(null)).general).toBe(DEFAULT_MODEL_ID)
  })

  it("treats a truthy-empty stored config as unset too", async () => {
    expect((await saveAfterRenaming({})).general).toBe(DEFAULT_MODEL_ID)
  })

  it("leaves Publish enabled and Update reachable on open", async () => {
    // Publish posts no body, so Update is the only flow that persists the
    // seeded slot -- it has to stay reachable, and Publish must not be
    // blocked for the very agents the seed exists to unblock.
    installApi({ models: null })
    await renderLoadedBuilder()

    await waitFor(() => expect(updateButton()).not.toBeDisabled())
    expect(publishButton()).not.toBeDisabled()
  })

  it("persists the seeded slot when Update is clicked with nothing else changed", async () => {
    installApi({ models: null })
    await renderLoadedBuilder()

    expect((await saveSeededWithoutOtherChanges()).general).toBe(DEFAULT_MODEL_ID)
  })

  it("still blocks Publish once the user edits something else", async () => {
    installApi({ models: null })
    await renderLoadedBuilder()

    fireEvent.change(nameBox(), { target: { value: "Renamed" } })

    await waitFor(() => expect(publishButton()).toBeDisabled())
  })

  it("does not overwrite a slot the owner already chose", async () => {
    expect((await saveAfterRenaming({ general: 7 })).general).toBe(7)
  })

  it("preserves the other slots it does not fill", async () => {
    const models = await saveAfterRenaming({ compact: 3 })
    expect(models.general).toBe(DEFAULT_MODEL_ID)
    expect(models.compact).toBe(3)
  })

  it("ignores a malformed entry without losing a valid one", async () => {
    installApi({
      models: null,
      userDefaults: [
        { config_type: "general", model: lanLlm(DEFAULT_MODEL_ID, "seeded-llm") },
        { config_type: "general", model: null },
      ],
    })
    await renderLoadedBuilder()

    expect((await saveSeededWithoutOtherChanges()).general).toBe(DEFAULT_MODEL_ID)
  })

  it("does not seed an id the model list does not contain", async () => {
    // A default pointing at a model absent from /api/models would render an
    // empty Select while counting as an edit.
    installApi({
      models: null,
      llms: [lanLlm(123, "other-llm")],
    })
    await renderLoadedBuilder()

    await waitFor(() => expect(generalSelect().value).toBe(""))
    expect(updateButton()).toBeDisabled()
  })

  it("does not fall back to the first available LLM in edit mode", async () => {
    // Silently pinning "whatever is first in the model list" onto an agent
    // that already exists is a choice the owner never made; the required-model
    // guard keeps holding instead.
    installApi({
      models: null,
      userDefaults: [],
      llms: [lanLlm(99, "some-llm")],
    })
    await renderLoadedBuilder()

    fireEvent.change(nameBox(), { target: { value: "Renamed" } })
    fireEvent.click(updateButton())

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(
      apiRequestMock.mock.calls.find(([, o]) => (o as any)?.method === "PUT")
    ).toBeUndefined()
  })

  it("shows the seeded model when the viewer owns the agent", async () => {
    installApi({
      models: null,
      llms: [lanLlm(DEFAULT_MODEL_ID, "seeded-llm")],
    })
    await renderLoadedBuilder()

    await waitFor(() =>
      expect(generalSelect().value).toBe(String(DEFAULT_MODEL_ID))
    )
  })

  it("does not seed a read-only cross-user view", async () => {
    // userDefaultGeneralRef holds the VIEWER's default; seeding here would
    // render someone else's model as this agent's configuration.
    installApi({
      models: null,
      canEdit: false,
      llms: [lanLlm(DEFAULT_MODEL_ID, "seeded-llm")],
    })
    await renderLoadedBuilder()

    await waitFor(() => expect(generalSelect()).toBeDisabled())
    expect(generalSelect().value).toBe("")
  })

  it("seeds when the agent load resolves last", async () => {
    const gateAgent: Gate = { release: () => {} }
    installApi({ models: null, gateAgent })
    render(<AgentBuilder agentId={AGENT_ID} />)
    await waitFor(() => expect(apiRequestMock).toHaveBeenCalled())
    gateAgent.release()
    await loaded()

    fireEvent.change(nameBox(), { target: { value: "Renamed" } })
    fireEvent.click(updateButton())

    expect((await savedModels()).general).toBe(DEFAULT_MODEL_ID)
  })

  it("seeds when isInitialDataLoaded is the last dependency to land", async () => {
    // Gating the user-default response also gates isInitialDataLoaded (both
    // come from the same Promise.all), so this is not a race between the two
    // fetches -- it is the case where the agent load commits first and the
    // effect must still fire once the mount fetch finally reports in.
    const gateDefaults: Gate = { release: () => {} }
    installApi({ models: null, gateDefaults })
    await renderLoadedBuilder()
    gateDefaults.release()

    await waitFor(() => expect(publishButton()).not.toBeDisabled())
    fireEvent.change(nameBox(), { target: { value: "Renamed" } })
    fireEvent.click(updateButton())

    expect((await savedModels()).general).toBe(DEFAULT_MODEL_ID)
  })
})

describe("AgentBuilder seed provenance after a save", () => {
  const OTHER_MODEL_ID = 7
  const availableModels = [
    lanLlm(DEFAULT_MODEL_ID, "seeded-llm"),
    lanLlm(OTHER_MODEL_ID, "other-llm"),
  ]

  beforeEach(async () => {
    installApi({ models: null, llms: availableModels })
    await renderLoadedBuilder()
    await waitFor(() => expect(publishButton()).not.toBeDisabled())
  })

  it("stops exempting the seeded model once an Update has persisted another", async () => {
    // seed A -> pick B -> Update (B is now stored) -> pick A again. A is no
    // longer "the slot this page seeded", it is an unsaved edit, and Publish
    // cannot persist it (the request carries no body).

    fireEvent.change(generalSelect(), { target: { value: String(OTHER_MODEL_ID) } })
    fireEvent.click(updateButton())
    expect((await savedModels()).general).toBe(OTHER_MODEL_ID)

    await waitFor(() => expect(updateButton()).toBeDisabled())
    fireEvent.change(generalSelect(), {
      target: { value: String(DEFAULT_MODEL_ID) },
    })

    await waitFor(() => expect(publishButton()).toBeDisabled())
  })

  it("still exempts the seeded model before any save", async () => {
    fireEvent.change(generalSelect(), { target: { value: String(OTHER_MODEL_ID) } })
    await waitFor(() => expect(publishButton()).toBeDisabled())

    fireEvent.change(generalSelect(), {
      target: { value: String(DEFAULT_MODEL_ID) },
    })
    await waitFor(() => expect(publishButton()).not.toBeDisabled())
  })
})

describe("AgentBuilder seed across loadAgent's awaits", () => {
  it("keeps the seed when an admin cross-user load awaits mid-way", async () => {
    // The admin branch of loadAgent awaits an owner-scoped MCP fetch. React 18
    // does not batch across it, so a setModelConfig placed after the await
    // clobbers a seed that already ran -- and the stamped ref would stop it
    // from ever running again. Only a truthy-empty `models` reaches that
    // assignment (null skips the `if`), which is what loaders.py used to write.
    const gateOwnerMcp: Gate = { release: () => {} }
    authUser.current = { id: "9", is_admin: true }
    installApi({
      models: {},
      gateOwnerMcp,
      llms: [lanLlm(DEFAULT_MODEL_ID, "seeded-llm")],
    })
    await renderLoadedBuilder()

    // Parked inside the await, with originalData already committed: the seed
    // effect gets its chance here.
    await waitFor(() =>
      expect(generalSelect().value).toBe(String(DEFAULT_MODEL_ID))
    )
    gateOwnerMcp.release()

    // ...and whatever runs after the await must not undo it.
    await waitFor(() => expect(screen.getByText("seeded-llm")).toBeInTheDocument())
    expect(generalSelect().value).toBe(String(DEFAULT_MODEL_ID))
  })
})
