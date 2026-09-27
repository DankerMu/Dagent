import { describe, expect, it } from "vitest"

import { findMatchingMcpServer, mcpNameMatches, resolveMcpToolSelector } from "./mcp-lookup"

describe("custom MCP server lookup", () => {
  it("resolves a saved server selector to its connected server name", () => {
    const servers = [{ name: "Internal Knowledge" }, { name: "Local Browser" }]
    expect(findMatchingMcpServer(servers, "internal-knowledge")?.name).toBe("Internal Knowledge")
    expect(resolveMcpToolSelector("internal-knowledge", servers)).toBe("Internal Knowledge")
    expect(mcpNameMatches("internal-knowledge", "Internal Knowledge")).toBe(true)
  })

  it("preserves an unknown selector rather than guessing a different server", () => {
    expect(resolveMcpToolSelector("retired-connector", [{ name: "Internal Knowledge" }])).toBe("retired-connector")
  })
})
