import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { Passthrough, NativeIcon, ConfirmDeleteButton, createJsonResponse, mockKnowledgeBaseToasts, passthroughDialog, passthroughTabs } from "./knowledge-base-test-stubs"
import { apiRequestMock } from "@/lib/test-api-request-shell"

const toastErrorMock = vi.hoisted(() => vi.fn())

vi.mock("@/components/ui/sonner", () => mockKnowledgeBaseToasts(toastErrorMock))

vi.mock("lucide-react", () => {
  const Icon = NativeIcon
  return {
    FileIcon: Icon,
    Trash2: Icon,
  }
})


vi.mock("@/components/ui/dialog", () => passthroughDialog)

vi.mock("@/components/ui/tabs", () => passthroughTabs)

vi.mock("@/components/ui/scroll-area", () => ({ ScrollArea: Passthrough }))

vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock("@/components/ui/confirm-dialog", () => ({ ConfirmDialog: ConfirmDeleteButton }))

import { KnowledgeBaseDocumentList } from "./knowledge-base-document-list"


function installApiMocks() {
  apiRequestMock.mockImplementation((url: string) => {
    if (url.startsWith("http://api.local/api/kb/collections/demo/documents/")) {
      return Promise.resolve(createJsonResponse({ status: "success", message: "deleted" }))
    }

    throw new Error(`Unhandled apiRequest: ${url}`)
  })
}

function renderDocumentList(collectionInfo: unknown, onRefresh = vi.fn().mockResolvedValue(undefined)) {
  return render(
    <KnowledgeBaseDocumentList
      collectionInfo={collectionInfo as Parameters<typeof KnowledgeBaseDocumentList>[0]["collectionInfo"]}
      collectionName="demo"
      onRefresh={onRefresh}
      t={(key: string) => key}
    />
  )
}

describe("KnowledgeBaseDetailContent delete flow", () => {
  beforeEach(() => {
    apiRequestMock.mockReset()
    toastErrorMock.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it("prefers file_id in the delete request when metadata is present", async () => {
    installApiMocks()

    renderDocumentList({
      name: "demo",
      documents: 1,
      chunks: 0,
      embeddings: 0,
      parses: 0,
      document_names: ["report.pdf"],
      document_metadata: [{ filename: "report.pdf", file_id: "file-123", doc_id: "doc-123" }],
    })

    fireEvent.click(screen.getByTitle("kb.detail.uploaded.delete"))
    fireEvent.click(screen.getByText("confirm-delete"))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/kb/collections/demo/documents/report.pdf?file_id=file-123",
        { method: "DELETE" }
      )
    })
  })

  it("falls back to filename-only delete when metadata is absent", async () => {
    installApiMocks()

    renderDocumentList({
      name: "demo",
      documents: 1,
      chunks: 0,
      embeddings: 0,
      parses: 0,
      document_names: ["legacy.txt"],
    })

    fireEvent.click(screen.getByTitle("kb.detail.uploaded.delete"))
    fireEvent.click(screen.getByText("confirm-delete"))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/kb/collections/demo/documents/legacy.txt",
        { method: "DELETE" }
      )
    })
  })

  it("refreshes the document list after a successful delete", async () => {
    installApiMocks()
    const onRefresh = vi.fn().mockResolvedValue(undefined)

    renderDocumentList({
      name: "demo",
      documents: 1,
      chunks: 0,
      embeddings: 0,
      parses: 0,
      document_names: ["report.pdf"],
      document_metadata: [{ filename: "report.pdf", file_id: "file-123" }],
    }, onRefresh)

    fireEvent.click(screen.getByTitle("kb.detail.uploaded.delete"))
    fireEvent.click(screen.getByText("confirm-delete"))

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledTimes(1)
    })
  })

  it("closes the confirm dialog before refresh failures surface", async () => {
    installApiMocks()
    const onRefresh = vi.fn().mockRejectedValue(new Error("refresh failed"))

    renderDocumentList({
      name: "demo",
      documents: 1,
      chunks: 0,
      embeddings: 0,
      parses: 0,
      document_names: ["report.pdf"],
      document_metadata: [{ filename: "report.pdf", file_id: "file-123" }],
    }, onRefresh)

    fireEvent.click(screen.getByTitle("kb.detail.uploaded.delete"))
    fireEvent.click(screen.getByText("confirm-delete"))

    await waitFor(() => {
      expect(screen.queryByText("confirm-delete")).not.toBeInTheDocument()
    })

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledTimes(1)
      expect(toastErrorMock).toHaveBeenCalledWith("refresh failed")
    })
  })

  it("keeps duplicate filenames with different identifiers addressable for delete", async () => {
    installApiMocks()

    renderDocumentList({
      name: "demo",
      documents: 2,
      chunks: 0,
      embeddings: 0,
      parses: 0,
      document_names: ["report.pdf"],
      document_metadata: [
        { filename: "report.pdf", file_id: "file-123", doc_id: "doc-123" },
        { filename: "report.pdf", file_id: "file-456", doc_id: "doc-456" },
      ],
    })

    const deleteButtons = screen.getAllByTitle("kb.detail.uploaded.delete")
    expect(deleteButtons).toHaveLength(2)

    fireEvent.click(deleteButtons[1])
    fireEvent.click(screen.getByText("confirm-delete"))

    await waitFor(() => {
      expect(apiRequestMock).toHaveBeenCalledWith(
        "http://api.local/api/kb/collections/demo/documents/report.pdf?file_id=file-456",
        { method: "DELETE" }
      )
    })
  })
})
