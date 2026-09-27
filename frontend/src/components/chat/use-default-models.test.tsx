import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { useDefaultModels } from "./use-default-models"

const request = vi.hoisted(() => vi.fn())
vi.mock("@/lib/api-wrapper", () => ({ apiRequest: request }))
vi.mock("@/lib/utils", () => ({ getApiUrl: () => "http://api.local" }))

function pendingBody() {
  let resolve!: (value: unknown) => void
  let reject!: (error: Error) => void
  const body = new Promise<unknown>((done, fail) => { resolve = done; reject = fail })
  const json = vi.fn(() => body)
  return { response: { ok: true, json }, resolve, reject, json }
}

function pageEvent(type: "pagehide" | "pageshow", persisted = false) {
  act(() => window.dispatchEvent(Object.assign(new Event(type), { persisted })))
}

beforeEach(() => request.mockReset())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it("does not publish stale models or continue to defaults after pagehide interrupts a body", async () => {
  const body = pendingBody()
  request.mockResolvedValue(body.response)
  const delivered = vi.fn()
  const { result } = renderHook(() => useDefaultModels(false, delivered))
  await waitFor(() => expect(body.json).toHaveBeenCalled())
  pageEvent("pagehide")
  await act(async () => body.resolve([{ model_id: "stale", is_default: true }]))
  expect(result.current.models).toEqual([])
  expect(result.current.defaultAgentConfig.model).toBe("")
  expect(delivered).not.toHaveBeenCalled()
  expect(request).toHaveBeenCalledTimes(1)
})

it.each(["pagehide", "unmount", "hideConfig"] as const)(
  "cancels the pending body on %s without treating disposal as a service failure",
  async (disposal) => {
    const body = pendingBody()
    request.mockResolvedValue(body.response)
    const delivered = vi.fn()
    const log = vi.spyOn(console, "error").mockImplementation(() => {})
    const hook = renderHook(({ hidden }) => useDefaultModels(hidden, delivered), {
      initialProps: { hidden: false },
    })
    await waitFor(() => expect(body.json).toHaveBeenCalled())
    if (disposal === "pagehide") pageEvent("pagehide")
    else if (disposal === "unmount") hook.unmount()
    else hook.rerender({ hidden: true })
    await act(async () => body.reject(new TypeError("body interrupted")))
    expect(log).not.toHaveBeenCalled()
    expect(delivered).not.toHaveBeenCalled()
  },
)

it("restores usable defaults without accepting the old body's late response", async () => {
  const old = pendingBody()
  const list = new Response(JSON.stringify([{ model_id: "fallback", is_default: true }]))
  request
    .mockResolvedValueOnce(list)
    .mockResolvedValueOnce(old.response)
    .mockResolvedValueOnce(new Response(JSON.stringify([{ model_id: "fresh", is_default: true }])))
    .mockResolvedValueOnce(new Response(JSON.stringify([
      { config_type: "general", model: { model_id: "fresh" } },
    ])))
  const delivered = vi.fn()
  const { result } = renderHook(() => useDefaultModels(false, delivered))
  await waitFor(() => expect(old.json).toHaveBeenCalled())
  pageEvent("pagehide")
  pageEvent("pageshow", true)
  await waitFor(() => expect(result.current.defaultAgentConfig.model).toBe("fresh"))
  await act(async () => old.resolve([{ config_type: "general", model: { model_id: "stale" } }]))
  expect(result.current.defaultAgentConfig.model).toBe("fresh")
  expect(delivered).toHaveBeenCalledTimes(1)
  expect(delivered).toHaveBeenCalledWith(expect.objectContaining({ model: "fresh" }))
  pageEvent("pageshow")
  expect(request).toHaveBeenCalledTimes(4)
})

it("reports an active body failure rather than broadly suppressing TypeError", async () => {
  const body = pendingBody()
  request.mockResolvedValue(body.response)
  const delivered = vi.fn()
  const log = vi.spyOn(console, "error").mockImplementation(() => {})
  renderHook(() => useDefaultModels(false, delivered))
  await waitFor(() => expect(body.json).toHaveBeenCalled())
  const failure = new TypeError("active stream failed")
  await act(async () => body.reject(failure))
  expect(log).toHaveBeenCalledWith("Failed to fetch default models:", failure)
})
