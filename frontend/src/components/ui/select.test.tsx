import React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import "@/lib/test-i18n-shell"

import { Select } from "./select"

afterEach(cleanup)

describe("Select interactions", () => {
  it("runs an option's secondary action without choosing it, then allows choosing the option", () => {
    const choose = vi.fn()
    const action = vi.fn()
    render(<Select value="first" onValueChange={choose} options={[
      { value: "first", label: "First" },
      { value: "second", label: "Second", actionIcon: <span>Configure</span>, onAction: action },
    ]} />)
    fireEvent.click(screen.getByText("First"))
    fireEvent.click(screen.getByText("Configure"))
    expect(action).toHaveBeenCalledTimes(1)
    expect(choose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: /Second/ }))
    expect(choose).toHaveBeenCalledWith("second")
    expect(screen.queryByRole("button", { name: /Second/ })).not.toBeInTheDocument()
  })

  it("trims a custom entry submitted by Enter and never submits whitespace-only input", () => {
    const add = vi.fn()
    render(<Select onValueChange={vi.fn()} options={[]} allowCustom onCustomAdd={add} />)
    fireEvent.click(screen.getByText("Select..."))
    const input = screen.getByPlaceholderText("common.customPlaceholder")
    fireEvent.change(input, { target: { value: "  " } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(add).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: "  local-model  " } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(add).toHaveBeenCalledWith("local-model")
    expect(screen.queryByPlaceholderText("common.customPlaceholder")).not.toBeInTheDocument()
  })

  it("closes an open menu on outside pointer down but not when disabled", () => {
    const choose = vi.fn()
    const view = render(<><Select onValueChange={choose} options={[{ value: "local", label: "Local" }]} /><button>Outside</button></>)
    fireEvent.click(screen.getByText("Select..."))
    expect(screen.getByRole("button", { name: "Local" })).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole("button", { name: "Outside" }))
    expect(screen.queryByRole("button", { name: "Local" })).not.toBeInTheDocument()
    view.rerender(<Select disabled onValueChange={choose} options={[{ value: "local", label: "Local" }]} />)
    fireEvent.click(screen.getByText("Select..."))
    expect(screen.queryByRole("button", { name: "Local" })).not.toBeInTheDocument()
    expect(choose).not.toHaveBeenCalled()
  })
})
