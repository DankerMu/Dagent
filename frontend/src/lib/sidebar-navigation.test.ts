import { describe, expect, it } from "vitest"

import { getNavigationGroupsForUser, getUserMenuItemsForUser } from "./sidebar-navigation"

describe("sidebar navigation", () => {
  it("exposes Conversation Logs under the More resource menu", () => {
    const groups = getNavigationGroupsForUser({ is_admin: false })
    const resources = groups.find((group) => group.titleKey === "nav.sections.resources")
    const more = resources?.items.find((item) => item.href === "__resources_more__")

    expect(more?.children).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "Conversation Logs",
          nameKey: "nav.conversationLogs",
          href: "/conversation-logs",
        }),
      ])
    )

  })

  it("collapses Resources by default and gives each built-in group a stable id", () => {
    const groups = getNavigationGroupsForUser({ is_admin: false })
    const agentDevelopment = groups.find((group) => group.titleKey === "nav.sections.agentDevelopment")
    const resources = groups.find((group) => group.titleKey === "nav.sections.resources")

    expect(agentDevelopment?.id).toBe("agent-development")
    expect(agentDevelopment?.defaultCollapsed).toBeFalsy()
    expect(resources?.id).toBe("resources")
    expect(resources?.defaultCollapsed).toBe(true)
  })

  it("renames the build/templates nav entries to My Team/Add teammates", () => {
    const groups = getNavigationGroupsForUser({ is_admin: false })
    const agentDevelopment = groups.find((group) => group.titleKey === "nav.sections.agentDevelopment")

    expect(agentDevelopment?.items.find((item) => item.href === "/build")).toEqual(
      expect.objectContaining({ name: "My Team", nameKey: "nav.myTeam" })
    )
    expect(agentDevelopment?.items.find((item) => item.href === "/templates")).toEqual(
      expect.objectContaining({ name: "Add teammates", nameKey: "nav.addTeammates" })
    )
  })

  it("keeps user management as the admin-only resource route", () => {
    const groups = getNavigationGroupsForUser({ is_admin: true })
    const resources = groups.find((group) => group.titleKey === "nav.sections.resources")
    const more = resources?.items.find((item) => item.href === "__resources_more__")

    expect(more?.children?.find((item) => item.name === "User Management")?.href).toBe("/users")
    expect(more?.children?.some((item) => item.href === "/admin-mcp" || item.href === "/channels")).toBe(false)
  })
  it("does not expose user management to anonymous or non-admin accounts", () => {
    for (const viewer of [null, { is_admin: false }]) {
      const resources = getNavigationGroupsForUser(viewer).find(group => group.titleKey === "nav.sections.resources")
      const more = resources?.items.find(item => item.href === "__resources_more__")
      expect(more?.children?.some(item => item.href === "/users")).toBe(false)
      expect(more?.children?.some(item => item.href === "/skills")).toBe(true)
    }
  })
  it("keeps account-menu navigation isolated across admin and anonymous viewers", () => {
    const adminMenu = getUserMenuItemsForUser({ is_admin: true })
    expect(adminMenu.map(item => item.href)).toEqual(["/settings"])
    adminMenu.splice(0, adminMenu.length)
    expect(getUserMenuItemsForUser(null).map(item => item.href)).toEqual(["/settings"])
  })
})
