export interface McpLookupReference {
  name?: unknown
  id?: unknown
}

const normalizeMcpLookupValue = (value: unknown): string => {
  return String(value ?? "").trim().toLowerCase()
}

const getMcpLookupKeys = (...values: unknown[]): Set<string> => {
  const keys = new Set<string>()

  values.forEach((value) => {
    const normalized = normalizeMcpLookupValue(value)
    if (!normalized) return

    keys.add(normalized)
    keys.add(normalized.replace(/\s+/g, "-"))
  })

  return keys
}

const hasSharedMcpLookupKey = (left: Set<string>, right: Set<string>): boolean => {
  for (const key of left) {
    if (right.has(key)) return true
  }
  return false
}

export const mcpNameMatches = (left: unknown, right: unknown): boolean => {
  return hasSharedMcpLookupKey(getMcpLookupKeys(left), getMcpLookupKeys(right))
}

export const findMatchingMcpServer = <T extends McpLookupReference>(
  servers: T[],
  serverName: string
): T | undefined => {
  const targetKeys = getMcpLookupKeys(serverName)
  return servers.find((server) =>
    hasSharedMcpLookupKey(getMcpLookupKeys(server.name), targetKeys)
  )
}

export const resolveMcpToolSelector = <S extends McpLookupReference>(
  server: string,
  mcpServers: S[]
): string => {
  const connectedServer = findMatchingMcpServer(mcpServers, server)
  if (typeof connectedServer?.name === "string" && connectedServer.name.trim()) {
    return connectedServer.name
  }
  if (server) {
    console.warn(
      `resolveMcpToolSelector: could not resolve "${server}" to a connected MCP server row; persisting it unchanged, which may load zero tools for this connector.`
    )
  }
  return server
}
