import React from "react"
import { act, cleanup, render, renderHook, screen } from "@testing-library/react"
import { renderToString } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

const navigation = vi.hoisted(() => ({
  params: { id: "__shell__", name: "__shell__", token: "__shell__" },
  pathname: "/skills/__shell__",
}))

vi.mock("next/navigation", () => ({
  useParams: () => navigation.params,
  usePathname: () => navigation.pathname,
}))

import { useRouteParam } from "./use-route-param"

function Identity({ pattern, keyName }: { pattern: string; keyName: string }) {
  const value = useRouteParam(pattern, keyName)
  return <span>{value ?? "unresolved"}</span>
}

afterEach(() => {
  cleanup()
  navigation.params = { id: "__shell__", name: "__shell__", token: "__shell__" }
  navigation.pathname = "/skills/__shell__"
  window.history.replaceState(null, "", "/")
})

describe("exported dynamic route identity", () => {
  it.each([
    ["/skills/[name]", "name", "/skills/my%20skill/", "my skill"],
    ["/agent/[id]", "id", "/agent/7", "7"],
    ["/build/[id]", "id", "/build/8", "8"],
    ["/task/[id]", "id", "/task/9", "9"],
    ["/templates/[id]", "id", "/templates/local", "local"],
    ["/workforces/[id]", "id", "/workforces/12", "12"],
    ["/workforces/[id]/run", "id", "/workforces/12/run", "12"],
    ["/share/[token]", "token", "/share/abc", "abc"],
    ["/widget/chat/[token]", "token", "/widget/chat/session", "session"],
  ])("uses the matching browser identity for %s", (pattern, keyName, url, expected) => {
    window.history.replaceState(null, "", `${url}?query=ignored#fragment`)
    navigation.pathname = url
    const { result } = renderHook(() => useRouteParam(pattern, keyName))
    expect(result.current).toBe(expected)
  })

  it("preserves a real Next route param during server rendering", () => {
    navigation.params = { id: "42", name: "__shell__", token: "__shell__" }
    expect(renderToString(<Identity pattern="/agent/[id]" keyName="id" />)).toContain("42")
    navigation.params.id = "__shell__"
    expect(renderToString(<Identity pattern="/agent/[id]" keyName="id" />)).toContain("unresolved")
  })

  it("keeps a percent escape decoded once, and rejects malformed or mismatched paths", () => {
    navigation.pathname = "/skills/a%252Fb"
    window.history.replaceState(null, "", navigation.pathname)
    const view = render(<Identity pattern="/skills/[name]" keyName="name" />)
    expect(screen.getByText("a%2Fb")).toBeInTheDocument()

    act(() => {
      window.history.replaceState(null, "", "/skills/%broken")
      navigation.pathname = "/skills/%broken"
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    expect(screen.getByText("unresolved")).toBeInTheDocument()
    act(() => {
      window.history.replaceState(null, "", "/workforces/42/run")
      navigation.pathname = "/workforces/42/run"
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    expect(screen.getByText("unresolved")).toBeInTheDocument()
    view.unmount()
  })

  it("uses Next's target immediately even before the browser history commits", () => {
    navigation.pathname = "/skills/target"
    window.history.replaceState(null, "", "/skills/new")
    render(<Identity pattern="/skills/[name]" keyName="name" />)
    expect(screen.getByText("target")).toBeInTheDocument()
    expect(screen.queryByText("new")).not.toBeInTheDocument()
  })

  it("uses the browser identity when the exported Next route remains a shell", () => {
    navigation.pathname = "/skills/__shell__"
    window.history.replaceState(null, "", "/skills/real")
    render(<Identity pattern="/skills/[name]" keyName="name" />)
    expect(screen.getByText("real")).toBeInTheDocument()
  })

  it("does not revive an old browser identity when Next has left this route", () => {
    navigation.pathname = "/task/42"
    window.history.replaceState(null, "", "/skills/old")
    render(<Identity pattern="/skills/[name]" keyName="name" />)
    expect(screen.getByText("unresolved")).toBeInTheDocument()
  })

  it("holds the previous identity until a browser-ahead back navigation agrees with Next", () => {
    navigation.pathname = "/task/first"
    window.history.replaceState(null, "", "/task/first")
    const view = render(<Identity pattern="/task/[id]" keyName="id" />)
    act(() => {
      window.history.replaceState(null, "", "/task/second")
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    expect(screen.getByText("first")).toBeInTheDocument()
    navigation.pathname = "/task/second"
    view.rerender(<Identity pattern="/task/[id]" keyName="id" />)
    expect(screen.getByText("second")).toBeInTheDocument()
  })

  it("replaces stale shell params across navigation and back-forward", () => {
    navigation.pathname = "/task/first"
    window.history.replaceState(null, "", navigation.pathname)
    const view = render(<Identity pattern="/task/[id]" keyName="id" />)
    expect(screen.getByText("first")).toBeInTheDocument()
    window.history.pushState(null, "", "/task/second")
    navigation.pathname = "/task/second"
    view.rerender(<Identity pattern="/task/[id]" keyName="id" />)
    expect(screen.getByText("second")).toBeInTheDocument()
    act(() => {
      window.history.replaceState(null, "", "/task/first")
      navigation.pathname = "/task/first"
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    expect(screen.getByText("first")).toBeInTheDocument()
  })
})
