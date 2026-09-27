import React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const requests = vi.hoisted(() => vi.fn())
const push = vi.hoisted(() => vi.fn())
const route = vi.hoisted(() => ({ name: "personal-one", pathname: "/skills/personal-one" }))
vi.mock("@/lib/api-wrapper", async () => ({
  ...await vi.importActual<Record<string, unknown>>("@/lib/api-wrapper"), apiRequest: requests,
}))
vi.mock("@/lib/utils", async () => ({
  ...await vi.importActual<Record<string, unknown>>("@/lib/utils"), getApiUrl: () => "http://api.local",
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useParams: () => ({ name: route.name }),
  usePathname: () => route.pathname,
}))
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) =>
  <a href={href} {...props}>{children}</a> }))
const translate = vi.hoisted(() => (key: string) => key)
vi.mock("@/contexts/i18n-context", () => ({ useI18n: () => ({ t: translate }) }))

import SkillsPage from "./page"
import NewSkillPage from "./new/page"
import SkillDetailPage from "./[name]/page-client"
import SkillDetailRoute, { generateStaticParams } from "./[name]/page"

const personal = {
  name: "personal-one", description: "A personal skill", when_to_use: "When requested", tags: ["local"],
  source: "user", scope: "personal", effective: true, shadowed_by: null,
}
const builtin = { ...personal, name: "builtin-one", source: "builtin", scope: "builtin" }
const detail = { ...personal, content: "# Personal skill", execution_flow: "", files: ["scripts/run.py"], path: "/skills/personal-one" }
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
}
function mockRequests(respond: (path: string, options?: RequestInit) => Response | Promise<Response>) {
  requests.mockImplementation((url: string, options?: RequestInit) => respond(new URL(url).pathname, options))
}
beforeEach(() => {
  requests.mockReset()
  push.mockReset()
  route.name = "personal-one"
  route.pathname = "/skills/personal-one"
  window.history.replaceState(null, "", route.pathname)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  const allowed: Record<string, true> = {
    "GET /api/skills/installed": true,
    "GET /api/skills/installed/personal-one": true,
    "GET /api/skills/installed/builtin-one": true,
    "GET /api/skills/installed/a%20b": true,
    "GET /api/skills/installed/offline-authored": true,
    "GET /api/skills/installed/team-one": true,
    "POST /api/skills/create": true,
    "POST /api/skills/upload": true,
    "PUT /api/skills/installed/personal-one": true,
    "DELETE /api/skills/installed/personal-one": true,
  }
  for (const [url, options] of requests.mock.calls) {
    expect(new URL(url).origin).toBe("http://api.local")
    expect(allowed[`${options?.method || "GET"} ${new URL(url).pathname}`]).toBe(true)
  }
})

async function loadReadOnlySkill(name: string, skill: typeof detail) {
  route.name = name
  route.pathname = `/skills/${name}`
  window.history.replaceState(null, "", route.pathname)
  mockRequests(path => {
    expect(path).toBe(`/api/skills/installed/${name}`)
    return json(skill)
  })
  render(<SkillDetailPage />)
  expect(await screen.findByText(name)).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "skills.detail.edit" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "skills.delete" })).not.toBeInTheDocument()
}

describe("local skill routes", () => {
  it("browses local scopes without marketplace calls and filters by name", async () => {
    mockRequests(path => {
      expect(path).toBe("/api/skills/installed")
      return json([personal, builtin])
    })
    render(<SkillsPage />)
    expect(await screen.findByRole("link", { name: "builtin-one" })).toHaveAttribute("href", "/skills/builtin-one")
    expect(screen.getByRole("link", { name: "personal-one" })).toBeInTheDocument()
    expect(screen.getAllByText("skills.scope.builtin")).toHaveLength(1)
    expect(screen.getAllByRole("button", { name: "skills.delete" })).toHaveLength(1)
    fireEvent.change(screen.getByRole("textbox", { name: "skills.search" }), { target: { value: "builtin" } })
    expect(screen.queryByRole("link", { name: "personal-one" })).not.toBeInTheDocument()
  })

  it("deletes a personal skill only after confirmation and handles a 204 response", async () => {
    let deleted = false
    mockRequests((path, options) => {
      if (path === "/api/skills/installed" && !options?.method) return json(deleted ? [] : [personal])
      if (path === "/api/skills/installed/personal-one" && options?.method === "DELETE") {
        deleted = true
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected request ${path}`)
    })
    render(<SkillsPage />)
    fireEvent.click(await screen.findByRole("button", { name: "skills.delete" }))
    expect(requests).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "common.cancel" }))
    expect(requests).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "skills.delete" }))
    fireEvent.click(screen.getByRole("button", { name: "skills.delete" }))
    await waitFor(() => expect(screen.getByText("skills.empty")).toBeInTheDocument())
    expect(requests).toHaveBeenCalledTimes(3)
  })

  it("creates a personal skill with SKILL.md and displays validation errors without navigating", async () => {
    let fail = true
    mockRequests((path, options) => {
      expect(path).toBe("/api/skills/create")
      expect(options?.method).toBe("POST")
      expect(JSON.parse(options?.body as string)).toEqual({
        name: "personal-one", skill_md: expect.stringContaining("# My Skill"), scope: "personal",
      })
      if (fail) return json({ detail: "Invalid frontmatter" }, 422)
      return json(personal)
    })
    render(<NewSkillPage />)
    fireEvent.change(screen.getByRole("textbox", { name: /skills.create.name/ }), { target: { value: "personal-one" } })
    fireEvent.click(screen.getByRole("button", { name: "skills.create.create" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid frontmatter")
    expect(push).not.toHaveBeenCalled()
    fail = false
    fireEvent.click(screen.getByRole("button", { name: "skills.create.create" }))
    await waitFor(() => expect(push).toHaveBeenCalledWith("/skills/personal-one"))
  })

  it("imports an archive as multipart with personal scope and no forced content type", async () => {
    mockRequests((path, options) => {
      expect(path).toBe("/api/skills/upload")
      expect(options?.method).toBe("POST")
      expect(options?.headers).toBeUndefined()
      const form = options?.body as FormData
      expect(form.get("scope")).toBe("personal")
      expect(form.get("name")).toBeNull()
      expect((form.get("file") as File).name).toBe("personal-one.zip")
      return json(personal)
    })
    render(<NewSkillPage />)
    fireEvent.change(screen.getByLabelText("skills.create.import"), {
      target: { files: [new File(["bundle"], "personal-one.zip", { type: "application/zip" })] },
    })
    await waitFor(() => expect(push).toHaveBeenCalledWith("/skills/personal-one"))
  })

  it("uploads with a typed name override and blocks create until import finishes", async () => {
    let finish!: (response: Response) => void
    mockRequests((path, options) => {
      expect(path).toBe("/api/skills/upload")
      expect((options?.body as FormData).get("name")).toBe("personal-one")
      return new Promise<Response>(resolve => { finish = resolve })
    })
    render(<NewSkillPage />)
    fireEvent.change(screen.getByRole("textbox", { name: /skills.create.name/ }), { target: { value: "personal-one" } })
    fireEvent.change(screen.getByLabelText("skills.create.import"), {
      target: { files: [new File(["bundle"], "bundle.zip")] },
    })
    expect(screen.getByRole("button", { name: "skills.create.create" })).toBeDisabled()
    expect(requests).toHaveBeenCalledTimes(1)
    finish(json(personal))
    await waitFor(() => expect(push).toHaveBeenCalledWith("/skills/personal-one"))
  })

  it("keeps a failed upload on the form and renders validation arrays as text", async () => {
    mockRequests(() => json({ detail: [{ msg: "Unsafe archive path" }] }, 422))
    render(<NewSkillPage />)
    fireEvent.change(screen.getByLabelText("skills.create.import"), {
      target: { files: [new File(["bundle"], "unsafe.zip")] },
    })
    expect(await screen.findByRole("alert")).toHaveTextContent("Unsafe archive path")
    expect(push).not.toHaveBeenCalled()
  })

  it("shows read-only skill content and bundle files without edit or delete controls", async () => {
    await loadReadOnlySkill("builtin-one", { ...detail, ...builtin })
    expect(screen.getByText("scripts/run.py")).toBeInTheDocument()
    expect(screen.getByText("skills.detail.readOnly")).toBeInTheDocument()
  })

  it("keeps team skills read-only even when the backend supports an optional write provider", async () => {
    await loadReadOnlySkill("team-one", { ...detail, name: "team-one", source: "team", scope: "team" })
    expect(screen.getByText("skills.scope.team")).toBeInTheDocument()
  })

  it("renders relative, same-origin, data and blob skill images but blocks external images", async () => {
    const origin = window.location.origin
    const content = [
      "![Local image](/assets/local.png)",
      `![Same-origin image](${origin}/assets/shared.png)`,
      "![Data image](data:image/png;base64,iVBORw0KGgo=)",
      `![Blob image](blob:${origin}/1234)`,
      "![Remote image](https://public.example/image.png)",
      "![Protocol-relative image](//public.example/image.png)",
    ].join("\n\n")
    mockRequests(() => json({ ...detail, content }))
    const { container } = render(<SkillDetailPage />)
    expect(await screen.findByAltText("Local image")).toHaveAttribute("src", "/assets/local.png")
    expect(screen.getByAltText("Same-origin image")).toHaveAttribute("src", `${origin}/assets/shared.png`)
    expect(screen.getByAltText("Data image")).toHaveAttribute("src", "data:image/png;base64,iVBORw0KGgo=")
    expect(screen.getByAltText("Blob image")).toHaveAttribute("src", `blob:${origin}/1234`)
    expect(screen.getByText("Remote image")).toBeInTheDocument()
    expect(container.querySelector('img[src="https://public.example/image.png"]')).toBeNull()
    expect(screen.getByText("Protocol-relative image")).toBeInTheDocument()
    expect(container.querySelector('img[src="//public.example/image.png"]')).toBeNull()
  })

  it("keeps local images visible in the SKILL.md editor preview", () => {
    render(<NewSkillPage />)
    fireEvent.change(screen.getByRole("textbox", { name: "skills.editor.skillMd" }), {
      target: { value: "![Preview](/assets/preview.png)\n\n![Public](https://public.example/image.png)" },
    })
    expect(screen.getByAltText("Preview")).toHaveAttribute("src", "/assets/preview.png")
    expect(screen.getByText("Public")).toBeInTheDocument()
    expect(screen.queryByAltText("Public")).not.toBeInTheDocument()
  })


  it("saves edited SKILL.md and preserves the draft on a failed save", async () => {
    let fail = true
    let content = detail.content
    mockRequests((path, options) => {
      expect(path).toBe("/api/skills/installed/personal-one")
      if (options?.method === "PUT") {
        const body = JSON.parse(options.body as string)
        expect(body).toEqual({ skill_md: "# Updated personal skill" })
        if (fail) return json({ detail: "Invalid SKILL.md" }, 422)
        content = body.skill_md
        return json(personal)
      }
      return json({ ...detail, content })
    })
    render(<SkillDetailPage />)
    fireEvent.click(await screen.findByRole("button", { name: "skills.detail.edit" }))
    fireEvent.change(screen.getByRole("textbox", { name: "skills.editor.skillMd" }), { target: { value: "# Updated personal skill" } })
    fireEvent.click(screen.getByRole("button", { name: "skills.detail.save" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid SKILL.md")
    expect(screen.getByRole("textbox", { name: "skills.editor.skillMd" })).toHaveValue("# Updated personal skill")
    fail = false
    fireEvent.click(screen.getByRole("button", { name: "skills.detail.save" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "skills.detail.edit" })).toBeInTheDocument())
    expect(screen.getByText("Updated personal skill")).toBeInTheDocument()
  })
  it("deletes from detail only after confirmation and returns to local browsing on 204", async () => {
    mockRequests((path, options) => {
      expect(path).toBe("/api/skills/installed/personal-one")
      return options?.method === "DELETE" ? new Response(null, { status: 204 }) : json(detail)
    })
    render(<SkillDetailPage />)
    fireEvent.click(await screen.findByRole("button", { name: "skills.delete" }))
    expect(requests).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "skills.delete" }))
    await waitFor(() => expect(push).toHaveBeenCalledWith("/skills"))
  })

  it("reports delete errors without navigating away or hiding the skill", async () => {
    mockRequests((path, options) => {
      expect(path).toBe("/api/skills/installed/personal-one")
      return options?.method === "DELETE"
        ? json({ detail: "You cannot delete this skill" }, 403) : json(detail)
    })
    render(<SkillDetailPage />)
    fireEvent.click(await screen.findByRole("button", { name: "skills.delete" }))
    fireEvent.click(screen.getByRole("button", { name: "skills.delete" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("You cannot delete this skill")
    expect(screen.getByText("personal-one")).toBeInTheDocument()
    expect(push).not.toHaveBeenCalled()
  })

  it("does not fall back to a remote registry when a local skill is missing", async () => {
    mockRequests(path => {
      expect(path).toBe("/api/skills/installed/personal-one")
      return json({ detail: "Not found" }, 404)
    })
    render(<SkillDetailPage />)
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found")
    expect(requests).toHaveBeenCalledTimes(1)
  })
  it("exports one offline shell while loading the browser-selected local skill", async () => {
    route.name = "__shell__"
    mockRequests(path => {
      expect(path).toBe("/api/skills/installed/personal-one")
      return json(detail)
    })
    vi.stubGlobal("React", React)
    expect(generateStaticParams()).toEqual([{ name: "__shell__" }])
    render(<SkillDetailRoute />)
    expect(await screen.findByText("personal-one")).toBeInTheDocument()
    expect(requests).toHaveBeenCalledTimes(1)
  })

  it("requests the Next target, never the old new-skill URL, before browser history commits", async () => {
    route.name = "__shell__"
    route.pathname = "/skills/offline-authored"
    window.history.replaceState(null, "", "/skills/new")
    mockRequests(path => {
      expect(path).toBe("/api/skills/installed/offline-authored")
      return json({ ...detail, name: "offline-authored" })
    })
    render(<SkillDetailPage />)
    expect(requests).toHaveBeenCalledTimes(1)
    expect(new URL(requests.mock.calls[0][0]).pathname).toBe("/api/skills/installed/offline-authored")
    expect(await screen.findByText("offline-authored")).toBeInTheDocument()
    expect(requests).toHaveBeenCalledTimes(1)
  })

  it("does not request an unresolved or mismatched route", async () => {
    route.name = "__shell__"
    route.pathname = "/skills/__shell__"
    window.history.replaceState(null, "", "/skills/__shell__")
    const view = render(<SkillDetailPage />)
    expect(requests).not.toHaveBeenCalled()
    act(() => {
      window.history.replaceState(null, "", "/task/personal-one")
      route.pathname = "/task/personal-one"
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    view.rerender(<SkillDetailPage />)
    expect(requests).not.toHaveBeenCalled()
    expect(screen.queryByText("skills.detail.notFound")).not.toBeInTheDocument()
  })

  it("decodes a browser skill name once before encoding its API request", async () => {
    route.name = "__shell__"
    route.pathname = "/skills/a%20b"
    window.history.replaceState(null, "", route.pathname)
    mockRequests(path => {
      expect(path).toBe("/api/skills/installed/a%20b")
      return json({ ...detail, name: "a b" })
    })
    render(<SkillDetailPage />)
    expect(await screen.findByText("a b")).toBeInTheDocument()
  })

  it("keeps navigation and back-forward owned by the current browser skill", async () => {
    route.name = "__shell__"
    const oldRequest = Promise.withResolvers<Response>()
    let personalLoads = 0
    mockRequests(path => {
      if (path === "/api/skills/installed/personal-one") {
        return personalLoads++ === 0 ? oldRequest.promise : json(detail)
      }
      if (path === "/api/skills/installed/builtin-one") return json({ ...detail, ...builtin })
      throw new Error(`Unexpected request ${path}`)
    })
    const view = render(<SkillDetailPage />)
    await waitFor(() => expect(requests).toHaveBeenCalledTimes(1))
    act(() => {
      window.history.pushState(null, "", "/skills/builtin-one")
      route.pathname = "/skills/builtin-one"
    })
    view.rerender(<SkillDetailPage />)
    expect(await screen.findByText("builtin-one")).toBeInTheDocument()
    await act(async () => { oldRequest.resolve(json(detail)) })
    expect(screen.queryByText("personal-one")).not.toBeInTheDocument()
    act(() => {
      // Popstate with Next's route notification identifies the same owner.
      window.history.replaceState(null, "", "/skills/personal-one")
      route.pathname = "/skills/personal-one"
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    expect(await screen.findByText("personal-one")).toBeInTheDocument()
    expect(screen.queryByText("builtin-one")).not.toBeInTheDocument()
    expect(requests).toHaveBeenCalledTimes(3)
  })
})
