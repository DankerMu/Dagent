import { describe, expect, it } from "vitest"
import { skillResponseError } from "./local-skill"

describe("local skill API errors", () => {
  it("displays the server's actionable validation messages in their original order", async () => {
    const response = new Response(JSON.stringify({ detail: [{ msg: "Unsafe archive path" }, { msg: "Missing SKILL.md" }] }), { status: 422 })
    expect(await skillResponseError(response, "Upload failed")).toBe("Unsafe archive path; Missing SKILL.md")
  })

  it("uses the safe fallback rather than rendering HTML or malformed error details", async () => {
    const html = new Response("<h1>Proxy credentials expired</h1>", { status: 502 })
    expect(await skillResponseError(html, "Skill unavailable")).toBe("Skill unavailable")
    const invalid = new Response(JSON.stringify({ detail: [{ loc: ["body"] }, null, { msg: 7 }] }), { status: 422 })
    expect(await skillResponseError(invalid, "Skill unavailable")).toBe("Skill unavailable")
  })

  it("preserves a human-readable server error rather than the generic fallback", async () => {
    expect(await skillResponseError(new Response(JSON.stringify({ detail: "Not your skill" }), { status: 403 }), "Save failed")).toBe("Not your skill")
  })
})
