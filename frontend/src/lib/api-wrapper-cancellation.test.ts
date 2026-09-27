import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { AUTH_CACHE_KEY, readAuthCache } from "@/lib/auth-cache"
import { apiRequest } from "@/lib/api-wrapper"

const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks")

beforeEach(() => {
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: { request: async (_name: string, action: () => unknown) => action() },
  })
  localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({
    schemaVersion: 2, sessionId: "cancelled-request", credentialRevision: 0, profileRevision: 0,
    user: { id: "1", username: "alice", is_admin: false }, token: "access", refreshToken: "refresh",
    timestamp: Date.now(),
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  if (originalLocks) Object.defineProperty(navigator, "locks", originalLocks)
  else Reflect.deleteProperty(navigator, "locks")
})

it("does not turn caller cancellation into a successful retried operation", async () => {
  const controller = new AbortController()
  const cancelled = new DOMException("Page disposed", "AbortError")
  vi.spyOn(globalThis, "fetch")
    .mockImplementationOnce(async () => {
      controller.abort(cancelled)
      throw cancelled
    })
    .mockResolvedValue(new Response("late operation", { status: 200 }))

  await expect(apiRequest("/api/chat/tasks", { signal: controller.signal })).rejects.toBe(cancelled)
})

it("does not clear authentication when a cancelled request finishes with a stale 401", async () => {
  const controller = new AbortController()
  const cancelled = new DOMException("Page disposed", "AbortError")
  vi.spyOn(globalThis, "fetch").mockImplementationOnce(async () => {
    controller.abort(cancelled)
    return new Response(null, { status: 401, headers: { "Error-Type": "InvalidToken" } })
  })

  await expect(apiRequest("/api/chat/tasks", { signal: controller.signal })).rejects.toBe(cancelled)
  expect(readAuthCache()?.token).toBe("access")
})

it("still clears rejected credentials for an active request", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(null, { status: 401, headers: { "Error-Type": "InvalidToken" } }),
  )
  const response = await apiRequest("/api/chat/tasks")
  expect(response.status).toBe(401)
  expect(readAuthCache()).toBeNull()
})
