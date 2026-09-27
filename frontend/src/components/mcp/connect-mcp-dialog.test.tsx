import React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { apiRequestMock as requests } from "./mcp-test-shell"

const success = vi.hoisted(() => vi.fn())
const changed = vi.hoisted(() => vi.fn())
const errors = vi.hoisted(() => vi.fn())
const translate = vi.hoisted(() => (key: string) => key)
vi.mock("@/contexts/i18n-context", () => ({ useI18n: () => ({ t: translate }) }))
vi.mock("@/components/ui/sonner", () => ({ toast: { success, error: errors } }))

import { ConnectMcpDialog } from "./connect-mcp-dialog"

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
}
function dialog() {
  return render(<ConnectMcpDialog open onOpenChange={changed} onSuccess={success} />)
}
function fillMcp() {
  fireEvent.change(screen.getByLabelText("tools.mcp.form.nameLabel"), { target: { value: "local_records" } })
  fireEvent.change(screen.getByLabelText("tools.mcp.dialog.command"), { target: { value: "python" } })
}
function startPendingMcpSave() {
  const finish = Promise.withResolvers<Response>()
  requests.mockReturnValue(finish.promise)
  dialog()
  fillMcp()
  fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
  return finish
}
function fillHttpApi() {
  dialog()
  fireEvent.mouseDown(screen.getByRole("tab", { name: "tools.mcp.dialog.customApi" }), { button: 0 })
  fireEvent.change(screen.getByLabelText("tools.mcp.dialog.customApiName"), { target: { value: "weather_api" } })
  fireEvent.change(screen.getByLabelText("tools.mcp.dialog.endpointUrl"), { target: { value: "https://lan.example/weather" } })
}
beforeEach(() => { requests.mockReset(); success.mockReset(); changed.mockReset(); errors.mockReset() })
afterEach(cleanup)

describe("custom connector creation", () => {
  it("saves the command-based MCP server and closes only after the API accepts it", async () => {
    const finish = startPendingMcpSave()
    expect(changed).not.toHaveBeenCalled()
    const [url, init] = requests.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("http://api.local/api/mcp/servers")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toMatchObject({ name: "local_records", transport: "stdio", config: { command: "python" } })
    finish.resolve(response({ id: 4 }))
    await waitFor(() => expect(changed).toHaveBeenCalledWith(false))
    expect(success).toHaveBeenCalledWith("local_records")
  })

  it("saves an HTTP MCP transport with its LAN endpoint rather than a stdio command", async () => {
    requests.mockResolvedValue(response({ id: 8 }))
    dialog()
    fireEvent.change(screen.getByLabelText("tools.mcp.form.nameLabel"), { target: { value: "lan_mcp" } })
    fireEvent.click(screen.getByRole("button", { name: "HTTP" }))
    fireEvent.change(screen.getByLabelText("tools.mcp.dialog.url"), { target: { value: "http://192.168.1.12:8080/mcp" } })
    fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
    await waitFor(() => expect(success).toHaveBeenCalledWith("lan_mcp"))
    const [url, init] = requests.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("http://api.local/api/mcp/servers")
    expect(JSON.parse(init.body as string)).toMatchObject({
      name: "lan_mcp", transport: "streamable_http", config: { url: "http://192.168.1.12:8080/mcp" },
    })
  })

  it("keeps the MCP draft on a server error, then resets it when reopened", async () => {
    requests.mockResolvedValue(response({ detail: "Command not allowed" }, 422))
    const view = dialog()
    fillMcp()
    fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
    await waitFor(() => expect(errors).toHaveBeenCalledWith("Command not allowed"))
    expect(screen.getByLabelText("tools.mcp.form.nameLabel")).toHaveValue("local_records")
    expect(changed).not.toHaveBeenCalled()
    view.rerender(<ConnectMcpDialog open={false} onOpenChange={changed} onSuccess={success} />)
    view.rerender(<ConnectMcpDialog open onOpenChange={changed} onSuccess={success} />)
    expect(screen.getByLabelText("tools.mcp.form.nameLabel")).toHaveValue("")
  })

  it("lets the user retry the same MCP draft after a transport failure", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {})
    requests.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(response({ id: 6 }))
    try {
      dialog()
      fillMcp()
      fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
      await waitFor(() => expect(errors).toHaveBeenCalledWith("tools.mcp.alerts.saveFailed"))
      expect(screen.getByLabelText("tools.mcp.form.nameLabel")).toHaveValue("local_records")
      fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
      await waitFor(() => expect(changed).toHaveBeenCalledWith(false))
      expect(requests).toHaveBeenCalledTimes(2)
    } finally {
      log.mockRestore()
    }
  })

  it("does not submit a malformed MCP name or leak it into the HTTP form", () => {
    dialog()
    fireEvent.change(screen.getByLabelText("tools.mcp.form.nameLabel"), { target: { value: "bad name" } })
    fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
    expect(errors).toHaveBeenCalledWith("tools.mcp.alerts.nameInvalidFormat")
    expect(requests).not.toHaveBeenCalled()
    fireEvent.mouseDown(screen.getByRole("tab", { name: "tools.mcp.dialog.customApi" }), { button: 0 })
    expect(screen.getByLabelText("tools.mcp.dialog.customApiName")).toHaveValue("")
  })

  it("posts the actual HTTP form values to custom APIs, not the MCP endpoint", async () => {
    requests.mockResolvedValue(response({ id: 7 }))
    fillHttpApi()
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "tools.mcp.buttons.save" }))
    await waitFor(() => expect(requests).toHaveBeenCalledTimes(1))
    const [url, init] = requests.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("http://api.local/api/custom-apis")
    expect(JSON.parse(init.body as string)).toMatchObject({ name: "weather_api", url: "https://lan.example/weather" })
    await waitFor(() => expect(success).toHaveBeenCalledWith("weather_api"))
  })

  it("retains the HTTP form on an API error and does not report a created connector", async () => {
    requests.mockResolvedValue(response({ detail: "Endpoint is unreachable" }, 422))
    fillHttpApi()
    fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.save" }))
    await waitFor(() => expect(errors).toHaveBeenCalledWith("Endpoint is unreachable"))
    expect(screen.getByLabelText("tools.mcp.dialog.customApiName")).toHaveValue("weather_api")
    expect(changed).not.toHaveBeenCalled()
    expect(success).not.toHaveBeenCalled()
  })

  it("prevents duplicate saves while the MCP create request is pending", async () => {
    const finish = startPendingMcpSave()
    expect(screen.getByRole("button", { name: "tools.mcp.buttons.save" })).toBeDisabled()
    expect(requests).toHaveBeenCalledTimes(1)
    finish.resolve(response({ id: 5 }))
    await waitFor(() => expect(changed).toHaveBeenCalledWith(false))
  })

  it("cancels without submitting either connector", () => {
    dialog()
    fillMcp()
    fireEvent.click(screen.getByRole("button", { name: "tools.mcp.buttons.cancel" }))
    expect(changed).toHaveBeenCalledWith(false)
    expect(requests).not.toHaveBeenCalled()
  })
})
