import React from "react"
import { cleanup, fireEvent, render as renderComponent, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type * as ApiWrapper from "../../lib/api-wrapper"
import { RagflowConnectDialog, RagflowDetail } from "./knowledge-base-ragflow"
import { I18nProvider } from "../../contexts/i18n-context"

const render = (element: React.ReactElement) => renderComponent(element, { wrapper: I18nProvider })

const request = vi.hoisted(() => vi.fn())
vi.mock("../../lib/api-wrapper", async (importOriginal) => ({
  ...(await importOriginal<typeof ApiWrapper>()), apiRequest: request,
}))
vi.mock("../ui/sonner", () => ({ toast: { success: vi.fn() } }))
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
afterEach(cleanup)
beforeEach(() => request.mockReset())

describe("RAGFlow knowledge base UI", () => {
  it("connects an explicitly selected remote dataset without a local embedding model", async () => {
    request.mockResolvedValueOnce(response({ datasets: [{ id: "remote-dataset", name: "Warehouse" }] }))
    request.mockResolvedValueOnce(response({ name: "Warehouse alias", dataset_id: "remote-dataset", rerank_id: null }))
    const connected = vi.fn()
    render(<RagflowConnectDialog open onOpenChange={vi.fn()} onSuccess={connected} />)
    await screen.findByLabelText("Local knowledge base name")
    fireEvent.change(screen.getByLabelText("RAGFlow dataset"), { target: { value: "remote-dataset" } })
    fireEvent.change(screen.getByLabelText("Local knowledge base name"), { target: { value: "Warehouse alias" } })
    fireEvent.click(screen.getByRole("button", { name: "Connect RAGFlow" }))
    await waitFor(() => expect(connected).toHaveBeenCalledOnce())
    const body = JSON.parse(request.mock.calls[1][1].body)
    expect(body).toEqual({ name: "Warehouse alias", dataset_id: "remote-dataset", rerank_id: null })
  })

  it("saves an arbitrary remote reranker, searches, then disables reranking", async () => {
    const updated = vi.fn()
    request.mockResolvedValueOnce(response({ name: "Warehouse", dataset_id: "dataset", rerank_id: "lan-provider/custom-reranker" }))
    request.mockResolvedValueOnce(response({ status: "success", results: [{
      text: "Returns within 37 days", score: 1,
      metadata: { dataset_id: "dataset", document_id: "doc", document_keyword: "policy.txt", chunk_id: "chunk", ragflow_similarity: -2.5 },
    }] }))
    request.mockResolvedValueOnce(response({ name: "Warehouse", dataset_id: "dataset", rerank_id: null }))
    render(<RagflowDetail name="Warehouse" storage={{ backend: "ragflow", dataset_id: "dataset", rerank_id: null }} isAdmin onRerankUpdated={updated} />)
    fireEvent.change(screen.getByLabelText("Remote reranker ID"), { target: { value: "lan-provider/custom-reranker" } })
    fireEvent.submit(screen.getByLabelText("Remote reranker ID").closest("form")!)
    await waitFor(() => expect(updated).toHaveBeenCalledWith("lan-provider/custom-reranker"))
    fireEvent.change(screen.getByLabelText("Enter search query..."), { target: { value: "Return policy" } })
    fireEvent.click(screen.getByRole("button", { name: "Search" }))
    await screen.findByText("Returns within 37 days")
    expect(screen.getByText(/policy.txt/)).toBeInTheDocument()
    expect(screen.getByText(/-2.5/)).toBeInTheDocument()
    const form = request.mock.calls[1][1].body as FormData
    expect(form.get("collection")).toBe("Warehouse")
    expect(form.get("query_text")).toBe("Return policy")
    expect(form.has("embedding_model_id")).toBe(false)
    fireEvent.change(screen.getByLabelText("Remote reranker ID"), { target: { value: "" } })
    fireEvent.submit(screen.getByLabelText("Remote reranker ID").closest("form")!)
    await waitFor(() => expect(updated).toHaveBeenLastCalledWith(null))
    expect(JSON.parse(request.mock.calls[2][1].body)).toEqual({ rerank_id: null })
  })

  it("allows shared read-only search but exposes neither reranker editing nor upstream errors", async () => {
    request.mockResolvedValue(response({ detail: "private-service-key" }, 503))
    render(<RagflowDetail name="Shared" storage={{ backend: "ragflow", dataset_id: "dataset" }} isAdmin={false} onRerankUpdated={vi.fn()} />)
    expect(screen.queryByLabelText("Remote reranker ID")).not.toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toBeNull()
    fireEvent.change(screen.getByLabelText("Enter search query..."), { target: { value: "Question" } })
    fireEvent.click(screen.getByRole("button", { name: "Search" }))
    await screen.findByRole("alert")
    expect(screen.queryByText(/private-service-key/)).not.toBeInTheDocument()
    expect(screen.queryByText("No results found")).not.toBeInTheDocument()
  })
})
