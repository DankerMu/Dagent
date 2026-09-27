import React, { useState } from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import "@/lib/test-i18n-shell"

import { MarkdownEditor } from "./markdown-editor"

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function Editable() {
  const [value, setValue] = useState("first\nsecond")
  return <MarkdownEditor value={value} onChange={setValue} />
}

describe("SKILL.md editor", () => {
  it("inserts two spaces over a selection on Tab and places the caret after them", () => {
    let placeCaret!: FrameRequestCallback
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { placeCaret = callback; return 1 })
    render(<Editable />)
    const editor = screen.getByRole("textbox", { name: "skills.editor.skillMd" }) as HTMLTextAreaElement
    editor.setSelectionRange(6, 12)
    fireEvent.keyDown(editor, { key: "Tab" })
    placeCaret(0)
    expect(editor).toHaveValue("first\n  ")
    expect(editor.selectionStart).toBe(8)
    expect(editor.selectionEnd).toBe(8)
  })

  it("keeps ordinary typing unchanged and updates the preview from the edited content", () => {
    render(<Editable />)
    const editor = screen.getByRole("textbox", { name: "skills.editor.skillMd" })
    fireEvent.change(editor, { target: { value: "# Local skill" } })
    expect(screen.getByRole("heading", { name: "Local skill" })).toBeInTheDocument()
  })
})
