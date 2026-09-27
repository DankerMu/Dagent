/// <reference types="@testing-library/jest-dom/vitest" />
import React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const apiRequestMock = vi.hoisted(() => vi.fn())
const routerPushMock = vi.hoisted(() => vi.fn())
const translateMock = vi.hoisted(() => {
  return (key: string, vars?: Record<string, string | number>) => {
    if (vars?.count) return `${key}:${vars.count}`
    if (vars?.timezone) return `${key}:${vars.timezone}`
    return key
  }
})

vi.mock("@/lib/api-wrapper", () => ({
  apiRequest: apiRequestMock,
}))

vi.mock("@/lib/utils", () => ({
  cn: (...values: Array<string | false | null | undefined>) => values.filter(Boolean).join(" "),
  getApiUrl: () => "http://api.local",
}))

vi.mock("@/contexts/i18n-context", () => ({
  useI18n: () => ({ t: translateMock, locale: "en" }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPushMock }),
}))

const toastMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  info: vi.fn(),
}))

vi.mock("@/components/ui/sonner", () => ({
  toast: toastMocks,
}))

vi.mock("@/lib/clipboard", () => ({
  copyToClipboard: vi.fn().mockResolvedValue(true),
}))

import { AgentTriggersDialog } from "./agent-triggers-dialog"
import { localIsoDate, localTimeOfDay, zonedIsoDate } from "./agent-triggers-schedule-fields"
import type { AgentTrigger, StagedTrigger } from "@/lib/agent-triggers-api"
import { testTrigger as makeTrigger } from "./agent-triggers-test-helpers"

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  })
}

function triggerListResponse(
  url: string,
  init: { method?: string } | undefined,
  triggers: AgentTrigger[],
): Response | undefined {
  if (url === "http://api.local/api/agents/42/triggers" && (!init?.method || init.method === "GET")) {
    return jsonResponse(triggers)
  }
}



function renderDialog(onOpenChange = vi.fn(), onChanged?: () => void) {
  return render(<AgentTriggersDialog agentId={42} open onOpenChange={onOpenChange} onChanged={onChanged} />)
}

function mockTriggerList(trigger: AgentTrigger, acceptPatch = false) {
  apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
    const list = triggerListResponse(url, init, [trigger])
    if (list) return Promise.resolve(list)
    if (acceptPatch && url === `http://api.local/api/agents/42/triggers/${trigger.id}` && init?.method === "PATCH") {
      return Promise.resolve(jsonResponse(trigger))
    }
    return Promise.resolve(jsonResponse([]))
  })
}
function mockEmptyTriggerList() {
  apiRequestMock.mockImplementation(() => Promise.resolve(jsonResponse([])))
}

function createdTriggerResponse(id: number, body?: string): Response {
  return jsonResponse(makeTrigger({ id, ...(body ? JSON.parse(body) : {}) }))
}

function mockCreatedWebhook(id: number, secret: string) {
  apiRequestMock.mockImplementation((url: string, options?: { method?: string }) => {
    if (url === "http://api.local/api/agents/42/triggers" && options?.method === "POST") {
      return Promise.resolve(jsonResponse(makeTrigger({
        id,
        name: "API / Webhook",
        webhook_token: "tok",
        webhook_secret: secret,
      })))
    }
    return Promise.resolve(jsonResponse([]))
  })
}

async function openWebhookEditor() {
  fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
  fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.edit" }))
}

async function saveNewWebhookAndReveal(secret: string) {
  const [webhookSwitch] = await screen.findAllByRole("switch")
  fireEvent.click(webhookSwitch)
  await screen.findByLabelText("triggers.form.secret")
  fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))
  expect(await screen.findByText(secret)).toBeInTheDocument()
}

async function editWebhookThenDisable(name: string) {
  await openWebhookEditor()
  const nameInput = await screen.findByLabelText("triggers.form.name")
  fireEvent.change(nameInput, { target: { value: name } })
  const [headerSwitch] = screen.getAllByRole("switch")
  fireEvent.click(headerSwitch)
  await waitFor(() => expect(headerSwitch).toHaveAttribute("aria-checked", "false"))
  return nameInput
}

async function openScheduledEditor() {
  fireEvent.click(await screen.findByText("triggers.cards.scheduled.title"))
  fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.edit" }))
  await screen.findByText("triggers.schedule.recurrenceLabel")
}

function mockBrowserTimezone(timezone: string) {
  const RealDateTimeFormat = Intl.DateTimeFormat
  return vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
    (...args: ConstructorParameters<typeof Intl.DateTimeFormat>) =>
      args.length === 0
        ? { resolvedOptions: () => ({ timeZone: timezone }) } as Intl.DateTimeFormat
        : new RealDateTimeFormat(...args),
  )
}

describe("AgentTriggersDialog", () => {

  const baseTrigger9 = makeTrigger({
    id: 9,
    type: "webhook",
    name: "Support hook",
    config: {},
    prompt_template: "Reply to {{payload}}",
  })

  beforeEach(() => {
    apiRequestMock.mockReset()
    routerPushMock.mockReset()
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers" && init?.method === "POST") {
        // Save (or Test on an unsaved draft) POSTs here; return the new
        // trigger shape rather than the list shape used by GET.
        return Promise.resolve(createdTriggerResponse(20, init.body))
      }
      if (url === "http://api.local/api/agents/42/triggers") {
        return Promise.resolve(jsonResponse([baseTrigger9]))
      }
      if (url === "http://api.local/api/agents/42/triggers/9/runs") {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === "http://api.local/api/agents/42/triggers/9" && init?.method === "PATCH") {
        // Echo the base trigger merged with the PATCH body, like a real
        // backend would — a bare `[]` fallback here would make `updated`
        // shapeless for any code that reads fields off the response.
        const patch = init.body ? JSON.parse(init.body) : {}
        return Promise.resolve(jsonResponse({ ...baseTrigger9, ...patch }))
      }
      return Promise.resolve(jsonResponse([]))
    })
  })

  afterEach(() => {
    cleanup()
  })










  it("reports a failed trigger list and reloads the real list when reopened", async () => {
    let denied = true
    apiRequestMock.mockImplementation((url: string) => {
      if (url === "http://api.local/api/agents/42/triggers") {
        return Promise.resolve(denied
          ? jsonResponse({ detail: "Trigger access denied" }, { status: 403 })
          : jsonResponse([baseTrigger9]))
      }
      return Promise.resolve(jsonResponse([]))
    })
    const view = renderDialog()
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Trigger access denied"))
    expect(screen.queryByText("Support hook")).not.toBeInTheDocument()
    denied = false
    view.rerender(<AgentTriggersDialog agentId={42} open={false} onOpenChange={vi.fn()} />)
    view.rerender(<AgentTriggersDialog agentId={42} open onOpenChange={vi.fn()} />)
    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    expect(await screen.findByText("Support hook")).toBeInTheDocument()
  })

  it("reports unavailable run history without hiding the editable webhook", async () => {
    const respond = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) =>
      url === "http://api.local/api/agents/42/triggers/9/runs"
        ? Promise.resolve(jsonResponse({ detail: "Run history unavailable" }, { status: 503 }))
        : respond(url, init))
    renderDialog()
    await openWebhookEditor()
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Run history unavailable"))
    expect(screen.getByLabelText("triggers.form.name")).toHaveValue("Support hook")
  })

  it("retains a live webhook when its confirmed deletion is denied", async () => {
    const respond = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) =>
      url === "http://api.local/api/agents/42/triggers/9" && init?.method === "DELETE"
        ? Promise.resolve(jsonResponse({ detail: "Cannot delete active webhook" }, { status: 403 }))
        : respond(url, init))
    const onChanged = vi.fn()
    renderDialog(vi.fn(), onChanged)
    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("Support hook")
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.delete" }))
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.confirmDelete" }))
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Cannot delete active webhook"))
    expect(screen.getByText("Support hook")).toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it("keeps a saved webhook editable when its test execution is denied", async () => {
    const respond = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) =>
      url === "http://api.local/api/agents/42/triggers/9/test" && init?.method === "POST"
        ? Promise.resolve(jsonResponse({ detail: "Execution not permitted" }, { status: 403 }))
        : respond(url, init))
    renderDialog()
    await openWebhookEditor()
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.test" }))
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Execution not permitted"))
    expect(screen.getByLabelText("triggers.form.name")).toHaveValue("Support hook")
    expect(screen.getByRole("button", { name: "triggers.actions.test" })).not.toBeDisabled()
  })

  it("does not reveal a replacement webhook secret when rotation is refused", async () => {
    const respond = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) =>
      url === "http://api.local/api/agents/42/triggers/9" && init?.method === "PATCH"
        && JSON.parse(init.body || "{}").rotate_secret
        ? Promise.resolve(jsonResponse({ detail: "Rotation denied" }, { status: 403 }))
        : respond(url, init))
    const onChanged = vi.fn()
    renderDialog(vi.fn(), onChanged)
    await openWebhookEditor()
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.rotateSecret" }))
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Rotation denied"))
    expect(onChanged).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "triggers.actions.rotateSecret" })).not.toBeDisabled()
  })

  it("reveals a rotated secret once and requires explicit acknowledgement", async () => {
    const respond = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers/9" && init?.method === "PATCH"
        && JSON.parse(init.body || "{}").rotate_secret) {
        return Promise.resolve(jsonResponse({ ...baseTrigger9, webhook_secret: "wh_rotated_once" })) // pragma: allowlist secret - synthetic rotation response
      }
      return respond(url, init)
    })
    const onOpenChange = vi.fn()
    const onChanged = vi.fn()
    renderDialog(onOpenChange, onChanged)
    await openWebhookEditor()
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.rotateSecret" }))
    expect(await screen.findByText("wh_rotated_once")).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    fireEvent.click(screen.getByRole("button", { name: "triggers.secret.dismiss" }))
    expect(screen.queryByText("wh_rotated_once")).not.toBeInTheDocument()
  })

  it("shows recent webhook runs and opens only the run with a task", async () => {
    const respond = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers/9/runs") {
        return Promise.resolve(jsonResponse([
          { id: 50, status: "completed", source_event_id: "ticket-123", idempotency_key: "idem-50", task_id: 813 },
          { id: 51, status: "failed", source_event_id: null, idempotency_key: "retry-51", task_id: null },
        ]))
      }
      return respond(url, init)
    })
    renderDialog()
    await openWebhookEditor()
    expect(await screen.findByText("ticket-123")).toBeInTheDocument()
    expect(screen.getByText("retry-51")).toBeInTheDocument()
    expect(screen.getByText("triggers.runStatus.completed")).toBeInTheDocument()
    expect(screen.getByText("triggers.runStatus.failed")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "#813" }))
    expect(routerPushMock).toHaveBeenCalledWith("/task/813")
  })

  it("shows a visible provisioning error on a trigger's list row", async () => {
    // PR #1051 review, N6: the backend sets provisioning_status/
    // provisioning_error (e.g. scan_due_scheduled_triggers disabling a
    // trigger after a recompute failure) — without visible error state,
    // before this, nothing in the dialog rendered them, so a trigger that
    // silently stopped firing showed no visible signal beyond the generic
    // enabled/disabled badge.
    const failingTrigger = makeTrigger({
      id: 11,
      type: "scheduled",
      name: "Broken schedule",
      config: { recurrence: "hourly", interval_seconds: 3600 },
      provisioning_status: "failed",
      provisioning_error: "Schedule recompute failed",
    })
    apiRequestMock.mockImplementation((url: string) => {
      if (url === "http://api.local/api/agents/42/triggers") {
        return Promise.resolve(jsonResponse([failingTrigger]))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.scheduled.title"))

    expect(
      await screen.findByText("Schedule recompute failed"),
    ).toBeInTheDocument()
  })

  it("persists the detail header switch immediately without pressing save", async () => {
    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    // Manage list: the trigger's card is visible; the first switch is the
    // type-level header switch (on, since the trigger is enabled).
    await screen.findByText("Support hook")

    const [headerSwitch] = screen.getAllByRole("switch")
    expect(headerSwitch).toHaveAttribute("aria-checked", "true")
    fireEvent.click(headerSwitch)

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers/9",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ enabled: false }),
        }),
      )
    })
    expect(headerSwitch).toHaveAttribute("aria-checked", "false")
  })

  it("reconciles a card switch from the PATCH response, not just the requested value", async () => {
    const defaultResponder = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers/9" && init?.method === "PATCH") {
        // A backend that (hypothetically) overrides the requested value —
        // the derived switch must reflect the response, not the request.
        return Promise.resolve(jsonResponse({ ...baseTrigger9, enabled: true }))
      }
      return defaultResponder(url, init)
    })

    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("Support hook")

    // switches: [header master, card switch]
    const [, cardSwitch] = screen.getAllByRole("switch")
    expect(cardSwitch).toHaveAttribute("aria-checked", "true")
    fireEvent.click(cardSwitch)

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers/9",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ enabled: false }),
        }),
      )
    })
    // Reconciled: the response said `enabled: true`, so the card stays on.
    await waitFor(() => {
      expect(cardSwitch).toHaveAttribute("aria-checked", "true")
    })
  })

  it("reveals the one-time webhook secret on the list after saving a new webhook", async () => {
    mockCreatedWebhook(11, "wh_secret_once")

    renderDialog()

    // Toggling on with no webhook yet opens the draft editor; Save creates
    // the trigger and lands back on the list, where the freshly generated
    // secret is revealed once.
    await saveNewWebhookAndReveal("wh_secret_once")
    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers",
        expect.objectContaining({ method: "POST" }),
      )
    })
  })

  it("fills the secret field with a client-generated whsec_ value on Generate secret", async () => {
    mockEmptyTriggerList()
    renderDialog()

    await screen.findByText("triggers.cards.webhook.title")
    const [webhookSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookSwitch)

    const secretInput = (await screen.findByLabelText(
      "triggers.form.secret",
    )) as HTMLInputElement
    expect(secretInput).toHaveValue("")

    fireEvent.click(screen.getByRole("button", { name: "triggers.form.generateSecret" }))

    expect(secretInput.value).toMatch(/^whsec_[A-Za-z0-9_-]+$/)
    expect(screen.getByText("triggers.form.secretGeneratedHint")).toBeInTheDocument()

    // Typing a value of one's own discards the generated one and its hint.
    fireEvent.change(secretInput, { target: { value: "my-own-secret" } })
    expect(screen.queryByText("triggers.form.secretGeneratedHint")).not.toBeInTheDocument()
  })

  it("calls onChanged exactly once for a Save (Done afterward does not refetch again)", async () => {
    // Mirrors the builder's wiring: onChanged is the sole resync signal:
    // onOpenChange(false) must not ALSO trigger a refetch, or every save
    // fires the same GET twice.
    const onChanged = vi.fn()
    renderDialog(vi.fn(), onChanged)

    await openWebhookEditor()
    const nameInput = await screen.findByLabelText("triggers.form.name")
    fireEvent.change(nameInput, { target: { value: "Support" } })
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))

    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByRole("button", { name: "common.done" }))
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it("opens a draft editor without any POST when the switch is turned on with no webhook yet", async () => {
    mockEmptyTriggerList()
    renderDialog()

    // The webhook type has no triggers yet: toggling on goes straight into
    // the new-webhook editor. Nothing is created until Save, so the header
    // switch (derived from saved triggers) stays off.
    await screen.findByText("triggers.cards.webhook.title")
    const [webhookCardSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookCardSwitch)

    await screen.findByLabelText("triggers.form.secret")
    expect(screen.getByText("triggers.editor.webhookNew")).toBeInTheDocument()
    const [headerSwitch] = screen.getAllByRole("switch")
    expect(headerSwitch).toHaveAttribute("aria-checked", "false")
    const postCalls = apiRequestMock.mock.calls.filter(
      ([url, init]) => url === "http://api.local/api/agents/42/triggers" && init?.method === "POST",
    )
    expect(postCalls).toHaveLength(0)
  })

  it("creates once via POST on Save; editing the card afterwards updates via PATCH", async () => {
    apiRequestMock.mockImplementation((url: string, options?: { method?: string; body?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers" && options?.method === "POST") {
        return Promise.resolve(createdTriggerResponse(15, options.body))
      }
      if (url === "http://api.local/api/agents/42/triggers/15/runs") {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === "http://api.local/api/agents/42/triggers/15" && options?.method === "PATCH") {
        return Promise.resolve(jsonResponse(makeTrigger({ id: 15, name: "Renamed hook" })))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    // Draft → Save: exactly one POST, with the switch-on default enabled.
    const [webhookCardSwitch] = await screen.findAllByRole("switch")
    fireEvent.click(webhookCardSwitch)
    await screen.findByLabelText("triggers.form.name")
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))
    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("\"enabled\":true"),
        }),
      )
    })

    // Back on the list, edit the new card and save again → PATCH, no 2nd POST.
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.edit" }))
    const nameInput = await screen.findByLabelText("triggers.form.name")
    fireEvent.change(nameInput, { target: { value: "Renamed hook" } })
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers/15",
        expect.objectContaining({ method: "PATCH" }),
      )
    })
    const postCalls = apiRequestMock.mock.calls.filter(
      ([url, init]) => url === "http://api.local/api/agents/42/triggers" && init?.method === "POST",
    )
    expect(postCalls).toHaveLength(1)
  })

  it("keeps the dialog open on Escape when a fresh create just revealed a webhook secret", async () => {
    const onOpenChange = vi.fn()
    mockCreatedWebhook(12, "wh_escape_secret")

    renderDialog(onOpenChange)

    await saveNewWebhookAndReveal("wh_escape_secret")

    // Escape must not drop a secret that only exists because it was just
    // generated — unlike an ordinary validation failure, it is unrecoverable.
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    await waitFor(() => {
      expect(screen.getByText("wh_escape_secret")).toBeInTheDocument()
    })
    expect(onOpenChange).not.toHaveBeenCalledWith(false)

    // Only once the secret is explicitly acknowledged does Escape close.
    fireEvent.click(screen.getByRole("button", { name: "triggers.secret.dismiss" }))
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  it("keeps showing a fresh secret after Back navigates to the overview", async () => {
    mockCreatedWebhook(13, "wh_back_secret")

    renderDialog()

    await saveNewWebhookAndReveal("wh_back_secret")

    // Back navigates to the overview like any other exit path (nothing to
    // "commit" anymore) — the secret alert renders on the overview too, so
    // it stays visible until the user explicitly dismisses it.
    fireEvent.click(screen.getByRole("button", { name: "common.back" }))
    await waitFor(() => {
      expect(screen.queryByLabelText("triggers.form.name")).not.toBeInTheDocument()
    })
    expect(screen.getByText("wh_back_secret")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "triggers.secret.dismiss" }))
    await waitFor(() => {
      expect(screen.queryByText("wh_back_secret")).not.toBeInTheDocument()
    })
  })


  it("disables navigation while a detail toggle is in flight, and rolls back cleanly on rejection", async () => {
    const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"
    let rejectPatch: (err: Error) => void = () => {}

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === TRIGGERS_URL) {
        return Promise.resolve(
          jsonResponse([
            makeTrigger({ id: 20, name: "Backup hook" }),
            makeTrigger({ id: 21, name: "Primary hook" }),
          ]),
        )
      }
      if (url === `${TRIGGERS_URL}/20/runs` || url === `${TRIGGERS_URL}/21/runs`) {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === `${TRIGGERS_URL}/21` && init?.method === "PATCH") {
        // Never resolves on its own — held open so navigation controls can be
        // asserted disabled, then rejected explicitly below.
        return new Promise<Response>((_resolve, reject) => {
          rejectPatch = reject
        })
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    // Manage list, newest first: [Primary hook (21), Backup hook (20)].
    await screen.findByText("Primary hook")

    // switches: [header master, card 21, card 20]
    const [, primaryCardSwitch] = screen.getAllByRole("switch")
    expect(primaryCardSwitch).toHaveAttribute("aria-checked", "true")
    fireEvent.click(primaryCardSwitch)

    // While the PATCH is pending every navigation/mutation control is
    // disabled, so the eventual failure can't land on an unrelated view.
    await waitFor(() => {
      expect(primaryCardSwitch).toBeDisabled()
    })
    expect(screen.getByRole("button", { name: "common.back" })).toBeDisabled()
    for (const editButton of screen.getAllByRole("button", { name: "triggers.actions.edit" })) {
      expect(editButton).toBeDisabled()
    }

    rejectPatch(new Error("network error"))

    // The switch is derived from the (unchanged) trigger list, so a rejected
    // PATCH leaves it exactly where it started.
    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalled()
    })
    expect(primaryCardSwitch).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText("Primary hook")).toBeInTheDocument()
  })

  it("resyncs the trigger list when a batch disable partially fails", async () => {
    const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"
    let triggers = [
      makeTrigger({ id: 30, name: "Hook A" }),
      makeTrigger({ id: 31, name: "Hook B" }),
    ]
    let getCallsAfterFailure = 0
    let patchAttempted = false

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        if (patchAttempted) getCallsAfterFailure += 1
        return Promise.resolve(jsonResponse(triggers))
      }
      if (url === `${TRIGGERS_URL}/30` && init?.method === "PATCH") {
        triggers = triggers.map((item) => (item.id === 30 ? { ...item, enabled: false } : item))
        return Promise.resolve(jsonResponse(triggers[0]))
      }
      if (url === `${TRIGGERS_URL}/31` && init?.method === "PATCH") {
        patchAttempted = true
        return Promise.reject(new Error("boom"))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    const cardSwitch = (
      await screen.findAllByRole("switch")
    ).find((el) => el.getAttribute("aria-checked") === "true")
    fireEvent.click(cardSwitch!)

    // One PATCH in the batch rejected: the catch resyncs via a fresh GET
    // instead of trusting the local list (which would otherwise wrongly
    // report both triggers disabled).
    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(getCallsAfterFailure).toBeGreaterThan(0)
    })
  })

  it("removes a deleted trigger's card and keeps the remaining ones", async () => {
    const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"
    let triggers = [
      makeTrigger({ id: 50, name: "Older hook" }),
      makeTrigger({ id: 51, name: "Newer hook" }),
    ]

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse(triggers))
      }
      if (url === `${TRIGGERS_URL}/50/runs` || url === `${TRIGGERS_URL}/51/runs`) {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === `${TRIGGERS_URL}/51` && init?.method === "DELETE") {
        triggers = triggers.filter((item) => item.id !== 51)
        return Promise.resolve(jsonResponse({}))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    // Manage list, newest first: [Newer hook (51), Older hook (50)].
    await screen.findByText("Newer hook")

    // Delete the newest card via its trash button + confirmation popover.
    const [newerHookDelete] = screen.getAllByRole("button", { name: "triggers.actions.delete" })
    fireEvent.click(newerHookDelete)
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.confirmDelete" }))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        `${TRIGGERS_URL}/51`,
        expect.objectContaining({ method: "DELETE" }),
      )
    })
    await waitFor(() => {
      expect(screen.queryByText("Newer hook")).not.toBeInTheDocument()
    })
    expect(screen.getByText("Older hook")).toBeInTheDocument()
  })

  it("keeps each overview switch's busy guard independent across two types toggled back-to-back", async () => {
    const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"
    const triggers = [
      makeTrigger({ id: 40, name: "Hook" }),
      makeTrigger({ id: 41, type: "scheduled", name: "Schedule", config: { interval_seconds: 3600 } }),
    ]

    let resolveWebhookPatch: ((value: Response) => void) | undefined
    const webhookPatchPromise = new Promise<Response>((resolve) => {
      resolveWebhookPatch = resolve
    })
    let resolveScheduledPatch: ((value: Response) => void) | undefined
    const scheduledPatchPromise = new Promise<Response>((resolve) => {
      resolveScheduledPatch = resolve
    })

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse(triggers))
      }
      if (url === `${TRIGGERS_URL}/40` && init?.method === "PATCH") return webhookPatchPromise
      if (url === `${TRIGGERS_URL}/41` && init?.method === "PATCH") return scheduledPatchPromise
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    // TRIGGER_TYPES order is webhook, scheduled.
    const [webhookSwitch, scheduledSwitch] = await screen.findAllByRole("switch")
    expect(webhookSwitch).toHaveAttribute("aria-checked", "true")
    expect(scheduledSwitch).toHaveAttribute("aria-checked", "true")

    fireEvent.click(webhookSwitch)
    await waitFor(() => {
      expect(webhookSwitch).toBeDisabled()
    })
    expect(scheduledSwitch).not.toBeDisabled()

    fireEvent.click(scheduledSwitch)
    await waitFor(() => {
      expect(scheduledSwitch).toBeDisabled()
    })

    // Resolve the scheduled toggle first. A scalar busy-guard would have
    // cleared entirely here and wrongly re-enabled webhook's switch while
    // its own PATCH was still in flight — the Set-based guard keeps them
    // independent.
    resolveScheduledPatch?.(jsonResponse({ ...triggers[1], enabled: false }))
    await waitFor(() => {
      expect(scheduledSwitch).not.toBeDisabled()
    })
    expect(webhookSwitch).toBeDisabled()

    resolveWebhookPatch?.(jsonResponse({ ...triggers[0], enabled: false }))
    await waitFor(() => {
      expect(webhookSwitch).not.toBeDisabled()
    })
  })

  it("starts a one-click test run from the editor and refreshes recent runs", async () => {
    let runsCalls = 0
    let patchCalls = 0
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers") {
        return Promise.resolve(jsonResponse([baseTrigger9]))
      }
      if (url === "http://api.local/api/agents/42/triggers/9/runs") {
        runsCalls += 1
        return Promise.resolve(jsonResponse([]))
      }
      if (url === "http://api.local/api/agents/42/triggers/9" && init?.method === "PATCH") {
        // Test always saves first, so the on-screen draft is what runs.
        patchCalls += 1
        const patch = init.body ? JSON.parse(init.body) : {}
        return Promise.resolve(jsonResponse({ ...baseTrigger9, ...patch }))
      }
      if (url === "http://api.local/api/agents/42/triggers/9/test" && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ trigger_run: { id: 77 }, duplicate: false }))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    await openWebhookEditor()

    const testButton = await screen.findByRole("button", { name: "triggers.actions.test" })
    expect(testButton).not.toBeDisabled()
    const runsCallsBeforeTest = runsCalls
    fireEvent.click(testButton)

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers/9/test",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            payload: { message: "test trigger" },
            source_event_id: null,
          }),
        }),
      )
    })
    await waitFor(() => {
      expect(toastMocks.success).toHaveBeenCalledWith("triggers.messages.testStarted")
    })
    // The unsaved-edits-safe contract: the trigger was saved (PATCH) before
    // the test fired, and the runs list refreshed to show the new run.
    expect(patchCalls).toBe(1)
    expect(runsCalls).toBeGreaterThan(runsCallsBeforeTest)
  })

  it("saves an unsaved draft first, then starts the test, when Test trigger is clicked", async () => {
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === "http://api.local/api/agents/42/triggers" && init?.method === "POST") {
        return Promise.resolve(createdTriggerResponse(21, init.body))
      }
      if (url === "http://api.local/api/agents/42/triggers/21/runs") {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === "http://api.local/api/agents/42/triggers/21/test" && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ trigger_run: { id: 5 }, duplicate: false }))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderDialog()

    // Webhook type has no triggers: toggling on opens a new-webhook draft.
    await screen.findByText("triggers.cards.webhook.title")
    const [webhookSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookSwitch)

    await screen.findByLabelText("triggers.form.secret")
    const testButton = screen.getByRole("button", { name: "triggers.actions.test" })
    expect(testButton).not.toBeDisabled()
    fireEvent.click(testButton)

    // The draft is persisted first (exactly one POST), then the test fires
    // against the fresh id — and the editor stays open on the saved trigger.
    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers/21/test",
        expect.objectContaining({ method: "POST" }),
      )
    })
    const createCalls = apiRequestMock.mock.calls.filter(
      ([url, init]) => url === "http://api.local/api/agents/42/triggers" && init?.method === "POST",
    )
    expect(createCalls).toHaveLength(1)
    const createIndex = apiRequestMock.mock.calls.findIndex(
      ([url, init]) => url === "http://api.local/api/agents/42/triggers" && init?.method === "POST",
    )
    const testIndex = apiRequestMock.mock.calls.findIndex(
      ([url]) => url === "http://api.local/api/agents/42/triggers/21/test",
    )
    expect(createIndex).toBeLessThan(testIndex)
    await waitFor(() => {
      expect(toastMocks.success).toHaveBeenCalledWith("triggers.messages.testStarted")
    })
    expect(screen.getByLabelText("triggers.form.name")).toBeInTheDocument()
  })

  it("keeps a draft's typed fields when the header switch is clicked mid-composition", async () => {
    mockEmptyTriggerList()
    renderDialog()

    // Webhook type has no triggers: toggle on → draft editor, type a name.
    await screen.findByText("triggers.cards.webhook.title")
    const [webhookSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookSwitch)
    const nameInput = await screen.findByLabelText("triggers.form.name")
    fireEvent.change(nameInput, { target: { value: "Half-typed draft" } })

    // Clicking the (derived, still-off) header switch while composing the
    // draft must be a no-op — not a form reset.
    const [headerSwitch] = screen.getAllByRole("switch")
    fireEvent.click(headerSwitch)

    expect(screen.getByLabelText("triggers.form.name")).toHaveValue("Half-typed draft")
  })





  it("keeps unsaved field edits when the header switch is toggled while editing", async () => {
    renderDialog()

    // In the editor the type-level switch PATCHes enabled state immediately,
    // but the unsaved name remains a draft.
    const nameInput = await editWebhookThenDisable("Edited but unsaved")
    expect(nameInput).toHaveValue("Edited but unsaved")
  })

  it("does not silently re-enable a trigger disabled via the header switch when Save is pressed afterward", async () => {
    // PR #1051 review, F9: the editor's form.enabled is captured once at
    // beginEdit and never re-synced (the sync effect is gated by a
    // same-trigger-id guard that doesn't fire on an external enabled-state
    // change). Toggling the header switch off PATCHes enabled:false
    // immediately, but a subsequent Save used to resend the stale captured
    // form.enabled === true, silently re-enabling the trigger with no
    // visual cue (the editor has no enabled control of its own).
    renderDialog()

    await editWebhookThenDisable("Edited but unsaved")

    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))
    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers/9",
        expect.objectContaining({
          method: "PATCH",
          body: expect.stringContaining('"name":"Edited but unsaved"'),
        }),
      )
    })
    const saveCall = apiRequestMock.mock.calls.find(
      ([url, init]) =>
        url === "http://api.local/api/agents/42/triggers/9" &&
        init?.method === "PATCH" &&
        (init as { body?: string }).body?.includes("Edited but unsaved"),
    )
    const body = JSON.parse((saveCall![1] as { body: string }).body)
    expect(body.enabled).toBe(false)
  })

  it("prefers enabling the trigger open in the editor over the first-in-list heuristic", async () => {
    // PR #1051 review, N9 (pre-existing, not introduced by this PR):
    // toggling the type-level switch on while every trigger of that type is
    // disabled used to always enable "the first already-enabled trigger,
    // else the first in the list" — ignoring which trigger is actually open
    // in the editor. Open the OLDER (not first-in-list) of two disabled
    // scheduled triggers and confirm the switch enables THAT one, not the
    // newer one the old heuristic would have picked.
    const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"
    const triggers = [
      makeTrigger({
        id: 202,
        type: "scheduled",
        name: "Newer schedule",
        enabled: false,
        config: { interval_seconds: 3600 },
      }),
      makeTrigger({
        id: 201,
        type: "scheduled",
        name: "Older schedule",
        enabled: false,
        config: { interval_seconds: 3600 },
      }),
    ]
    let patchedId: number | null = null

    apiRequestMock.mockImplementation(
      (url: string, init?: { method?: string; body?: string }) => {
        const list = triggerListResponse(url, init, triggers)
        if (list) return Promise.resolve(list)
        if (url === `${TRIGGERS_URL}/201/runs` || url === `${TRIGGERS_URL}/202/runs`) {
          return Promise.resolve(jsonResponse([]))
        }
        if (
          (url === `${TRIGGERS_URL}/201` || url === `${TRIGGERS_URL}/202`) &&
          init?.method === "PATCH"
        ) {
          patchedId = Number(url.split("/").pop())
          const target = triggers.find((item) => item.id === patchedId)!
          const patch = init.body ? JSON.parse(init.body) : {}
          return Promise.resolve(jsonResponse({ ...target, ...patch }))
        }
        return Promise.resolve(jsonResponse([]))
      },
    )

    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.scheduled.title"))
    // Manage list, newest first: [Newer schedule (202), Older schedule (201)].
    await screen.findByText("Newer schedule")
    const editButtons = screen.getAllByRole("button", { name: "triggers.actions.edit" })
    // Open the OLDER (second-in-list) trigger's editor.
    fireEvent.click(editButtons[1])
    await screen.findByText("triggers.schedule.recurrenceLabel")

    // Both triggers are disabled, so the derived header switch starts off.
    const headerSwitch = screen.getByRole("switch")
    expect(headerSwitch).toHaveAttribute("aria-checked", "false")
    fireEvent.click(headerSwitch)

    await waitFor(() => {
      expect(patchedId).toBe(201)
    })
  })
})

describe("AgentTriggersDialog staging mode (agent not created yet)", () => {
  function stagedWebhook(clientId: number, name: string): StagedTrigger {
    return {
      clientId,
      type: "webhook",
      name,
      enabled: true,
      config: {},
      prompt_template: null,
      secret: null,
    }
  }

  function renderStaging(triggers: StagedTrigger[]) {
    const onChange = vi.fn()
    render(
      <AgentTriggersDialog
        agentId={null}
        open
        onOpenChange={vi.fn()}
        staged={{ triggers, onChange }}
      />,
    )
    return onChange
  }

  async function editStagedWebhookName(name: string) {
    await openWebhookEditor()
    const nameInput = await screen.findByLabelText("triggers.form.name")
    expect(nameInput).toHaveValue("Old name")
    fireEvent.change(nameInput, { target: { value: name } })
  }

  // Unlike renderStaging's vi.fn(), this harness feeds onChange back into the
  // staged prop like agent-builder does, so list updates round-trip and the
  // form-sync behavior under real re-renders is exercised.
  function StatefulStagingHarness({
    initial,
    onChangeSpy,
    onOpenChange,
  }: {
    initial: StagedTrigger[]
    onChangeSpy?: (next: StagedTrigger[]) => void
    onOpenChange?: (open: boolean) => void
  }) {
    const [triggers, setTriggers] = React.useState(initial)
    return (
      <AgentTriggersDialog
        agentId={null}
        open
        onOpenChange={onOpenChange ?? vi.fn()}
        staged={{
          triggers,
          onChange: (next) => {
            onChangeSpy?.(next)
            setTriggers(next)
          },
        }}
      />
    )
  }

  beforeEach(() => {
    apiRequestMock.mockReset()
    apiRequestMock.mockResolvedValue(jsonResponse([]))
  })

  afterEach(() => {
    cleanup()
  })

  it("opens a draft editor when a type is toggled on, staging only on Save", async () => {
    const onChangeSpy = vi.fn()
    render(<StatefulStagingHarness initial={[]} onChangeSpy={onChangeSpy} />)

    await screen.findByText("triggers.cards.webhook.title")

    const [webhookSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookSwitch)

    // Toggling on with no staged webhook opens the draft editor without
    // staging anything yet.
    await screen.findByLabelText("triggers.form.name")
    expect(onChangeSpy).not.toHaveBeenCalled()

    // Save stages the draft, enabled (that's what the toggle-on meant).
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))
    await waitFor(() => {
      expect(onChangeSpy).toHaveBeenCalledWith([
        expect.objectContaining({ clientId: -1, type: "webhook", enabled: true }),
      ])
    })
    // Back on the list, the type-level header switch is now on.
    const [headerSwitch] = screen.getAllByRole("switch")
    expect(headerSwitch).toHaveAttribute("aria-checked", "true")
  })

  it("shows the empty state (not a form) when a type with no triggers is opened via its title", async () => {
    renderStaging([])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))

    expect(await screen.findByText("triggers.cards.webhook.empty.title")).toBeInTheDocument()
    expect(screen.queryByLabelText("triggers.form.name")).not.toBeInTheDocument()

    // The empty state's own CTA opens the same draft form.
    fireEvent.click(screen.getByRole("button", { name: /triggers.cards.webhook.empty.cta/ }))
    await screen.findByLabelText("triggers.form.name")
    expect(screen.getByText("triggers.editor.webhookNew")).toBeInTheDocument()
  })

  it("Save stages the toggled-on draft exactly once, with the edited name", async () => {
    const onChangeSpy = vi.fn()
    render(<StatefulStagingHarness initial={[]} onChangeSpy={onChangeSpy} />)

    const [webhookSwitch] = await screen.findAllByRole("switch")
    fireEvent.click(webhookSwitch)

    const nameInput = await screen.findByLabelText("triggers.form.name")
    fireEvent.change(nameInput, { target: { value: "Toggled hook" } })
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))

    await waitFor(() => {
      expect(onChangeSpy).toHaveBeenCalledWith([
        expect.objectContaining({
          clientId: -1,
          type: "webhook",
          name: "Toggled hook",
          enabled: true,
        }),
      ])
    })
    expect(onChangeSpy).toHaveBeenCalledTimes(1)
  })

  it("applies the card switch to the staged trigger without pressing save", async () => {
    const onChange = renderStaging([stagedWebhook(-1, "Hook one")])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("Hook one")

    // switches: [header master, card]
    const [, cardSwitch] = screen.getAllByRole("switch")
    expect(cardSwitch).toHaveAttribute("aria-checked", "true")
    fireEvent.click(cardSwitch)

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith([
        expect.objectContaining({ clientId: -1, enabled: false }),
      ])
    })
  })

  it("appends a new staged trigger via Add instead of overwriting an existing one", async () => {
    const onChange = renderStaging([stagedWebhook(-1, "First hook")])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("First hook")

    fireEvent.click(screen.getByRole("button", { name: /triggers.actions.addAnotherWebhook/ }))

    // Creation state: an empty draft form.
    expect(await screen.findByLabelText("triggers.form.name")).toHaveValue("")

    fireEvent.change(screen.getByLabelText("triggers.form.name"), {
      target: { value: "Second hook" },
    })
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith([
        expect.objectContaining({ clientId: -1, name: "First hook" }),
        // "Add another" drafts save enabled, like the reference design.
        expect.objectContaining({ clientId: -2, name: "Second hook", type: "webhook", enabled: true }),
      ])
    })
  })

  it("lists staged triggers newest first", async () => {
    renderStaging([stagedWebhook(-1, "Old hook"), stagedWebhook(-2, "New hook")])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))

    // Newest staged trigger (-2) precedes the older one in the card list.
    const newCard = await screen.findByText("New hook")
    const oldCard = screen.getByText("Old hook")
    expect(
      newCard.compareDocumentPosition(oldCard) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it("removes a staged trigger after confirming in the card's popover", async () => {
    const onChange = renderStaging([stagedWebhook(-1, "Doomed hook")])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))

    // The X opens a confirmation popover; the destructive button deletes.
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.delete" }))
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.confirmDelete" }))

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith([])
    })
  })

  it("keeps the trigger when the delete popover is cancelled", async () => {
    const onChange = renderStaging([stagedWebhook(-1, "Kept hook")])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))

    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.delete" }))
    // The form itself also has a "common.cancel" button now (next to Save),
    // so scope this query to the delete-confirm popover specifically.
    const popover = (await screen.findByText("triggers.deleteConfirm")).parentElement as HTMLElement
    fireEvent.click(within(popover).getByRole("button", { name: "common.cancel" }))

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "triggers.actions.confirmDelete" }),
      ).not.toBeInTheDocument()
    })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText("Kept hook")).toBeInTheDocument()
  })

  it("deleting one card keeps the other card intact after the staged list round-trips", async () => {
    render(
      <StatefulStagingHarness
        initial={[stagedWebhook(-1, "Old hook"), stagedWebhook(-2, "New hook")]}
      />,
    )

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("New hook")

    // Delete the older card (cards list newest first, so its trash is second).
    const [, oldHookDelete] = screen.getAllByRole("button", { name: "triggers.actions.delete" })
    fireEvent.click(oldHookDelete)
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.confirmDelete" }))

    await waitFor(() => {
      expect(screen.queryByText("Old hook")).not.toBeInTheDocument()
    })
    expect(screen.getByText("New hook")).toBeInTheDocument()
  })

  it("keeps the header switch usable alongside unsaved edits after a round-trip", async () => {
    render(<StatefulStagingHarness initial={[stagedWebhook(-1, "Hook")]} />)

    await openWebhookEditor()
    const nameInput = await screen.findByLabelText("triggers.form.name")
    fireEvent.change(nameInput, { target: { value: "Renamed but unsaved" } })

    // The immediate type-level toggle round-trips the staged list; the
    // pending name edit must survive it.
    const [headerSwitch] = screen.getAllByRole("switch")
    fireEvent.click(headerSwitch)
    await waitFor(() => {
      expect(headerSwitch).toHaveAttribute("aria-checked", "false")
    })
    expect(screen.getByLabelText("triggers.form.name")).toHaveValue("Renamed but unsaved")
  })

  it("discards unsaved edits (without attempting to save) when the dialog is dismissed via Escape", async () => {
    const onChangeSpy = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <StatefulStagingHarness
        initial={[stagedWebhook(-1, "Old name")]}
        onChangeSpy={onChangeSpy}
        onOpenChange={onOpenChange}
      />,
    )

    await openWebhookEditor()
    const nameInput = await screen.findByLabelText("triggers.form.name")
    fireEvent.change(nameInput, { target: { value: "Unsaved on escape" } })

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })

    // Dismissal never attempts to save the draft (unlike Save, which is the
    // only thing that persists edits) — it just closes.
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
    expect(onChangeSpy).not.toHaveBeenCalled()
  })


  it("deleting one card leaves the other staged triggers untouched in onChange", async () => {
    const onChange = renderStaging([
      stagedWebhook(-1, "Old hook"),
      stagedWebhook(-2, "New hook"),
    ])

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("New hook")

    // Delete the older card (-1) via its trash button (cards newest first).
    const [, oldHookDelete] = screen.getAllByRole("button", { name: "triggers.actions.delete" })
    fireEvent.click(oldHookDelete)
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.confirmDelete" }))

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith([
        expect.objectContaining({ clientId: -2, name: "New hook" }),
      ])
    })
  })

  it("saves pending edits to the staged trigger being edited via the Save button", async () => {
    const onChange = renderStaging([stagedWebhook(-1, "Old name")])

    await editStagedWebhookName("New name")
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith([
        expect.objectContaining({ clientId: -1, name: "New name", type: "webhook" }),
      ])
    })
  })

  it("discards unsaved edits when navigating back to the overview", async () => {
    const onChange = renderStaging([stagedWebhook(-1, "Old name")])

    await editStagedWebhookName("Renamed but unsaved")
    fireEvent.click(screen.getByRole("button", { name: "common.back" }))

    // Back landed on the overview without ever calling onChange — the edit
    // was never saved.
    expect(screen.queryByLabelText("triggers.form.name")).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it("closes without changes when Done is pressed on an untouched form", async () => {
    const onOpenChange = vi.fn()
    const onChange = vi.fn()
    render(
      <AgentTriggersDialog
        agentId={null}
        open
        onOpenChange={onOpenChange}
        staged={{ triggers: [stagedWebhook(-1, "Untouched hook")], onChange }}
      />,
    )

    await openWebhookEditor()
    await screen.findByLabelText("triggers.form.name")
    fireEvent.click(screen.getByRole("button", { name: "common.done" }))

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
    expect(onChange).not.toHaveBeenCalled()
  })

  it("tests a staged trigger locally inside the editor, rendering its prompt", async () => {
    const onChange = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <AgentTriggersDialog
        agentId={null}
        open
        onOpenChange={onOpenChange}
        staged={{ triggers: [], onChange }}
      />,
    )

    // Toggle webhook on → draft editor; give it a template and hit Test.
    await screen.findByText("triggers.cards.webhook.title")
    const [webhookSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookSwitch)
    const promptInput = await screen.findByLabelText("triggers.form.webhookPrompt")
    fireEvent.change(promptInput, {
      target: { value: "Lead: {{payload}} (test={{test}}, type={{trigger_type}})" },
    })

    const testButton = screen.getByRole("button", { name: "triggers.actions.test" })
    expect(testButton).not.toBeDisabled()
    fireEvent.click(testButton)

    // A run row appears right inside the editor (like the reference design),
    // with the rendered prompt — template variables substituted, exactly
    // what a real firing would send to the agent.
    expect(await screen.findByText("triggers.runs.title")).toBeInTheDocument()
    expect(screen.getByText("triggers.test.stagedPreviewNote")).toBeInTheDocument()
    expect(screen.getByText("triggers.runStatus.completed")).toBeInTheDocument()
    expect(screen.getByText(/trigger-run:test:draft:/)).toBeInTheDocument()
    const rendered = screen.getByText(/test=true, type=webhook/)
    expect(rendered.textContent).toContain("test trigger")
    expect(rendered.textContent).not.toContain("{{payload}}")

    // Everything happens in place: no staging, no API call, dialog stays open.
    expect(onChange).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/test"),
      expect.anything(),
    )
  })

  it("disables every staged trigger of a type when its switch is toggled off", async () => {
    const onChange = renderStaging([
      stagedWebhook(-1, "Hook one"),
      stagedWebhook(-2, "Hook two"),
    ])

    await screen.findByText("triggers.cards.webhook.title")
    const [webhookSwitch] = screen.getAllByRole("switch")
    expect(webhookSwitch).toHaveAttribute("aria-checked", "true")

    fireEvent.click(webhookSwitch)

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith([
        expect.objectContaining({ clientId: -1, enabled: false }),
        expect.objectContaining({ clientId: -2, enabled: false }),
      ])
    })
  })
})

describe("AgentTriggersDialog empty states", () => {
  const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"

  beforeEach(() => {
    apiRequestMock.mockReset()
    // Return a fresh Response per call: response bodies can only be read
    // once. The empty-state CTA opens a draft; Save POSTs a real trigger.
    let nextId = 100
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === TRIGGERS_URL && init?.method === "POST") {
        return Promise.resolve(createdTriggerResponse(nextId++, init.body))
      }
      return Promise.resolve(jsonResponse([]))
    })
  })

  afterEach(() => {
    cleanup()
  })

  it("shows the webhook empty state until the switch is turned on", async () => {
    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))

    expect(await screen.findByText("triggers.cards.webhook.empty.title")).toBeInTheDocument()
    expect(screen.queryByLabelText("triggers.form.name")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /triggers.cards.webhook.empty.cta/ }))

    expect(await screen.findByLabelText("triggers.form.name")).toBeInTheDocument()
    expect(screen.queryByText("triggers.cards.webhook.empty.title")).not.toBeInTheDocument()
  })

  it("shows the schedule empty state until a schedule is created", async () => {
    renderDialog()

    fireEvent.click(await screen.findByText("triggers.cards.scheduled.title"))

    expect(await screen.findByText("triggers.cards.scheduled.empty.title")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /triggers.cards.scheduled.empty.cta/ }))

    expect(await screen.findByText("triggers.schedule.recurrenceLabel")).toBeInTheDocument()
  })
})

describe("AgentTriggersDialog schedule recurrence", () => {
  const TRIGGERS_URL = "http://api.local/api/agents/42/triggers"

  beforeEach(() => {
    apiRequestMock.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  // The schedule switch opens a local draft. Save POSTs its configuration;
  // subsequent saves to an existing trigger PATCH it. Track the latest body.
  function mockCreate() {
    let lastBody: Record<string, unknown> | null = null
    apiRequestMock.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      const list = triggerListResponse(url, init, [])
      if (list) return Promise.resolve(list)
      if (url === TRIGGERS_URL && init?.method === "POST") {
        lastBody = init.body ? JSON.parse(init.body) : null
        return Promise.resolve(
          jsonResponse(makeTrigger({ id: 90, type: "scheduled", config: (lastBody?.config as Record<string, unknown>) ?? {} })),
        )
      }
      if (url === `${TRIGGERS_URL}/90` && init?.method === "PATCH") {
        lastBody = init.body ? JSON.parse(init.body) : null
        return Promise.resolve(
          jsonResponse(makeTrigger({ id: 90, type: "scheduled", config: (lastBody?.config as Record<string, unknown>) ?? {} })),
        )
      }
      return Promise.resolve(jsonResponse([]))
    })
    return () => lastBody
  }

  async function openScheduleDraft() {
    renderDialog()
    const [, scheduledCardSwitch] = await screen.findAllByRole("switch")
    fireEvent.click(scheduledCardSwitch)
    await screen.findByText("triggers.schedule.recurrenceLabel")
  }

  // toEqual (not toMatchObject) against the FULL config object: a partial
  // match would stay green even if a field (e.g. time_of_day, start_at) is
  // silently dropped from buildConfig, since it simply wouldn't be listed
  // in the expectation either. Dynamic fields (timezone / the anchor
  // timestamp) are asserted via expect.any(String) rather than mocking the
  // clock and Intl locale.
  async function saveAndGetConfig(getBody: () => Record<string, unknown> | null) {
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))
    await waitFor(() => {
      expect(getBody()).not.toBeNull()
    })
    return (getBody() as { config: Record<string, unknown> }).config
  }

  it("saves an hourly schedule with the default recurrence", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    const config = await saveAndGetConfig(getBody)

    // Hourly is a flat repeat interval with no fixed civil time to honor, so
    // it (like custom) keeps the interval_seconds/next_run_at mechanism and
    // omits time_of_day/timezone entirely — the backend schema now rejects
    // them outright for hourly/custom (see _require_schedule) so the two
    // mechanisms stay enforceably disjoint, not just conventionally so.
    expect(config).toEqual({
      recurrence: "hourly",
      interval_seconds: 3600,
      next_run_at: expect.any(String),
    })
  })

  it("saves a daily schedule", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.click(screen.getByText("triggers.schedule.daily"))
    const config = await saveAndGetConfig(getBody)

    // Daily has a fixed civil time to honor every day, so — unlike
    // hourly/custom — it's routed through the timezone-aware occurrence
    // mechanism (start_at), not a flat interval that would drift across DST.
    // start_at is a bare "YYYY-MM-DD" date, not a full ISO instant — sending
    // an instant here would materialize it in the BROWSER's zone while the
    // backend combines it with the trigger's own `timezone`, silently
    // disagreeing whenever they differ (PR #1051 review).
    expect(config).toEqual({
      recurrence: "daily",
      time_of_day: "09:00",
      timezone: expect.any(String),
      start_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    })
  })

  it("saves a weekly schedule with the selected weekdays", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.click(screen.getByText("triggers.schedule.weekly"))
    // Default weekday selection is Monday (index 0); add Wednesday (index 2).
    fireEvent.click(await screen.findByText("triggers.schedule.weekdayWed"))
    const config = await saveAndGetConfig(getBody)

    expect(config).toEqual({
      recurrence: "weekly",
      time_of_day: "09:00",
      timezone: expect.any(String),
      weekdays: [0, 2],
      start_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    })
  })

  it("saves a monthly schedule with the default day of month", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.click(screen.getByText("triggers.schedule.monthly"))
    const config = await saveAndGetConfig(getBody)

    expect(config).toEqual({
      recurrence: "monthly",
      time_of_day: "09:00",
      timezone: expect.any(String),
      day_of_month: 1,
      start_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    })
  })

  it("saves a custom schedule converting amount+unit into interval_seconds", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.click(screen.getByText("triggers.schedule.custom"))
    const amountInput = await screen.findByLabelText("triggers.schedule.runEvery")
    fireEvent.change(amountInput, { target: { value: "2" } })
    // Default unit is minutes: 2 minutes = 120 seconds.
    const config = await saveAndGetConfig(getBody)

    expect(config).toEqual({
      recurrence: "custom",
      interval_seconds: 120,
      next_run_at: expect.any(String),
    })
  })

  // PR #1051 review, N2: the backend's documented cron semantics fire an
  // hourly/custom schedule immediately on the next scan tick when its picked
  // start instant has already passed (its own test on the backend side) —
  // this is UI-only, warning the user up front instead of surprising them
  // after Save. daily/weekly/monthly never "catch up" that way, so the hint
  // must not appear for them even with a past start date.
  it("warns inline when an hourly schedule's picked start instant has already passed", async () => {
    await openScheduleDraft()

    // Default recurrence is hourly; set the start date far enough in the
    // past that the anchor (start date + its own time input) is
    // unambiguously behind "now".
    fireEvent.change(document.getElementById("schedule-start-date") as HTMLInputElement, {
      target: { value: "2020-01-01" },
    })

    expect(
      await screen.findByText("triggers.schedule.runsImmediatelyHint"),
    ).toBeInTheDocument()
  })

  it("does not warn when an hourly schedule's picked start instant is in the future", async () => {
    await openScheduleDraft()

    fireEvent.change(document.getElementById("schedule-start-date") as HTMLInputElement, {
      target: { value: "2999-01-01" },
    })

    expect(
      screen.queryByText("triggers.schedule.runsImmediatelyHint"),
    ).not.toBeInTheDocument()
  })

  it("does not warn for a daily schedule even with a past start date", async () => {
    await openScheduleDraft()
    fireEvent.click(screen.getByText("triggers.schedule.daily"))

    fireEvent.change(document.getElementById("schedule-start-date") as HTMLInputElement, {
      target: { value: "2020-01-01" },
    })

    expect(
      screen.queryByText("triggers.schedule.runsImmediatelyHint"),
    ).not.toBeInTheDocument()
  })

  // PR #1051 review, N2 follow-up: scheduleFieldsFromConfig derives
  // startDate/timeOfDay from the trigger's STORED config, which is frozen
  // at creation time and never rewritten by the backend scan loop (only the
  // next_run_at DB COLUMN is advanced — see triggers.py's
  // _apply_trigger_updates). So any hourly/custom trigger that's already
  // fired at least once reconstructs with a past startDate on every reopen,
  // even though resaving with no schedule-relevant change will NOT actually
  // recompute next_run_at server-side (_schedule_signature sees no diff) —
  // the warning must only fire when a schedule-relevant field genuinely
  // differs from what was loaded.
  it("does not warn when reopening an already-fired hourly trigger with only an unrelated field changed", async () => {
    const trigger = makeTrigger({
      id: 96,
      type: "scheduled",
      name: "Old hourly",
      config: {
        recurrence: "hourly",
        interval_seconds: 3600,
        next_run_at: "2020-01-01T09:00:00+00:00",
      },
    })
    mockTriggerList(trigger)
    renderDialog()
    await openScheduledEditor()

    // Reopening alone (no edits at all yet) must not warn.
    expect(
      screen.queryByText("triggers.schedule.runsImmediatelyHint"),
    ).not.toBeInTheDocument()

    // An edit to a field that has nothing to do with the schedule (the
    // prompt) must not make the (still-unchanged) past anchor start warning.
    const promptField = await screen.findByLabelText("triggers.form.schedulePrompt")
    fireEvent.change(promptField, { target: { value: "Say hello" } })

    expect(
      screen.queryByText("triggers.schedule.runsImmediatelyHint"),
    ).not.toBeInTheDocument()
  })

  it("still warns when an existing hourly trigger's schedule is genuinely edited into the past", async () => {
    const trigger = makeTrigger({
      id: 97,
      type: "scheduled",
      name: "Old hourly",
      config: {
        recurrence: "hourly",
        interval_seconds: 3600,
        next_run_at: "2999-01-01T09:00:00+00:00",
      },
    })
    mockTriggerList(trigger)
    renderDialog()
    await openScheduledEditor()

    // No warning yet: the loaded anchor (2999) is in the future.
    expect(
      screen.queryByText("triggers.schedule.runsImmediatelyHint"),
    ).not.toBeInTheDocument()

    // A genuine schedule edit — moving the start date into the past —
    // differs from what was loaded, so the warning must still fire.
    fireEvent.change(document.getElementById("schedule-start-date") as HTMLInputElement, {
      target: { value: "2020-01-01" },
    })

    expect(
      await screen.findByText("triggers.schedule.runsImmediatelyHint"),
    ).toBeInTheDocument()
  })

  // One test per buildConfig validation-throw path: none of these were
  // exercised at all before, so a regression in any of them (e.g. a
  // guard silently removed) would ship undetected.
  it("rejects saving when the start date is cleared", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.change(document.getElementById("schedule-start-date") as HTMLInputElement, {
      target: { value: "" },
    })
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith("triggers.validation.startDate")
    })
    expect(getBody()).toBeNull()
  })

  it("rejects saving a weekly schedule with no weekday selected", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.click(screen.getByText("triggers.schedule.weekly"))
    // Deselect the only (default) selected day, Monday.
    fireEvent.click(screen.getByText("triggers.schedule.weekdayMon"))
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith("triggers.validation.scheduleRequired")
    })
    expect(getBody()).toBeNull()
  })

  it("rejects saving a custom schedule with a non-positive amount", async () => {
    const getBody = mockCreate()
    await openScheduleDraft()

    fireEvent.click(screen.getByText("triggers.schedule.custom"))
    const amountInput = await screen.findByLabelText("triggers.schedule.runEvery")
    fireEvent.change(amountInput, { target: { value: "0" } })
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith("triggers.validation.interval")
    })
    expect(getBody()).toBeNull()
  })

  // buildConfig's `Number.isNaN(anchor.getTime())` guard (triggers.validation
  // .nextRunAt) has no test here: both <input type="date"> and
  // type="time"> self-sanitize an invalid or out-of-range value to "" per
  // the HTML spec (verified directly against jsdom — a malformed string and
  // a real-but-nonexistent date like Feb 30 both land as ""), which is then
  // caught by the empty-startDate check above instead, or defaulted to
  // "00:00" for the time. The guard is unreachable through the actual
  // editor UI; only a caller constructing a form value directly could hit
  // it, and buildConfig is a component-scoped closure, not an exported unit.

  it("preserves an existing trigger's stored timezone instead of re-deriving the browser's", async () => {
    // PR #1051 review: buildConfig used to call Intl.DateTimeFormat()
    // .resolvedOptions().timeZone fresh on every Save. Editing a schedule
    // from a machine in a different zone than it was created in — without
    // touching the schedule at all — would silently relocate it, since the
    // backend's recompute gate couldn't tell that apart from a real edit.
    const trigger = makeTrigger({
      id: 91,
      type: "scheduled",
      config: {
        recurrence: "daily",
        time_of_day: "09:00",
        timezone: "Asia/Shanghai",
        start_at: "2026-01-01T01:00:00+00:00",
      },
    })
    mockTriggerList(trigger, true)

    // Simulate editing from a browser in a DIFFERENT zone than the stored
    // one — only the no-arg "what's my current zone" call is faked;
    // locale-formatting calls (new Intl.DateTimeFormat(locale, options)) in
    // schedule-fields.tsx's own summary/label rendering still delegate to
    // the real implementation.
    const dtfSpy = mockBrowserTimezone("America/New_York")
    try {
      renderDialog()
      await openScheduledEditor()

      // The displayed label reflects the STORED zone, not the browser's.
      expect(await screen.findByText("triggers.schedule.timezoneLabel:Asia/Shanghai")).toBeInTheDocument()

      fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))
      await waitFor(() => {
        expect(apiRequestMock).toHaveBeenCalledWith(
          `${TRIGGERS_URL}/91`,
          expect.objectContaining({ method: "PATCH" }),
        )
      })
      const patchCall = apiRequestMock.mock.calls.find(
        ([url, init]) => url === `${TRIGGERS_URL}/91` && init?.method === "PATCH",
      )
      const body = JSON.parse((patchCall![1] as { body: string }).body)
      expect(body.config.timezone).toBe("Asia/Shanghai")
    } finally {
      dtfSpy.mockRestore()
    }
  })

  it("falls back to the browser's timezone, not hardcoded UTC, when an hourly trigger is switched to a calendar recurrence", async () => {
    // PR #1051 review, F2: scheduleFieldsFromConfig's hourly/custom branch
    // hardcoded timezone: configString(config, "timezone") || "UTC". Since
    // hourly/custom configs can never carry a stored timezone (the backend
    // schema rejects it for them), this always evaluated to "UTC". The bug
    // only becomes externally observable once the trigger is switched to a
    // calendar recurrence (buildConfig omits timezone entirely for hourly/
    // custom, so a chip round-trip that stays on an interval recurrence
    // can't surface it) — reopening an hourly trigger, switching to daily,
    // and saving must use the browser's own zone, not UTC.
    const trigger = makeTrigger({
      id: 95,
      type: "scheduled",
      config: {
        recurrence: "hourly",
        interval_seconds: 3600,
        next_run_at: "2026-01-01T09:00:00+00:00",
      },
    })
    mockTriggerList(trigger, true)

    const dtfSpy = mockBrowserTimezone("America/New_York")
    try {
      renderDialog()
      await openScheduledEditor()

      fireEvent.click(screen.getByText("triggers.schedule.daily"))
      fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))

      await waitFor(() => {
        expect(apiRequestMock).toHaveBeenCalledWith(
          `${TRIGGERS_URL}/95`,
          expect.objectContaining({ method: "PATCH" }),
        )
      })
      const patchCall = apiRequestMock.mock.calls.find(
        ([url, init]) => url === `${TRIGGERS_URL}/95` && init?.method === "PATCH",
      )
      const body = JSON.parse((patchCall![1] as { body: string }).body)
      expect(body.config.timezone).toBe("America/New_York")
    } finally {
      dtfSpy.mockRestore()
    }
  })

  it("reconstructs timeOfDay/startDate when reopening an existing trigger, including a legacy config with only an anchor", async () => {
    // PR #1051 review: the timeOfDay-derivation fix (anchor-derived, not a
    // hardcoded "09:00") was previously only exercised via the create path.
    // 20:30 UTC on 2026-03-15 is 2026-03-16 04:30 in Asia/Shanghai (UTC+8) —
    // deliberately crossing a calendar-day boundary, so a test-runner
    // machine that happens to sit in a zone matching the trigger's OWN
    // configured zone can't mask a regression back to reading the machine's
    // zone instead (see zonedIsoDate/zonedTimeOfDay in buildConfig's sibling
    // read path, scheduleFieldsFromConfig).
    const withTimeOfDayAnchor = new Date("2026-03-15T20:30:00+00:00")
    const withTimeOfDay = makeTrigger({
      id: 92,
      type: "scheduled",
      config: {
        recurrence: "daily",
        time_of_day: "14:30",
        timezone: "Asia/Shanghai",
        start_at: withTimeOfDayAnchor.toISOString(),
      },
    })
    mockTriggerList(withTimeOfDay)
    renderDialog()
    await openScheduledEditor()
    // The stored time_of_day ("14:30") is authoritative regardless of any
    // zone — it's used verbatim, not derived from the anchor.
    expect(await screen.findByLabelText("triggers.schedule.atWhatTime")).toHaveValue("14:30")
    // startDate for a calendar recurrence's legacy (full-ISO) anchor comes
    // from the anchor's date in the TRIGGER's OWN configured zone — Mar 16
    // in Shanghai, not Mar 15 (which is what the raw UTC instant, or the
    // machine's own unrelated local zone, would show).
    expect(document.getElementById("schedule-start-date")).toHaveValue(
      zonedIsoDate(withTimeOfDayAnchor, "Asia/Shanghai"),
    )
    expect(document.getElementById("schedule-start-date")).toHaveValue("2026-03-16")
    cleanup()

    // Legacy config: no `recurrence`/`time_of_day` at all, only the flat
    // interval mechanism's anchor — timeOfDay must come from the anchor
    // timestamp itself, converted to the LOCAL calendar date/time (hence
    // computing the expected values the same way the component does,
    // rather than hardcoding UTC-relative ones — the conversion legitimately
    // depends on the machine's own timezone).
    const anchorIso = "2026-04-01T16:45:00+00:00"
    const anchorDate = new Date(anchorIso)
    const legacy = makeTrigger({
      id: 93,
      type: "scheduled",
      config: { interval_seconds: 86400, next_run_at: anchorIso },
    })
    mockTriggerList(legacy)
    renderDialog()
    await openScheduledEditor()
    expect(await screen.findByLabelText("triggers.schedule.atWhatTime")).toHaveValue(
      localTimeOfDay(anchorDate),
    )
    expect(document.getElementById("schedule-start-date")).toHaveValue(localIsoDate(anchorDate))
  })

  it("defaults the start date to today when reopening a calendar trigger with no stored start_at", async () => {
    // PR #1051 review, F5: start_at is genuinely optional for daily/weekly/
    // monthly at the backend schema level, but buildConfig requires a
    // startDate to save at all. Without a fallback, a calendar trigger
    // created via the API with no start_at would reconstruct with a blank
    // startDate and become permanently uneditable in this dialog — Save
    // would always throw triggers.validation.startDate.
    const noStartAt = makeTrigger({
      id: 94,
      type: "scheduled",
      config: { recurrence: "daily", time_of_day: "09:00", timezone: "UTC" },
    })
    mockTriggerList(noStartAt, true)
    renderDialog()
    await openScheduledEditor()

    // The fixture's configured timezone is "UTC" (see noStartAt above), and
    // the code under test computes today's date via
    // zonedIsoDate(new Date(), configuredTimezone) — asserting against
    // localIsoDate(new Date()) (the TEST MACHINE's own local timezone)
    // instead made this flaky: it only passed when the CI runner's local
    // calendar date happened to match UTC's at the moment of the assertion.
    expect(document.getElementById("schedule-start-date")).toHaveValue(
      zonedIsoDate(new Date(), "UTC"),
    )

    // And Save (unrelated no-op edit) must succeed rather than throwing the
    // "start date is required" validation error. Clear prior call history
    // first: an earlier test in this file legitimately triggers this same
    // toast message, so only calls from THIS Save click matter here.
    toastMocks.error.mockClear()
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveSchedule" }))
    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        `${TRIGGERS_URL}/94`,
        expect.objectContaining({ method: "PATCH" }),
      )
    })
    expect(toastMocks.error).not.toHaveBeenCalled()
  })

  it("shows a legacy one-shot config (next_run_at only, no interval_seconds) as custom, not hourly", async () => {
    // PR #1051 review: `Number(configNumber(config, "interval_seconds")) ||
    // 3600` treated a MISSING interval_seconds the same as an explicit
    // 3600 — defaulting the pill to "Hourly" for what is actually a
    // deliberate one-shot (the backend fires it once, then disables it; see
    // test_scheduled_scan_disables_one_shot_trigger). A no-op Save on that
    // pill would have written interval_seconds: 3600, silently and
    // irreversibly turning it into a perpetual hourly job.
    const oneShot = makeTrigger({
      id: 94,
      type: "scheduled",
      config: { next_run_at: "2026-05-01T09:00:00+00:00" },
    })
    mockTriggerList(oneShot)
    renderDialog()
    await openScheduledEditor()

    expect(await screen.findByText("triggers.schedule.custom")).toHaveClass(
      "border-primary",
    )
    expect(screen.queryByText("triggers.schedule.hourly")).not.toHaveClass("border-primary")
  })
})

describe("AgentTriggersDialog owner routing", () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    apiRequestMock.mockImplementation(() => Promise.resolve(jsonResponse([])))
  })

  afterEach(() => {
    cleanup()
  })

  function renderWorkforceDialog() {
    return render(
      <AgentTriggersDialog
        agentId={null}
        owner={{ kind: "workforce", id: 5 }}
        open
        onOpenChange={vi.fn()}
      />,
    )
  }

  it("loads triggers from the workforce route when owner is a workforce", async () => {
    renderWorkforceDialog()

    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/workforces/5/triggers",
      ),
    )
    // The workforce owner must never fall through to the agent route.
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/agents/"),
    )
  })

  it("loads triggers from the agent route when no explicit owner is given", async () => {
    renderDialog()

    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/agents/42/triggers",
      ),
    )
  })

  it("toggles a workforce-owned trigger via PATCH on the workforce route", async () => {
    const WORKFORCE_TRIGGERS_URL = "http://api.local/api/workforces/5/triggers"
    const trigger = makeTrigger({ id: 60, name: "Workforce hook", enabled: true })

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === WORKFORCE_TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse([trigger]))
      }
      if (url === `${WORKFORCE_TRIGGERS_URL}/60` && init?.method === "PATCH") {
        return Promise.resolve(jsonResponse({ ...trigger, enabled: false }))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderWorkforceDialog()

    const cardSwitch = (
      await screen.findAllByRole("switch")
    ).find((el) => el.getAttribute("aria-checked") === "true")
    expect(cardSwitch).toBeDefined()
    fireEvent.click(cardSwitch!)

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        `${WORKFORCE_TRIGGERS_URL}/60`,
        expect.objectContaining({ method: "PATCH" }),
      )
    })
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/agents/"),
      expect.anything(),
    )
  })

  it("toggles an existing workforce-owned trigger from the detail view via PATCH", async () => {
    const WORKFORCE_TRIGGERS_URL = "http://api.local/api/workforces/5/triggers"
    const trigger = makeTrigger({ id: 63, name: "Workforce hook", enabled: true })

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === WORKFORCE_TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse([trigger]))
      }
      if (url === `${WORKFORCE_TRIGGERS_URL}/63/runs`) {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === `${WORKFORCE_TRIGGERS_URL}/63` && init?.method === "PATCH") {
        return Promise.resolve(jsonResponse({ ...trigger, enabled: false }))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderWorkforceDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("Workforce hook")
    const [headerSwitch] = screen.getAllByRole("switch")
    expect(headerSwitch).toHaveAttribute("aria-checked", "true")
    fireEvent.click(headerSwitch)

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        `${WORKFORCE_TRIGGERS_URL}/63`,
        expect.objectContaining({ method: "PATCH" }),
      )
    })
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/agents/"),
      expect.anything(),
    )
  })

  it("creates a workforce-owned trigger via POST on the workforce route", async () => {
    const WORKFORCE_TRIGGERS_URL = "http://api.local/api/workforces/5/triggers"

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === WORKFORCE_TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse([]))
      }
      if (url === WORKFORCE_TRIGGERS_URL && init?.method === "POST") {
        return Promise.resolve(jsonResponse(makeTrigger({ id: 61, enabled: true })))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderWorkforceDialog()

    await screen.findByText("triggers.cards.webhook.title")
    const [webhookCardSwitch] = screen.getAllByRole("switch")
    fireEvent.click(webhookCardSwitch)
    await screen.findByLabelText("triggers.form.secret")
    fireEvent.click(screen.getByRole("button", { name: "triggers.actions.saveWebhook" }))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        WORKFORCE_TRIGGERS_URL,
        expect.objectContaining({ method: "POST" }),
      )
    })
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/agents/"),
      expect.anything(),
    )
  })

  it("deletes a workforce-owned trigger via DELETE on the workforce route", async () => {
    const WORKFORCE_TRIGGERS_URL = "http://api.local/api/workforces/5/triggers"
    const trigger = makeTrigger({ id: 62, name: "Workforce hook" })

    apiRequestMock.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === WORKFORCE_TRIGGERS_URL && (!init?.method || init.method === "GET")) {
        return Promise.resolve(jsonResponse([trigger]))
      }
      if (url === `${WORKFORCE_TRIGGERS_URL}/62` && init?.method === "DELETE") {
        return Promise.resolve(jsonResponse({}))
      }
      return Promise.resolve(jsonResponse([]))
    })

    renderWorkforceDialog()

    fireEvent.click(await screen.findByText("triggers.cards.webhook.title"))
    await screen.findByText("Workforce hook")
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.delete" }))
    fireEvent.click(await screen.findByRole("button", { name: "triggers.actions.confirmDelete" }))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        `${WORKFORCE_TRIGGERS_URL}/62`,
        expect.objectContaining({ method: "DELETE" }),
      )
    })
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/agents/"),
      expect.anything(),
    )
  })
})
