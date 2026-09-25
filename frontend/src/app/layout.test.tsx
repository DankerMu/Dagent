import React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import RootLayout, { metadata } from "@/app/layout"
import { useAuth } from "@/contexts/auth-context"
import { useConnectorRuntimeDialog } from "@/contexts/connector-runtime-dialog-context"
import { apiRequest, refreshStoredAccessToken } from "@/lib/api-wrapper"
import { AUTH_CACHE_KEY } from "@/lib/auth-cache"
import { isExternalRoutePath } from "@/lib/auth-pages"
import { __resetDeploymentConfigCache } from "@/lib/deployment-config"

const route = vi.hoisted(() => ({ pathname: "/widget/chat/session" as string | null }))

vi.mock("next/navigation", () => ({
  usePathname: () => route.pathname,
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock("@/lib/api-wrapper", () => ({
  apiRequest: vi.fn(),
  refreshStoredAccessToken: vi.fn(),
}))

vi.mock("@/components/auth/auth-guard", () => ({
  AuthGuard: ({ children }: { children: React.ReactNode }) => <div data-testid="auth-guard">{children}</div>,
}))

vi.mock("@/components/layout/layout-content", () => ({
  LayoutContent: ({ children }: { children: React.ReactNode }) => <div data-testid="layout-content">{children}</div>,
}))

vi.mock("@/components/voice-input-controller", () => ({
  VoiceInputController: () => <div data-testid="voice-controller" />,
}))

vi.mock("@/components/task-error-controller", () => ({
  TaskErrorController: () => <div data-testid="task-error-controller" />,
}))

function AuthProbe() {
  const { user, token, isLoading, inTeam, teamRole } = useAuth()
  return <span data-testid="auth-probe">{`${user?.username ?? "anonymous"}:${token ?? "none"}:${isLoading}:${inTeam}:${teamRole}`}</span>
}

function seedPersonalAuthCache() {
  const now = Date.now()
  localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({
    schemaVersion: 2,
    sessionId: "personal-session",
    credentialRevision: 0,
    profileRevision: 0,
    user: { id: "owner", username: "owner" },
    token: "personal-access-token",
    refreshToken: "personal-refresh-token",
    timestamp: now,
    refreshExpiresAt: now + 60 * 60 * 1000,
  }))
}

describe("RootLayout provider boundary", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    __resetDeploymentConfigCache()
    vi.mocked(apiRequest).mockImplementation(async (url) => new Response(JSON.stringify(
      String(url).endsWith("/api/deployment-config")
        ? { deployment_origin: null, app_origin: null, region: null, team_membership_enabled: false }
        : { team_role: "member" },
    ), { status: 200 }))
    vi.stubGlobal("React", React)
    route.pathname = "/widget/chat/session"
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: { request: vi.fn(async (_name: string, callback: () => Promise<unknown>) => callback()) },
    })
  })

  afterEach(cleanup)

  it("keeps an external widget route anonymous without personal-auth side effects", async () => {
    seedPersonalAuthCache()
    const getItem = vi.spyOn(localStorage, "getItem")

    render(<RootLayout><AuthProbe /></RootLayout>)

    await waitFor(() => {
      expect(screen.getByTestId("auth-probe")).toHaveTextContent("anonymous:none:false")
    })
    expect(getItem).not.toHaveBeenCalledWith(AUTH_CACHE_KEY)
    expect(apiRequest).not.toHaveBeenCalled()
    expect(refreshStoredAccessToken).not.toHaveBeenCalled()
    expect(screen.queryByTestId("auth-guard")).not.toBeInTheDocument()
    expect(screen.queryByTestId("layout-content")).not.toBeInTheDocument()
    expect(screen.queryByTestId("voice-controller")).not.toBeInTheDocument()
    expect(screen.queryByTestId("task-error-controller")).not.toBeInTheDocument()
  })

  it("withholds route content while the pathname is unresolved", () => {
    route.pathname = null

    render(<RootLayout><AuthProbe /></RootLayout>)

    expect(screen.queryByTestId("auth-probe")).not.toBeInTheDocument()
    expect(screen.queryByTestId("auth-guard")).not.toBeInTheDocument()
    expect(screen.queryByTestId("layout-content")).not.toBeInTheDocument()
  })

  it("keeps the authenticated provider shell on general routes", async () => {
    route.pathname = "/settings"
    seedPersonalAuthCache()

    render(<RootLayout><AuthProbe /></RootLayout>)

    await waitFor(() => {
      expect(screen.getByTestId("auth-probe")).toHaveTextContent("owner:personal-access-token:false:false:null")
      expect(apiRequest).toHaveBeenCalledWith(expect.stringContaining("/api/mcp/apps"))
    })
    expect(apiRequest).not.toHaveBeenCalledWith(expect.stringContaining("/api/teams/my-team"))
    expect(screen.getByTestId("auth-guard")).toBeInTheDocument()
    expect(screen.getByTestId("layout-content")).toBeInTheDocument()
    expect(screen.getByTestId("voice-controller")).toBeInTheDocument()
    expect(screen.getByTestId("task-error-controller")).toBeInTheDocument()
  })

  it("loads membership only when the deployment advertises support", async () => {
    route.pathname = "/settings"
    seedPersonalAuthCache()
    vi.mocked(apiRequest).mockImplementation(async (url) => new Response(JSON.stringify(
      String(url).endsWith("/api/deployment-config")
        ? { deployment_origin: null, app_origin: null, region: null, team_membership_enabled: true }
        : { team_role: "admin" },
    ), { status: 200 }))
    render(<RootLayout><AuthProbe /></RootLayout>)
    await waitFor(() => expect(screen.getByTestId("auth-probe")).toHaveTextContent("owner:personal-access-token:false:true:admin"))
  })

  it("keeps authentication usable when deployment discovery fails", async () => {
    route.pathname = "/settings"
    seedPersonalAuthCache()
    vi.mocked(apiRequest).mockImplementation(async (url) => {
      if (String(url).endsWith("/api/deployment-config")) throw new Error("offline")
      return new Response("{}", { status: 200 })
    })
    render(<RootLayout><AuthProbe /></RootLayout>)
    await waitFor(() => expect(screen.getByTestId("auth-probe")).toHaveTextContent("owner:personal-access-token:false:false:null"))
    expect(apiRequest).not.toHaveBeenCalledWith(expect.stringContaining("/api/teams/my-team"))
  })

  it("does not fetch membership for a cleared session after discovery resolves", async () => {
    route.pathname = "/settings"
    seedPersonalAuthCache()
    let resolveConfig!: (response: Response) => void
    const pending = new Promise<Response>((resolve) => { resolveConfig = resolve })
    vi.mocked(apiRequest).mockImplementation(async (url) =>
      String(url).endsWith("/api/deployment-config") ? pending : new Response("{}"),
    )
    render(<RootLayout><AuthProbe /></RootLayout>)
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(expect.stringContaining("/api/deployment-config")))
    act(() => {
      localStorage.removeItem(AUTH_CACHE_KEY)
      window.dispatchEvent(new StorageEvent("storage", { key: AUTH_CACHE_KEY }))
    })
    await waitFor(() => expect(screen.getByTestId("auth-probe")).toHaveTextContent("anonymous:none:false:false:null"))
    await act(async () => {
      resolveConfig(new Response(JSON.stringify({
        deployment_origin: null, app_origin: null, region: null, team_membership_enabled: true,
      })))
      await pending
    })
    expect(apiRequest).not.toHaveBeenCalledWith(expect.stringContaining("/api/teams/my-team"))
  })
})

// Triggered by a click, not a mount effect: a mount effect fires before the
// provider's own identity-clearing mount effect settles (children's effects
// run before their parent's in the same commit), which would make even a
// live provider read back "noop" here -- an ordering quirk of triggering
// this from a child's own mount, not something a real caller ever does
// (the real trigger point is an async WebSocket handler long after mount).
function ConnectorRuntimeProbe() {
  const { openForTask, request } = useConnectorRuntimeDialog()
  return (
    <>
      <button onClick={() => openForTask(1)}>open connector runtime dialog</button>
      <span data-testid="connector-runtime-probe">{request ? "live" : "noop"}</span>
    </>
  )
}

describe("connector runtime dialog provider boundary", () => {
  afterEach(cleanup)

  it("mounts the connector runtime dialog provider only outside external routes", async () => {
    // Doing nothing here is correct, and is also exactly what a caller
    // mounted outside the provider by mistake would see, so the default
    // announces itself outside a production build -- once per action name,
    // however many times it is called.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    for (const path of ["/widget", "/widget/chat/x", "/share", "/share/x"]) {
      expect(isExternalRoutePath(path)).toBe(true)
      route.pathname = path
      render(<RootLayout><ConnectorRuntimeProbe /></RootLayout>)
      const probe = screen.getByTestId("connector-runtime-probe")
      fireEvent.click(screen.getByText("open connector runtime dialog"))
      expect(probe).toHaveTextContent("noop")
      cleanup()
    }
    expect(warn).toHaveBeenCalledTimes(1)
    expect(String(warn.mock.calls[0][0])).toContain("openForTask")
    expect(String(warn.mock.calls[0][0])).toContain("outside ConnectorRuntimeDialogProvider")
    warn.mockRestore()

    route.pathname = "/task/1"
    render(<RootLayout><ConnectorRuntimeProbe /></RootLayout>)
    await waitFor(() => expect(screen.getByTestId("auth-guard")).toBeInTheDocument())
    fireEvent.click(screen.getByText("open connector runtime dialog"))
    expect(screen.getByTestId("connector-runtime-probe")).toHaveTextContent("live")
  })
})

describe("RootLayout metadata", () => {
  it("exposes an application name and OpenGraph block for crawlers", () => {
    expect(metadata.applicationName).toBe("Xagent")
    expect(metadata.openGraph).toMatchObject({
      siteName: "Xagent",
      title: "Xagent",
      description: "AI-powered agent and workflow management system",
      type: "website",
      url: "/",
      images: [{ url: "/xagent_logo.png", alt: "Xagent Logo", width: 300, height: 300, type: "image/png" }],
    })
    expect(metadata.metadataBase).toEqual(new URL("https://cloud.xagent.co"))
  })
})
