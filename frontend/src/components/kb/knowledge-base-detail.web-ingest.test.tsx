import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { NativeIcon, Passthrough, createJsonResponse, mockKnowledgeBaseApiWrapper, mockKnowledgeBaseToasts, partialWebIngestionApiResponse, passthroughDialog, passthroughTabs } from "./knowledge-base-test-stubs"

const apiRequestMock = vi.hoisted(() => vi.fn())
const toastErrorMock = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api-wrapper", () => mockKnowledgeBaseApiWrapper(apiRequestMock))


vi.mock("sonner", () => mockKnowledgeBaseToasts(toastErrorMock))

vi.mock("lucide-react", () => {
  const Icon = NativeIcon
  return {
    ArrowLeft: Icon,
    HardDrive: Icon,
    Search: Icon,
    Upload: Icon,
    Plus: Icon,
    Trash2: Icon,
    FileIcon: Icon,
    CheckCircle: Icon,
    XCircle: Icon,
    AlertCircle: Icon,
    Globe: Icon,
    Loader2: Icon,
  }
})


vi.mock("@/components/ui/dialog", () => ({
  ...passthroughDialog,
  Dialog: ({ children, open = true }: { children: React.ReactNode; open?: boolean }) => (open ? <div>{children}</div> : null),
}))

vi.mock("@/components/ui/tabs", () => passthroughTabs)

vi.mock("@/components/ui/scroll-area", () => ({ ScrollArea: Passthrough }))

vi.mock("@/components/ui/select", () => ({
  Select: () => <div />,
}))


vi.mock("@/components/ui/confirm-dialog", () => ({
  ConfirmDialog: () => null,
}))

vi.mock("./knowledge-base-document-list", () => ({
  KnowledgeBaseDocumentList: () => <div data-testid="kb-document-list" />,
}))

import { KnowledgeBaseDetailContent } from "./knowledge-base-detail"


describe("KnowledgeBaseDetailContent web ingest", () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    toastErrorMock.mockReset()

    apiRequestMock.mockImplementation((url: string) => {
      if (url === "http://api.local/api/kb/collections") {
        return Promise.resolve(
          createJsonResponse({
            collections: [
              {
                name: "demo",
                documents: 0,
                chunks: 0,
                embeddings: 0,
                parses: 0,
                document_names: [],
              },
            ],
          })
        )
      }
      const response = partialWebIngestionApiResponse(url, "demo")
      if (response) return Promise.resolve(response)

      throw new Error(`Unhandled apiRequest: ${url}`)
    })
  })

  afterEach(() => {
    cleanup()
  })

  it("keeps the add-source dialog open for partial web failures and surfaces the error", async () => {
    render(<KnowledgeBaseDetailContent collectionName="demo" />)

    await waitFor(() => {
      expect(screen.getByText("kb.detail.files.addSource")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("kb.detail.files.addSource"))
    fireEvent.click(screen.getByText("kb.dialog.tabs.web"))
    fireEvent.change(screen.getByLabelText("kb.dialog.webImport.basic.startUrl *"), {
      target: { value: "https://example.com/docs" },
    })
    fireEvent.click(screen.getByText("kb.index.startImport"))

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith(
        "kb.detail.errors.webImportFailed",
        expect.objectContaining({
          description: "Web import partially failed",
        })
      )
    })

    expect(await screen.findByText("kb.dialog.webImport.status.failed")).toBeInTheDocument()
    expect(await screen.findByText("Web import partially failed")).toBeInTheDocument()
    expect(screen.getByDisplayValue("https://example.com/docs")).toBeInTheDocument()

    const collectionCalls = apiRequestMock.mock.calls.filter(([url]) => url === "http://api.local/api/kb/collections")
    expect(collectionCalls).toHaveLength(1)
  })

  it("relays the backend verdict for a 409 instead of rename advice", async () => {
    // A collection can drop out of the caller's own listing while still existing
    // globally, and then this endpoint answers 409 even though the user never
    // typed a name here.
    const previous = apiRequestMock.getMockImplementation()!
    apiRequestMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === "http://api.local/api/kb/ingest-web/jobs") {
        return Promise.resolve(
          createJsonResponse(
            {
              detail:
                "Knowledge base name unavailable: demo.",
            },
            false,
            409
          )
        )
      }
      return previous(url, options)
    })

    render(<KnowledgeBaseDetailContent collectionName="demo" />)

    await waitFor(() => {
      expect(screen.getByText("kb.detail.files.addSource")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("kb.detail.files.addSource"))
    fireEvent.click(screen.getByText("kb.dialog.tabs.web"))
    fireEvent.change(screen.getByLabelText("kb.dialog.webImport.basic.startUrl *"), {
      target: { value: "https://example.com/docs" },
    })
    fireEvent.click(screen.getByText("kb.index.startImport"))

    // This page has no name field. The backend states the fact without advising
    // a rename, and that sentence is what the user sees.
    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith(
        "kb.detail.errors.webImportFailed",
        expect.objectContaining({
          description:
            "Knowledge base name unavailable: demo.",
        })
      )
    })

    expect(toastErrorMock).not.toHaveBeenCalledWith(
      "kb.errors.nameUnavailable",
      expect.anything()
    )
  })
})

describe("KnowledgeBaseDetailContent config save", () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    toastErrorMock.mockReset()

    apiRequestMock.mockImplementation((url: string) => {
      if (url === "http://api.local/api/kb/collections") {
        return Promise.resolve(
          createJsonResponse({
            collections: [
              {
                name: "demo",
                documents: 0,
                chunks: 0,
                embeddings: 0,
                parses: 0,
                document_names: [],
              },
            ],
          })
        )
      }
      if (url === "http://api.local/api/kb/collections/demo/config") {
        return Promise.resolve(
          createJsonResponse(
            { detail: "Access denied for collection: demo" },
            false,
            403
          )
        )
      }
      if (url.startsWith("http://api.local/api/models/")) {
        return Promise.resolve(createJsonResponse([]))
      }
      if (url === "http://api.local/api/jobs/capabilities") {
        return Promise.resolve(createJsonResponse({ kb_ingest_mode: "celery" }))
      }

      throw new Error(`Unhandled apiRequest: ${url}`)
    })
  })

  afterEach(() => {
    cleanup()
  })

  it("relays the denial verdict verbatim", async () => {
    render(<KnowledgeBaseDetailContent collectionName="demo" />)

    await waitFor(() => {
      expect(screen.getByText("kb.index.saveConfig")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("kb.index.saveConfig"))

    // The settings panel has no name field, so this endpoint answers a foreign
    // name with 403 rather than the ingest paths' "name taken" conflict.
    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith(
        "kb.detail.errors.saveConfigFailed",
        expect.objectContaining({
          description: "Access denied for collection: demo",
        })
      )
    })

    expect(toastErrorMock).not.toHaveBeenCalledWith(
      "kb.errors.nameUnavailable",
      expect.anything()
    )
  })
})
