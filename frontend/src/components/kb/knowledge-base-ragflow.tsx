"use client"

import React, { useCallback, useEffect, useState } from "react"
import { Button } from "../ui/button"
import { Card } from "../ui/card"
import { Input } from "../ui/input"
import { Label } from "../ui/label"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog"
import { toast } from "../ui/sonner"
import { useI18n } from "../../contexts/i18n-context"
import { apiRequest, isJsonRecord } from "../../lib/api-wrapper"
import { getApiUrl } from "../../lib/utils"

export interface RagflowStorage {
  backend: string
  dataset_id?: string
  rerank_id?: string | null
}

type Dataset = { id: string; name: string }
type RemoteResult = {
  text: string
  score: number
  metadata?: {
    dataset_id?: string
    document_id?: string
    document_keyword?: string
    chunk_id?: string
    ragflow_similarity?: number
    score_kind?: string
  } | null
}
class RemoteUiFailure extends Error {}

function isDataset(value: unknown): value is Dataset {
  return isJsonRecord(value) && typeof value.id === "string" && typeof value.name === "string"
}

function isRemoteResult(value: unknown): value is RemoteResult {
  if (!isJsonRecord(value) || typeof value.text !== "string"
    || typeof value.score !== "number" || !Number.isFinite(value.score)) return false
  if (value.metadata == null) return true
  if (!isJsonRecord(value.metadata)) return false
  const metadata = value.metadata
  for (const key of ["dataset_id", "document_id", "document_keyword", "chunk_id", "score_kind"] as const) {
    if (metadata[key] != null && typeof metadata[key] !== "string") return false
  }
  return metadata.ragflow_similarity == null || typeof metadata.ragflow_similarity === "number"
}

/** Never display arbitrary upstream response bodies: they can contain deployment details. */
function useRemoteError() {
  const { t } = useI18n()
  return useCallback((status: number | undefined, operation: "datasets" | "bind" | "search" | "rerank") => {
    if (status === 403 || status === 401) return t("kb.ragflow.errors.forbidden")
    if (status === 422) return t("kb.ragflow.errors.invalid")
    if (status === 503 || status === 404 || status === undefined) return t("kb.ragflow.errors.unavailable")
    if (operation === "datasets") return t("kb.ragflow.errors.datasets")
    if (operation === "bind") return t("kb.ragflow.errors.bind")
    if (operation === "rerank") return t("kb.ragflow.errors.rerank")
    return t("kb.ragflow.errors.search")
  }, [t])
}

function useRagflowDatasets(open: boolean) {
  const { t } = useI18n()
  const errorMessage = useRemoteError()
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [datasetId, setDatasetId] = useState("")
  const [loading, setLoading] = useState(false)
  const [discoveryError, setDiscoveryError] = useState("")
  const [reload, setReload] = useState(0)
  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    setDiscoveryError("")
    setDatasets([])
    setDatasetId("")
    void (async () => {
      try {
        const response = await apiRequest(`${getApiUrl()}/api/kb/ragflow/datasets`)
        if (!response.ok) throw new RemoteUiFailure(errorMessage(response.status, "datasets"))
        const payload: unknown = await response.json()
        if (!isJsonRecord(payload) || !Array.isArray(payload.datasets) || !payload.datasets.every(isDataset)) {
          throw new RemoteUiFailure(t("kb.ragflow.errors.datasets"))
        }
        if (active) setDatasets(payload.datasets)
      } catch (cause) {
        if (active) setDiscoveryError(cause instanceof RemoteUiFailure ? cause.message : errorMessage(undefined, "datasets"))
      } finally {
        if (active) setLoading(false)
      }
    })()
    return () => { active = false }
  }, [open, reload, t, errorMessage])
  return { datasets, datasetId, setDatasetId, loading, discoveryError, retry: () => setReload((value) => value + 1) }
}

export function RagflowConnectDialog({ open, onOpenChange, onSuccess }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const { t } = useI18n()
  const errorMessage = useRemoteError()
  const { datasets, datasetId, setDatasetId, loading, discoveryError, retry } = useRagflowDatasets(open)
  const [name, setName] = useState("")
  const [rerankId, setRerankId] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => { if (open) setError("") }, [open])

  const connect = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim() || !datasetId || submitting) return
    setSubmitting(true)
    setError("")
    try {
      const response = await apiRequest(`${getApiUrl()}/api/kb/ragflow/bindings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), dataset_id: datasetId, rerank_id: rerankId.trim() || null }),
      })
      if (!response.ok) throw new RemoteUiFailure(errorMessage(response.status, "bind"))
      toast.success(t("kb.ragflow.connected"))
      setName("")
      setRerankId("")
      onOpenChange(false)
      onSuccess()
    } catch (cause) {
      setError(cause instanceof RemoteUiFailure ? cause.message : errorMessage(undefined, "bind"))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!submitting) onOpenChange(next) }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("kb.ragflow.connect")}</DialogTitle>
          <DialogDescription>{t("kb.ragflow.connectDescription")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={connect} className="space-y-4">
          {loading ? <p role="status">{t("kb.ragflow.loadingDatasets")}</p> : discoveryError ? (
            <div role="alert" className="space-y-2 text-sm">
              <p>{discoveryError}</p>
              <Button type="button" variant="outline" onClick={retry}>{t("kb.ragflow.retry")}</Button>
            </div>
          ) : datasets.length === 0 ? <div role="status" className="space-y-2 text-sm text-muted-foreground">
            <p>{t("kb.ragflow.noDatasets")}</p>
            <Button type="button" variant="outline" onClick={retry}>{t("kb.ragflow.retry")}</Button>
          </div> : (
            <>
              <div className="space-y-2">
                <Label htmlFor="ragflow-dataset">{t("kb.ragflow.dataset")}</Label>
                <select id="ragflow-dataset" required value={datasetId} onChange={(event) => setDatasetId(event.target.value)} className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm">
                  <option value="">{t("kb.ragflow.chooseDataset")}</option>
                  {datasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name} ({dataset.id})</option>)}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="ragflow-name">{t("kb.ragflow.localName")}</Label>
                <Input id="ragflow-name" required value={name} onChange={(event) => setName(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ragflow-rerank">{t("kb.ragflow.rerankId")}</Label>
                <Input id="ragflow-rerank" value={rerankId} onChange={(event) => setRerankId(event.target.value)} placeholder={t("kb.ragflow.rerankPlaceholder")} />
                <p className="text-xs text-muted-foreground">{t("kb.ragflow.rerankHint")}</p>
              </div>
            </>
          )}
          {error && datasets.length > 0 && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={loading || submitting || !datasetId || !name.trim()}>{submitting ? t("kb.ragflow.connecting") : t("kb.ragflow.connect")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RagflowSearch({ name, datasetId, rerankId, saving, onSearchingChange }: {
  name: string
  datasetId?: string
  rerankId: string
  saving: boolean
  onSearchingChange: (searching: boolean) => void
}) {
  const { t } = useI18n()
  const errorMessage = useRemoteError()
  const [query, setQuery] = useState("")
  const [topK, setTopK] = useState(5)
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [results, setResults] = useState<RemoteResult[]>([])
  const [error, setError] = useState("")
  useEffect(() => { setResults([]); setSearched(false); setError("") }, [name, rerankId])

  const search = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!query.trim() || searching || saving || !datasetId) return
    setSearching(true)
    onSearchingChange(true)
    setError("")
    setResults([])
    setSearched(false)
    try {
      const form = new FormData()
      form.append("collection", name)
      form.append("query_text", query.trim())
      form.append("top_k", String(topK))
      const response = await apiRequest(`${getApiUrl()}/api/kb/search`, { method: "POST", body: form })
      if (!response.ok) throw new RemoteUiFailure(errorMessage(response.status, "search"))
      const payload: unknown = await response.json()
      if (!isJsonRecord(payload) || payload.status !== "success"
        || !Array.isArray(payload.results) || !payload.results.every(isRemoteResult)) {
        throw new RemoteUiFailure(errorMessage(0, "search"))
      }
      setResults(payload.results)
      setSearched(true)
    } catch (cause) {
      setError(cause instanceof RemoteUiFailure ? cause.message : errorMessage(undefined, "search"))
    } finally {
      setSearching(false)
      onSearchingChange(false)
    }
  }
  return <Card className="p-5 space-y-4">
    <h3 className="font-semibold">{t("kb.detail.search.title")}</h3>
    <form onSubmit={search} className="space-y-3">
      <Label htmlFor="remote-query">{t("kb.detail.search.queryPlaceholder")}</Label>
      <Input id="remote-query" value={query} onChange={(event) => setQuery(event.target.value)} />
      <Label htmlFor="remote-top-k">{t("kb.detail.search.topKLabel")}</Label>
      <Input id="remote-top-k" type="number" min={1} max={100} value={topK} onChange={(event) => setTopK(Number(event.target.value))} />
      <Button type="submit" disabled={!query.trim() || !Number.isInteger(topK) || topK < 1 || topK > 100 || !datasetId || searching || saving}>{searching ? t("kb.ragflow.searching") : t("kb.detail.search.searchButton")}</Button>
    </form>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {searched && results.length === 0 && <p className="text-sm text-muted-foreground">{t("kb.detail.search.noResults")}</p>}
    {results.length > 0 && <div className="space-y-3">
      <h4 className="font-medium">{t("kb.detail.search.resultsTitle", { count: results.length })}</h4>
      {results.map((result, index) => (
        <Card key={result.metadata?.chunk_id ?? index} className="p-4 space-y-2">
          <p className="whitespace-pre-wrap break-words text-sm">{result.text}</p>
          <div className="text-xs text-muted-foreground space-y-1">
            <p>{t("kb.ragflow.rankScore")}: {result.score} ({t("kb.ragflow.rankExplanation")})</p>
            {result.metadata?.ragflow_similarity != null && <p>{t("kb.ragflow.remoteSimilarity")}: {result.metadata.ragflow_similarity}</p>}
            {result.metadata?.document_keyword && <p>{t("kb.ragflow.document")}: {result.metadata.document_keyword}</p>}
            {result.metadata?.document_id && <p>{t("kb.ragflow.documentId")}: {result.metadata.document_id}</p>}
            {result.metadata?.chunk_id && <p>{t("kb.ragflow.chunkId")}: {result.metadata.chunk_id}</p>}
            {result.metadata?.dataset_id && <p>{t("kb.ragflow.datasetId")}: {result.metadata.dataset_id}</p>}
          </div>
        </Card>
      ))}
    </div>}
  </Card>
}

export function RagflowDetail({ name, storage, isAdmin, onRerankUpdated }: {
  name: string
  storage: RagflowStorage
  isAdmin: boolean
  onRerankUpdated: (rerankId: string | null) => void
}) {
  const { t } = useI18n()
  const errorMessage = useRemoteError()
  const [rerankId, setRerankId] = useState(storage.rerank_id ?? "")
  const [savedRerankId, setSavedRerankId] = useState(storage.rerank_id ?? "")
  const [saving, setSaving] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    setRerankId(storage.rerank_id ?? "")
    setSavedRerankId(storage.rerank_id ?? "")
  }, [name, storage.rerank_id])

  const updateReranker = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!isAdmin || saving || searching) return
    setSaving(true)
    setError("")
    try {
      const response = await apiRequest(`${getApiUrl()}/api/kb/ragflow/bindings/${encodeURIComponent(name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rerank_id: rerankId.trim() || null }),
      })
      if (!response.ok) throw new RemoteUiFailure(errorMessage(response.status, "rerank"))
      const binding: unknown = await response.json()
      if (!isJsonRecord(binding)
        || (binding.rerank_id !== null && typeof binding.rerank_id !== "string")) {
        throw new RemoteUiFailure(errorMessage(0, "rerank"))
      }
      const updated = binding.rerank_id ?? ""
      setRerankId(updated)
      setSavedRerankId(updated)
      onRerankUpdated(binding.rerank_id)
      toast.success(t("kb.ragflow.rerankSaved"))
    } catch (cause) {
      setError(cause instanceof RemoteUiFailure ? cause.message : errorMessage(undefined, "rerank"))
    } finally {
      setSaving(false)
    }
  }


  return (
    <div className="space-y-6 py-6">
      <Card className="p-5 space-y-3">
        <h3 className="font-semibold">{t("kb.ragflow.remoteDataset")}</h3>
        <p className="text-sm text-muted-foreground">{t("kb.ragflow.readOnly")}</p>
        <p className="text-sm">{t("kb.ragflow.datasetId")}: <span className="break-all font-mono">{storage.dataset_id || t("kb.ragflow.errors.invalid")}</span></p>
        <p className="text-sm">{t("kb.ragflow.currentRerank")}: {savedRerankId || t("kb.ragflow.disabled")}</p>
        {isAdmin && <form onSubmit={updateReranker} className="space-y-2">
          <Label htmlFor="remote-rerank">{t("kb.ragflow.rerankId")}</Label>
          <Input id="remote-rerank" value={rerankId} onChange={(event) => setRerankId(event.target.value)} placeholder={t("kb.ragflow.rerankPlaceholder")} />
          <p className="text-xs text-muted-foreground">{t("kb.ragflow.rerankHint")}</p>
          <Button type="submit" disabled={saving || searching || rerankId.trim() === savedRerankId}>{saving ? t("kb.ragflow.saving") : t("kb.ragflow.saveRerank")}</Button>
        </form>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </Card>
      <RagflowSearch name={name} datasetId={storage.dataset_id} rerankId={savedRerankId} saving={saving} onSearchingChange={setSearching} />
    </div>
  )
}
