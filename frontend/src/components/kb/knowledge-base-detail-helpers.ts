import { useEffect, useState } from "react"
import { apiRequest, getUploadErrorMessage, isJsonRecord, parseApiResponse, UPLOAD_ERROR_MESSAGES } from "@/lib/api-wrapper"
import { getApiUrl } from "@/lib/utils"
import { findMatchingIngestionTask, getKBTaskProgressDetail, getKBTaskProgressPercent, type KBProgressTask } from "@/lib/kb-progress"
import { appendIngestionConfigToFormData, normalizeIngestionConfigForFilename, type IngestionConfigForm } from "@/lib/ingestion-form"
import { getBackgroundJobFailureMessage, getBackgroundJobProgressMessage, getBackgroundJobProgressPercent, getBackgroundJobResult, isBackgroundJobResponse, waitForBackgroundJob } from "@/lib/background-jobs"
import { buildKnowledgeBaseErrorResult, type KnowledgeBaseIngestionResultLike } from "@/lib/kb-ingest-feedback"
import type { Translate } from "@/contexts/i18n-context"
export function getKnowledgeBaseToastCopy(
  t: Translate,
  genericTitle: string
) {
  return {
    genericTitle,
    nameUnavailableTitle: t("kb.errors.nameUnavailable"),
    nameUnavailableDescription: t("kb.errors.nameUnavailableHint"),
    embeddingTitle: t("kb.errors.embeddingModelUnavailable"),
    embeddingDescription: t("kb.errors.embeddingModelUnavailableHint"),
    rollbackTitle: t("kb.errors.rollbackFailed"),
    rollbackDescription: t("kb.errors.rollbackFailedHint"),
  }
}

export function initialWebIngestionConfig() {
  return {
    start_url: "",
    max_pages: 100,
    max_depth: 3,
    url_patterns: "",
    exclude_patterns: "",
    same_domain_only: true,
    content_selector: "",
    remove_selectors: "",
    concurrent_requests: 3,
    request_delay: 1.0,
    timeout: 30,
    respect_robots_txt: true,
  }
}

export interface WebIngestionResult {
  status: string
  collection: string
  total_urls_found: number
  pages_crawled: number
  pages_failed: number
  documents_created: number
  chunks_created: number
  embeddings_created: number
  crawled_urls: string[]
  failed_urls: Record<string, string>
  message: string
  warnings: string[]
  elapsed_time_ms: number
}

export function buildWebIngestionErrorResult(collection: string, message: string): WebIngestionResult {
  return {
    status: "error",
    collection,
    total_urls_found: 0,
    pages_crawled: 0,
    pages_failed: 0,
    documents_created: 0,
    chunks_created: 0,
    embeddings_created: 0,
    crawled_urls: [],
    failed_urls: {},
    message,
    warnings: [],
    elapsed_time_ms: 0,
  }
}

export async function readEmbeddingModels<T extends { model_id: string }>(onModels: (models: T[]) => void): Promise<string | null> {
  const response = await apiRequest(`${getApiUrl()}/api/models/?category=embedding`)
  if (!response.ok) throw new Error("Failed to fetch embedding models")
  const models: T[] = await response.json() || []
  onModels(models)
  const defaultResponse = await apiRequest(`${getApiUrl()}/api/models/user-default`)
  if (defaultResponse.ok) {
    const defaults = await defaultResponse.json()
    if (defaults.embedding?.model?.model_id) {
      return defaults.embedding.model.model_id
    }
  }
  return models[0]?.model_id ?? null
}

async function readIngestionProgress(collection: string, filename: string) {
  const response = await apiRequest(`${getApiUrl()}/api/progress?task_type=ingestion`)
  if (!response.ok) return null
  const data = await response.json()
  const task = findMatchingIngestionTask((data.tasks || []) as KBProgressTask[], collection, filename)
  if (!task) return null
  return { detail: getKBTaskProgressDetail(task), percent: getKBTaskProgressPercent(task) }
}

export function createIngestionForm(file: File, collection: string, config: IngestionConfigForm): FormData {
  const form = new FormData()
  form.append("file", file)
  form.append("collection", collection)
  appendIngestionConfigToFormData(form, normalizeIngestionConfigForFilename(config, file.name))
  return form
}

function reportBackgroundUploadProgress(
  job: Parameters<typeof getBackgroundJobProgressMessage>[0],
  index: number,
  total: number,
  setDetail: (detail: string) => void,
  setProgress: (percent: number) => void,
) {
  const detail = getBackgroundJobProgressMessage(job)
  const percent = getBackgroundJobProgressPercent(job)
  if (detail) setDetail(detail)
  if (typeof percent === "number") {
    const overall = ((index + percent / 100) / Math.max(total, 1)) * 100
    setProgress(Math.max(0, Math.min(100, overall)))
  }
}

export function useIngestionUploadProgress(
  isUploading: boolean,
  collection: string | null,
  filename: string | null,
  completedCount: number,
  totalCount: number,
  setDetail: (detail: string) => void,
  setProgress: (percent: number) => void,
) {
  useEffect(() => {
    if (!isUploading || !filename || !collection) return
    let cancelled = false
    const pollProgress = async () => {
      try {
        const progress = await readIngestionProgress(collection, filename)
        if (!progress || cancelled) return
        if (progress.detail) setDetail(progress.detail)
        if (typeof progress.percent === "number") {
          const overall = ((completedCount + progress.percent / 100) / Math.max(totalCount, 1)) * 100
          setProgress(Math.max(0, Math.min(100, overall)))
        }
      } catch {
        // Transient polling failures do not change the upload request's outcome.
      }
    }
    pollProgress()
    const interval = window.setInterval(pollProgress, 1000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [isUploading, collection, filename, completedCount, totalCount])
}

export async function readUploadedFileResult(
  response: Response,
  apiUrl: string,
  useBackgroundJobs: boolean,
  collection: string,
  filename: string,
  index: number,
  total: number,
  failureMessage: string,
  setDetail: (detail: string) => void,
  setProgress: (percent: number) => void,
  onFailure: (result: KnowledgeBaseIngestionResultLike) => void,
): Promise<KnowledgeBaseIngestionResultLike> {
  const parsed = await parseApiResponse(response)
  if (!response.ok) {
    const errorData = isJsonRecord(parsed.data) ? parsed.data : {}
    if (errorData.status === "error") {
      onFailure(errorData as unknown as KnowledgeBaseIngestionResultLike)
      throw new Error((typeof errorData.message === "string" && errorData.message) || failureMessage)
    }
    const errorMessage = getUploadErrorMessage(response, parsed, {
      generic: failureMessage || `Failed to upload file: ${filename}`,
      ...UPLOAD_ERROR_MESSAGES,
    })
    onFailure(buildKnowledgeBaseErrorResult(collection, errorMessage, undefined, filename))
    throw new Error(errorMessage)
  }

  const job = useBackgroundJobs && isBackgroundJobResponse(parsed.data)
    ? await waitForBackgroundJob(apiUrl, parsed.data, updatedJob => {
        reportBackgroundUploadProgress(updatedJob, index, total, setDetail, setProgress)
      })
    : null
  const result = job ? getBackgroundJobResult(job) : parsed.data
  if (job?.status === "failed" || job?.status === "cancelled") {
    const errorMessage = getBackgroundJobFailureMessage(job, failureMessage)
    onFailure(isJsonRecord(result)
      ? result as unknown as KnowledgeBaseIngestionResultLike
      : buildKnowledgeBaseErrorResult(collection, errorMessage, undefined, filename))
    throw new Error(errorMessage)
  }
  if (!isJsonRecord(result)) throw new Error(failureMessage)
  return result as unknown as KnowledgeBaseIngestionResultLike
}

export function useWebIngestionState() {
  const [isWebIngesting, setIsWebIngesting] = useState(false)
  const [webIngestionProgress, setWebIngestionProgress] = useState(0)
  const [webIngestionResult, setWebIngestionResult] = useState<WebIngestionResult | null>(null)
  const [webIngestionConfig, setWebIngestionConfig] = useState(initialWebIngestionConfig)
  return {
    isWebIngesting, setIsWebIngesting,
    webIngestionProgress, setWebIngestionProgress,
    webIngestionResult, setWebIngestionResult,
    webIngestionConfig, setWebIngestionConfig,
  }
}

export interface CollectionDocumentInfo {
  filename: string
  file_id?: string
  doc_id?: string
}

export interface CollectionDocumentSource {
  document_names?: string[]
  document_metadata?: CollectionDocumentInfo[]
}

export interface CollectionTranslator {
  (key: string, vars?: Record<string, string | number>): string
}

function normalizeOptionalIdentifier(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined
  }

  const normalizedValue = value.trim()
  return normalizedValue || undefined
}

function getDocumentIdentityKey(document: CollectionDocumentInfo): string {
  return JSON.stringify([
    document.filename,
    normalizeOptionalIdentifier(document.file_id) ?? null,
    normalizeOptionalIdentifier(document.doc_id) ?? null,
  ])
}

export function getCollectionDocuments(collectionInfo: CollectionDocumentSource | null): CollectionDocumentInfo[] {
  if (!collectionInfo) {
    return []
  }

  const representedFilenames = new Set<string>()
  const seenDocumentKeys = new Set<string>()
  const documents: CollectionDocumentInfo[] = []

  if (Array.isArray(collectionInfo.document_metadata) && collectionInfo.document_metadata.length > 0) {
    for (const document of collectionInfo.document_metadata) {
      if (typeof document.filename !== "string") {
        continue
      }
      const normalizedFilename = document.filename.trim()
      if (!normalizedFilename) {
        continue
      }

      const normalizedDocument = {
        ...document,
        filename: normalizedFilename,
        file_id: normalizeOptionalIdentifier(document.file_id),
        doc_id: normalizeOptionalIdentifier(document.doc_id),
      }
      const documentKey = getDocumentIdentityKey(normalizedDocument)
      if (seenDocumentKeys.has(documentKey)) {
        continue
      }

      seenDocumentKeys.add(documentKey)
      representedFilenames.add(normalizedFilename)
      documents.push(normalizedDocument)
    }
  }

  if (!Array.isArray(collectionInfo.document_names)) {
    return documents
  }

  for (const filename of collectionInfo.document_names) {
    if (typeof filename !== "string") {
      continue
    }
    const normalizedFilename = filename.trim()
    if (!normalizedFilename || representedFilenames.has(normalizedFilename)) {
      continue
    }

    representedFilenames.add(normalizedFilename)
    documents.push({ filename: normalizedFilename })
  }

  return documents
}

export function buildDeleteDocumentUrl(apiUrl: string, collectionName: string, document: CollectionDocumentInfo): string {
  const baseUrl = `${apiUrl}/api/kb/collections/${encodeURIComponent(collectionName)}/documents/${encodeURIComponent(document.filename)}`
  const query = new URLSearchParams()

  if (document.file_id) {
    query.set("file_id", document.file_id)
  } else if (document.doc_id) {
    query.set("doc_id", document.doc_id)
  }

  const queryString = query.toString()
  return queryString ? `${baseUrl}?${queryString}` : baseUrl
}

export function getDeleteErrorMessage(result: unknown, fallbackMessage: string): string {
  if (!result || typeof result !== "object") {
    return fallbackMessage
  }

  const response = result as { detail?: unknown; message?: unknown; errors?: unknown }
  if (typeof response.detail === "string" && response.detail) {
    return response.detail
  }
  if (typeof response.message === "string" && response.message) {
    return response.message
  }
  if (Array.isArray(response.errors)) {
    const firstError = response.errors.find((error): error is string => typeof error === "string" && error.length > 0)
    if (firstError) {
      return firstError
    }
  }

  return fallbackMessage
}
