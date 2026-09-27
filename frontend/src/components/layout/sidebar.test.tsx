import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { Globe } from "lucide-react"
import React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { NavigationGroup } from "@/lib/sidebar-navigation"

import { Sidebar } from "./sidebar"

const authState = vi.hoisted(() => ({
  logout: vi.fn<() => Promise<boolean>>(),
  user: {
    id: "1",
    username: "acct_0123456789abcdef0123456789abcdef",
    email: "alice@example.com",
  },
}))
const toast = vi.hoisted(() => ({ error: vi.fn() }))
const routeState = vi.hoisted(() => ({ pathname: "/task" }))
const navState = vi.hoisted(() => ({ groups: [] as unknown[] }))

vi.mock("next/navigation", () => ({ usePathname: () => routeState.pathname, useRouter: () => ({ push: vi.fn() }) }))
vi.mock("next/image", () => ({ default: (props: React.ImgHTMLAttributes<HTMLImageElement>) => <img {...props} /> }))
vi.mock("next/link", () => ({ default: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props}>{children}</a> }))
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => authState }))
vi.mock("@/contexts/app-context-chat", () => ({ useApp: () => ({ state: { lastTaskUpdate: 0 } }) }))
vi.mock("@/contexts/i18n-context", () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock("@/components/ui/sonner", () => ({ toast }))
vi.mock("@/lib/branding", () => ({ getBrandingFromEnv: () => ({ appName: "Xagent" }) }))
vi.mock("@/lib/extra-nav", () => ({ default: [] }))
vi.mock("@/lib/sidebar-navigation", () => ({
  getNavigationGroupsForUser: () => navState.groups, getUserMenuItemsForUser: () => [],
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function taskResponse(body: Promise<unknown>) {
  const response = new Response(null, { status: 200 })
  response.json = vi.fn(() => body)
  return response
}

function pageTransition(name: "pagehide" | "pageshow", persisted = true) {
  const event = new Event(name) as PageTransitionEvent
  Object.defineProperty(event, "persisted", { value: persisted })
  window.dispatchEvent(event)
}

describe("Sidebar task list page lifetime", () => {
  beforeEach(() => {
    routeState.pathname = "/task"
    navState.groups = []
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it.each(["pagehide", "unmount"] as const)("aborts a pending task body on %s without reporting a cancelled load", async disposal => {
    const body = deferred<unknown>()
    const response = taskResponse(body.promise)
    const error = vi.spyOn(console, "error").mockImplementation(() => {})
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (!url.includes("/api/chat/tasks")) return Promise.resolve(new Response(null, { status: 500 }))
      return Promise.resolve(response)
    }))

    const view = render(<Sidebar />)
    await waitFor(() => expect(response.json).toHaveBeenCalledOnce())
    if (disposal === "pagehide") act(() => pageTransition("pagehide"))
    else view.unmount()
    await act(async () => body.reject(new TypeError("Failed to fetch")))
    expect(error).not.toHaveBeenCalledWith("Failed to load tasks:", expect.anything())
  })


  it("restores a usable task list while an old cancelled body finishes late", async () => {
    const oldBody = deferred<unknown>()
    const restoredBody = deferred<unknown>()
    const laterBody = deferred<unknown>()
    const responses = [oldBody, restoredBody, laterBody].map(body => taskResponse(body.promise))
    const signals: AbortSignal[] = []
    vi.stubGlobal("fetch", vi.fn((url: string, options?: RequestInit) => {
      if (!url.includes("/api/chat/tasks")) return Promise.resolve(new Response(null, { status: 500 }))
      signals.push(options?.signal as AbortSignal)
      return Promise.resolve(responses[signals.length - 1])
    }))

    render(<Sidebar />)
    await waitFor(() => expect(responses[0].json).toHaveBeenCalledOnce())
    act(() => pageTransition("pagehide"))
    act(() => pageTransition("pageshow"))
    await waitFor(() => expect(responses[1].json).toHaveBeenCalledOnce())

    await act(async () => oldBody.resolve({
      tasks: [{ task_id: "old", title: "Stale task", status: "completed" }],
      pagination: { total_pages: 1 },
    }))
    expect(screen.queryByText("Stale task")).not.toBeInTheDocument()
    expect(screen.queryByText("common.noData")).not.toBeInTheDocument()

    await act(async () => restoredBody.resolve({
      tasks: [{ task_id: "fresh", title: "Restored task", status: "completed" }],
      pagination: { total_pages: 1 },
    }))
    expect(screen.getByRole("link", { name: /Restored task/ })).toBeInTheDocument()

    act(() => pageTransition("pagehide"))
    act(() => pageTransition("pageshow"))
    await waitFor(() => expect(responses[2].json).toHaveBeenCalledOnce())
    act(() => pageTransition("pagehide"))
    await act(async () => laterBody.resolve({
      tasks: [{ task_id: "later", title: "Disposed task", status: "completed" }],
      pagination: { total_pages: 1 },
    }))
    expect(screen.queryByText("Disposed task")).not.toBeInTheDocument()
  })

  it("reloads the displayed search after restoring before its debounce fires", async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(url.includes("/api/chat/tasks")
        ? new Response(JSON.stringify({ tasks: [], pagination: { total_pages: 1 } }))
        : new Response(null, { status: 500 }))
    )
    vi.stubGlobal("fetch", fetchMock)

    render(<Sidebar />)
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes("/api/chat/tasks"))).toBe(true))
    fireEvent.click(screen.getByText("nav.history").nextElementSibling!)
    fireEvent.change(screen.getByPlaceholderText("nav.search"), { target: { value: "restored" } })
    const beforeRestore = fetchMock.mock.calls.filter(([url]) => url.includes("/api/chat/tasks")).length
    act(() => pageTransition("pagehide"))
    act(() => pageTransition("pageshow"))
    const taskRequests = fetchMock.mock.calls.filter(([url]) => url.includes("/api/chat/tasks"))
    expect(taskRequests).toHaveLength(beforeRestore + 1)
    expect(taskRequests.at(-1)?.[0]).toContain("&search=restored")
  })

  it("still reports task response body failures while the page is active", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {})
    const failure = new TypeError("Failed to fetch")
    vi.stubGlobal("fetch", vi.fn((url: string) =>
      Promise.resolve(url.includes("/api/chat/tasks")
        ? taskResponse(Promise.reject(failure))
        : new Response(null, { status: 500 }))
    ))

    render(<Sidebar />)
    await waitFor(() => expect(error).toHaveBeenCalledWith("Failed to load tasks:", failure))
  })
})

describe("Sidebar logout", () => {
  beforeEach(() => {
    authState.logout.mockReset()
    toast.error.mockReset()
    authState.logout.mockResolvedValue(false)
    routeState.pathname = "/task"
    navState.groups = []
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    cleanup()
  })

  it("keeps the menu open and reports a localized failure when logout cannot clear auth", async () => {
    render(<Sidebar />)
    const userMenu = screen.getByRole("button", { name: /alice@example\.com/i })
    expect(screen.queryByText("acct_0123456789abcdef0123456789abcdef")).not.toBeInTheDocument()
    fireEvent.click(userMenu)
    fireEvent.click(screen.getByRole("button", { name: "sidebar.user.logoutTitle" }))
    await waitFor(() => expect(authState.logout).toHaveBeenCalledOnce())
    expect(toast.error).toHaveBeenCalledWith("sidebar.user.logoutFailed")
    expect(screen.getByRole("button", { name: "sidebar.user.logoutTitle" })).toBeInTheDocument()
  })
})

describe("Sidebar collapsible nav groups", () => {
  const RESOURCES_GROUP: NavigationGroup = {
    title: "Resources",
    defaultCollapsed: true,
    items: [{ name: "Knowledge Base", href: "/kb", icon: Globe }],
  }

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })))
    navState.groups = [RESOURCES_GROUP]
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    cleanup()
  })

  it("collapses a defaultCollapsed group by default and expands on click", () => {
    routeState.pathname = "/task"
    render(<Sidebar />)

    const header = screen.getByRole("button", { name: "Resources" })
    expect(header).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("link", { name: "Knowledge Base" })).not.toBeInTheDocument()

    fireEvent.click(header)
    expect(header).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("link", { name: "Knowledge Base" })).toBeInTheDocument()
  })

  it("auto-expands a defaultCollapsed group when it owns the active route", () => {
    routeState.pathname = "/kb"
    render(<Sidebar />)

    expect(screen.getByRole("button", { name: "Resources" })).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("link", { name: "Knowledge Base" })).toBeInTheDocument()
  })

  it("keeps an explicit collapse even while the group owns the active route", () => {
    routeState.pathname = "/kb"
    render(<Sidebar />)

    const header = screen.getByRole("button", { name: "Resources" })
    expect(header).toHaveAttribute("aria-expanded", "true")

    fireEvent.click(header)
    expect(header).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("link", { name: "Knowledge Base" })).not.toBeInTheDocument()
  })
})

describe("Sidebar profile subtitle", () => {
  beforeEach(() => {
    routeState.pathname = "/task"
    navState.groups = []
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })))
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    cleanup()
  })

  it("leaves the profile unlabeled without a subtitle", () => {
    render(<Sidebar />)

    expect(screen.getByText("alice@example.com")).toBeInTheDocument()
  })

  it("shows the host-supplied subtitle under the signed-in user", () => {
    render(<Sidebar profileSubtitle="Singapore" />)

    expect(screen.getByText("alice@example.com")).toBeInTheDocument()
    expect(screen.getByText("Singapore")).toBeInTheDocument()
  })

  it("treats a blank subtitle as absent", () => {
    // /agent collapses the rail, so the profile shows only its avatar.
    routeState.pathname = "/agent"
    render(<Sidebar profileSubtitle="   " />)

    const profile = screen.getByRole("button", { name: /alice@example\.com/ })
    expect(profile.getAttribute("title")).not.toContain("\u00b7")
  })

  it("announces the subtitle from the collapsed profile rail", () => {
    // The rail has no room for the name and subtitle, so both are announced.
    routeState.pathname = "/agent"
    render(<Sidebar profileSubtitle="Singapore" />)

    const profile = screen.getByRole("button", { name: /alice@example\.com.*Singapore/ })
    expect(profile.getAttribute("title")).toMatch(/alice@example\.com.*Singapore/)
  })

  it("never reads the host deployment configuration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 500 }))
    vi.stubGlobal("fetch", fetchMock)
    render(<Sidebar profileSubtitle="Singapore" />)

    await act(async () => {})

    // Deployment configuration belongs to the hosting app, not to core.
    const requested = fetchMock.mock.calls.map(([input]) => String(input))
    expect(requested).not.toContain("/api/deployment-config")
  })
})
