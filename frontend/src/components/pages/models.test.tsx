import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { mockApiWrapper, mockLanApiUrls } from "@/lib/test-api-wrapper"
import { identityI18n } from "@/lib/test-i18n"

const apiRequestMock = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api-wrapper", () => mockApiWrapper(apiRequestMock))
vi.mock("@/lib/utils", () => mockLanApiUrls())
vi.mock("@/contexts/auth-context", () => ({
  useAuth: () => ({ token: "local-session", user: { id: "1" } }),
}))
vi.mock("@/contexts/i18n-context", () => ({ useI18n: identityI18n }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/models",
  useSearchParams: () => new URLSearchParams(),
}))

import { ModelsPage } from "./models"

const lanModel = {
  id: 73,
  model_id: "local/assistant",
  model_provider: "openai",
  model_name: "LAN assistant",
  base_url: "http://lan-llm/v1",
  category: "llm",
  is_active: true,
  is_owner: true,
  can_edit: true,
  can_delete: true,
  is_shared: false,
}

beforeEach(() => {
  apiRequestMock.mockReset()
  apiRequestMock.mockImplementation((url: string) => {
    if (url.endsWith("/api/models/")) {
      return Promise.resolve(new Response(JSON.stringify([lanModel])))
    }
    if (url.endsWith("/api/models/user-default")) {
      return Promise.resolve(new Response(JSON.stringify([{ config_type: "general", model: lanModel }])))
    }
    if (url.endsWith("/api/models/providers/supported")) {
      return Promise.resolve(new Response(JSON.stringify([{
        id: "openai", name: "OpenAI Compatible", description: "LAN endpoint",
        category: ["llm"], requires_base_url: true,
      }])))
    }
    throw new Error(`Unhandled model request: ${url}`)
  })
})

afterEach(cleanup)

describe("existing LAN model management", () => {
  it("shows an openai-compatible LAN row, its default, and its management detail", async () => {
    render(<ModelsPage />)

    await waitFor(() => expect(screen.getByText("models.section.enabledModels")).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText("models.card.fields.default")).toBeInTheDocument())
    fireEvent.click(screen.getByRole("button", { name: "models.card.actions.manage" }))

    expect(await screen.findByText("LAN assistant")).toBeInTheDocument()
    expect(screen.getByText("lan-llm")).toBeInTheDocument()
    expect(screen.getByText("general")).toBeInTheDocument()
  })
})
