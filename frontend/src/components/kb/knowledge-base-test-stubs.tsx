import React from "react"
import { vi, type Mock } from "vitest"

// Invariant presentation boundaries across the KB component suites.
vi.mock("@/lib/utils", () => ({
  getApiUrl: () => "http://api.local",
  cn: (...classes: Array<string | false | null | undefined>) => classes.filter(Boolean).join(" "),
}))
vi.mock("@/contexts/i18n-context", () => ({
  useI18n: () => ({
    locale: "en",
    t: (key: string, vars?: Record<string, string | number>) => {
      if (vars?.name) return `${key}:${vars.name}`
      if (vars?.owners) return `${key}:${vars.owners}`
      return key
    },
  }),
}))
vi.mock("@radix-ui/react-tabs", () => ({ Trigger: NativeButton }))
vi.mock("@/components/ui/button", () => ({ Button: NativeButton }))
vi.mock("@/components/ui/input", () => ({ Input: NativeInput }))
vi.mock("@/components/ui/label", () => ({ Label: NativeLabel }))
vi.mock("@/components/ui/badge", () => ({ Badge: NativeBadge }))
vi.mock("@/components/ui/card", () => ({ Card: NativeCard }))

export function Passthrough({ children }: { children: React.ReactNode }) {
  return <div>{children}</div>
}

export function NativeButton({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props}>{children}</button>
}

function NativeInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} />
}

function NativeLabel({ children, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label {...props}>{children}</label>
}

export const passthroughDialog = {
  Dialog: Passthrough,
  DialogContent: Passthrough,
  DialogDescription: Passthrough,
  DialogHeader: Passthrough,
  DialogTitle: Passthrough,
}

export const passthroughTabs = {
  Tabs: Passthrough,
  TabsContent: Passthrough,
  TabsList: Passthrough,
}

export function NativeIcon(props: React.SVGProps<SVGSVGElement>) {
  return <svg {...props} />
}

export function NativeBadge({ children }: { children: React.ReactNode }) {
  return <span>{children}</span>
}

export function NativeCard({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...props}>{children}</div>
}

export function ConfirmDeleteButton({ isOpen, onConfirm }: { isOpen: boolean; onConfirm: () => void }) {
  return isOpen ? <button onClick={onConfirm}>confirm-delete</button> : null
}

export function createJsonResponse(body: unknown, ok = true, status = ok ? 200 : 500) {
  return { ok, status, json: vi.fn().mockResolvedValue(body) }
}

// All KB suites share the same system-boundary toast shape; individual tests
// supply the callbacks they need to inspect.
export function mockKnowledgeBaseToasts(error: Mock, success: Mock = vi.fn(), warning: Mock = vi.fn()) {
  return { toast: { error, success, warning } }
}

export function createSucceededJob(result: Record<string, unknown>, jobType: "kb.ingest.document" | "kb.ingest.web" = "kb.ingest.document") {
  return {
    id: "job-1",
    user_id: 1,
    job_type: jobType,
    queue: "kb",
    status: "succeeded",
    progress: { message: "Completed", completed: 1, total: 1 },
    result,
    error_message: null,
    celery_task_id: "task-1",
    attempts: 1,
    max_attempts: 3,
  }
}
function partialWebIngestionResult(collection: string) {
  return {
    status: "partial",
    collection,
    total_urls_found: 1,
    pages_crawled: 1,
    pages_failed: 1,
    documents_created: 0,
    chunks_created: 0,
    embeddings_created: 0,
    crawled_urls: [],
    failed_urls: { "https://example.com/docs": "embedding missing" },
    message: "Web import partially failed",
    warnings: [],
    elapsed_time_ms: 0,
  }
}

// Common backend responses for a partially successful web import. Other
// endpoints remain explicit in each suite so unexpected requests still fail.
export function partialWebIngestionApiResponse(url: string, collection: string) {
  if (url === "http://api.local/api/models/?category=embedding") return createJsonResponse([])
  if (url === "http://api.local/api/models/user-default") return createJsonResponse({})
  if (url === "http://api.local/api/jobs/capabilities") {
    return createJsonResponse({ kb_ingest_mode: "celery" })
  }
  if (url === "http://api.local/api/kb/ingest-web/jobs") {
    return createJsonResponse(createSucceededJob(partialWebIngestionResult(collection), "kb.ingest.web"))
  }
  return undefined
}

export function mockKnowledgeBaseApiWrapper(apiRequest: Mock) {
  return {
    apiRequest,
    parseApiResponse: async (response: { json: () => Promise<unknown> }) => ({
      data: await response.json(),
      text: null,
      isHtml: false,
    }),
    getUploadErrorMessage: (
      _response: unknown,
      parsed: { data?: { detail?: string; message?: string } | null },
      messages: { generic: string },
    ) => parsed?.data?.detail || parsed?.data?.message || messages.generic,
    isJsonRecord: (value: unknown) => typeof value === "object" && value !== null && !Array.isArray(value),
    UPLOAD_ERROR_MESSAGES: {},
  }
}
