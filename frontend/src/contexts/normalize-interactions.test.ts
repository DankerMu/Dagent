import { describe, expect, it } from "vitest"
import { normalizeInteractions } from "./app-context-chat"

describe("normalizeInteractions", () => {
  it("drops retired connect_apps interactions without losing other questions", () => {
    const result = normalizeInteractions([
      { type: "connect_apps", field: "connect_apps", label: "Connect your apps", apps: ["Retired"] },
      { type: "text_input", field: "purpose", label: "Purpose" },
    ])

    expect(result).toEqual([{ type: "text_input", field: "purpose", label: "Purpose" }])
  })

  it("still filters out an unrecognized interaction type", () => {
    const result = normalizeInteractions([{ type: "not_a_real_type", field: "x", label: "x" }])

    expect(result).toEqual([])
  })

  it("keeps two same-named local clarification questions independently answerable", () => {
    const result = normalizeInteractions([
      { type: "input", field: "region", label: "Home region" },
      { type: "select_one", field: "region", label: "Destination", options: [
        { value: "eu", label: "Europe" },
        { value: 42, label: "Malformed choice" },
        { value: "us" },
      ] },
    ])
    expect(result).toEqual([
      { type: "text_input", field: "region", label: "Home region" },
      { type: "select_one", field: "region_1", label: "Destination", options: [
        { value: "eu", label: "Europe" },
        { value: "us", label: "us" },
      ] },
    ])
  })
})
