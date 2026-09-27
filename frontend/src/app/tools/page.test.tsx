import React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { apiRequestMock as requests } from "@/components/mcp/mcp-test-shell"

const errors = vi.hoisted(() => vi.fn())
const successes = vi.hoisted(() => vi.fn())
const auth = vi.hoisted(() => ({ user: { is_admin: true } }))
const translate = vi.hoisted(() => (key: string, vars?: Record<string, unknown>) => vars ? `${key}:${JSON.stringify(vars)}` : key)
const params = vi.hoisted(() => ({ current: new URLSearchParams() }))
const replace = vi.hoisted(() => vi.fn())
vi.mock("@/contexts/i18n-context", () => ({ useI18n: () => ({ t: translate, tDynamic: (_key: string, fallback: string) => fallback }) }))
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => auth }))
vi.mock("@/components/ui/sonner", () => ({ toast: { error: errors, success: successes } }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }), useSearchParams: () => params.current }))

import ToolsPage from "./page"

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } })
}
const databaseTool = { name: "database", description: "Local SQL", type: "basic", category: "database", enabled: true, requires_configuration: true }
const browserTool = { name: "browser", description: "LAN browser", type: "basic", category: "browser", enabled: true }
const server = { id: 73, user_id: 7, name: "records_mcp", description: "Internal records", transport: "stdio", config: { command: "python" }, is_active: true, is_default: false, transport_display: "STDIO", created_at: "2026-07-01", updated_at: "2026-07-02" }
let connections: { name: string; source: string; masked: string }[]
function installRequests() {
  requests.mockImplementation((url: string, init?: RequestInit) => {
    const path = new URL(url).pathname
    if (path === "/api/tools/available") return Promise.resolve(json({ tools: [databaseTool, browserTool] }))
    if (path === "/api/mcp/servers") return Promise.resolve(json([server]))
    if (path === "/api/tools/sql-connections" && !init) return Promise.resolve(json({ connections }))
    if (path === "/api/mcp/servers/73" && !init) return Promise.resolve(json({ ...server, user_env: {}, env_source: "own", runtime_input_schema: null, runtime_bindings: [], allow_delegated_authorization: false, can_edit_global: true }))
    throw new Error(`Unexpected request ${init?.method || "GET"} ${path}`)
  })
}
async function page() {
  render(<ToolsPage />)
  expect(await screen.findByText("records_mcp")).toBeInTheDocument()
  expect(screen.getByText("database")).toBeInTheDocument()
}
beforeEach(() => {
  vi.stubGlobal("React", React)
  requests.mockReset(); errors.mockReset(); successes.mockReset(); replace.mockReset()
  params.current = new URLSearchParams()
  auth.user.is_admin = true
  connections = [{ name: "local", source: "db", masked: "postgresql://***@127.0.0.1/main" }]
  installRequests()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("local tools", () => {
  it("filters built-in tools without dropping custom MCP results from the connectors tab", async () => {
    await page()
    fireEvent.click(screen.getByRole("tab", { name: "tools.tabs.connectors" }))
    expect(screen.queryByText("database")).not.toBeInTheDocument()
    expect(screen.getByText("records_mcp")).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText("tools.list.searchPlaceholder"), { target: { value: "missing" } })
    expect(screen.queryByText("records_mcp")).not.toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText("tools.list.searchPlaceholder"), { target: { value: "records" } })
    expect(screen.getByText("records_mcp")).toBeInTheDocument()
  })

  it("keeps policy toggles admin-only without hiding personal SQL connections", async () => {
    auth.user.is_admin = false
    await page()
    expect(screen.queryByRole("button", { name: "tools.policy.disableAction" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "tools.database.manageConnections" }))
    expect(await within(screen.getByRole("dialog")).findByText("postgresql://***@127.0.0.1/main")).toBeInTheDocument()
  })

  it("updates a built-in tool policy through the retained enable endpoint", async () => {
    const original = requests.getMockImplementation()!
    requests.mockImplementation((url: string, init?: RequestInit) => {
      if (new URL(url).pathname === "/api/tools/browser/enabled" && init?.method === "PUT") return Promise.resolve(json({ enabled: false }))
      return original(url, init)
    })
    await page()
    fireEvent.click(screen.getAllByRole("button", { name: "tools.policy.disableAction" })[1])
    await waitFor(() => expect(successes).toHaveBeenCalledWith("tools.policy.toggleSuccessDisabled"))
    const [url, init] = requests.mock.calls.find(([, options]) => options?.method === "PUT") as [string, RequestInit]
    expect(url).toBe("http://api.local/api/tools/browser/enabled")
    expect(JSON.parse(init.body as string)).toEqual({ enabled: false })
  })

  it("does not open an edit form when the authoritative MCP detail fails", async () => {
    requests.mockImplementation((url: string, init?: RequestInit) => {
      if (new URL(url).pathname === "/api/mcp/servers/73") return Promise.resolve(json({ detail: "Not found" }, 404))
      if (new URL(url).pathname === "/api/tools/available") return Promise.resolve(json({ tools: [databaseTool] }))
      if (new URL(url).pathname === "/api/mcp/servers") return Promise.resolve(json([server]))
      if (new URL(url).pathname === "/api/tools/sql-connections") return Promise.resolve(json({ connections }))
      throw new Error(`Unexpected request ${init?.method || "GET"} ${url}`)
    })
    await page()
    fireEvent.click(screen.getByText("records_mcp"))
    await waitFor(() => expect(errors).toHaveBeenCalledWith("tools.mcp.dialog.mcpDetailFetchError"))
    expect(screen.queryByText("tools.mcp.dialog.editTitle")).not.toBeInTheDocument()
  })

  it("edits the fetched MCP detail and retains the form on a failed save", async () => {
    const original = requests.getMockImplementation()!
    requests.mockImplementation((url: string, init?: RequestInit) => {
      if (new URL(url).pathname === "/api/mcp/servers/73" && init?.method === "PUT") return Promise.resolve(json({ detail: "Write denied" }, 403))
      return original(url, init)
    })
    await page()
    fireEvent.click(screen.getByText("records_mcp"))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByLabelText("tools.mcp.form.nameLabel")).toHaveValue("records_mcp")
    fireEvent.change(within(dialog).getByLabelText("tools.mcp.form.descriptionLabel"), { target: { value: "Updated description" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "tools.mcp.buttons.save" }))
    await waitFor(() => expect(errors).toHaveBeenCalledWith("Write denied"))
    const [, init] = requests.mock.calls.find(([url, options]) => new URL(url).pathname === "/api/mcp/servers/73" && options?.method === "PUT") as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toMatchObject({ description: "Updated description" })
    expect(within(screen.getByRole("dialog")).getByLabelText("tools.mcp.form.descriptionLabel")).toHaveValue("Updated description")
  })

  it("preserves SQL connection management while built-in credential endpoints are retired", async () => {
    const original = requests.getMockImplementation()!
    requests.mockImplementation((url: string, init?: RequestInit) => {
      if (new URL(url).pathname === "/api/tools/sql-connections/new%20local" && init?.method === "PUT") {
        connections = [...connections, { name: "new local", source: "db", masked: "sqlite:/***" }]
        return Promise.resolve(json({ name: "new local" }))
      }
      return original(url, init)
    })
    await page()
    fireEvent.click(screen.getByRole("button", { name: "tools.database.manageConnections" }))
    const dialog = screen.getByRole("dialog")
    expect(await within(dialog).findByText("postgresql://***@127.0.0.1/main")).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText("tools.database.connectionName"), { target: { value: "new local" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "tools.database.save" }))
    expect(errors).toHaveBeenCalledWith("tools.database.validation.required")
    fireEvent.change(within(dialog).getByLabelText("tools.database.host"), { target: { value: "127.0.0.1" } })
    fireEvent.change(within(dialog).getByLabelText("tools.database.databaseName"), { target: { value: "main" } })
    fireEvent.change(within(dialog).getByLabelText("tools.database.username"), { target: { value: "reader" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "tools.database.save" }))
    await waitFor(() => expect(within(dialog).getByText("new local")).toBeInTheDocument())
    const [url, init] = requests.mock.calls.find(([, options]) => options?.method === "PUT") as [string, RequestInit]
    expect(url).toBe("http://api.local/api/tools/sql-connections/new%20local")
    expect(JSON.parse(init.body as string)).toEqual({ connection_url: "postgresql://reader@127.0.0.1:5432/main" })
    expect(requests.mock.calls.every(([requestUrl]) => !/\/configurable|\/credentials(?:\/|$)/.test(requestUrl))).toBe(true)
  })
})
