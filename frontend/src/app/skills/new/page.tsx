"use client"

import React, { useRef, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ChevronLeft, Loader2, Plus, Upload } from "lucide-react"
import { MarkdownEditor } from "@/components/skills/markdown-editor"
import { useI18n } from "@/contexts/i18n-context"
import { apiRequest, getUploadErrorMessage, parseApiResponse } from "@/lib/api-wrapper"
import { getApiUrl } from "@/lib/utils"
import { skillResponseError } from "@/types/local-skill"

const STARTER_TEMPLATE = `---
description: One-line summary of what this skill does.
when_to_use: "Use this skill when the user wants to ..."
tags:
  - example
---

# My Skill

## Overview

Describe what this skill is for.

## Execution Flow

1. First step
2. Second step
3. Final output
`

function UploadZone({ busy, uploading, upload, onError }: {
  busy: boolean
  uploading: boolean
  upload: (file: File) => void
  onError: (message: string) => void
}) {
  const { t } = useI18n()
  const fileInput = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  return <div className={`mb-6 rounded-lg border border-dashed p-4 ${dragOver ? "border-primary bg-primary/5" : "border-border"}`}
    onDragOver={event => { event.preventDefault(); setDragOver(true) }}
    onDragLeave={() => setDragOver(false)}
    onDrop={event => {
      event.preventDefault(); setDragOver(false)
      if (busy || !event.dataTransfer.files.length) return
      if (event.dataTransfer.files.length !== 1) { onError(t("skills.create.oneFile")); return }
      upload(event.dataTransfer.files[0])
    }}>
    <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}
      className="flex items-center gap-3 text-left disabled:opacity-50">
      {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Upload className="h-5 w-5" />}
      <span><span className="block text-sm font-medium">{uploading ? t("skills.create.importing") : t("skills.create.import")}</span>
        <span className="block text-xs text-muted-foreground">{t("skills.create.importHint")}</span></span>
    </button>
    <input ref={fileInput} type="file" accept=".zip,.md" aria-label={t("skills.create.import")}
      className="sr-only" onChange={event => {
        const file = event.target.files?.[0]
        if (file) upload(file)
        event.target.value = ""
      }} />
  </div>
}

export default function NewSkillPage() {
  const { t } = useI18n()
  const router = useRouter()
  const [name, setName] = useState("")
  const [content, setContent] = useState(STARTER_TEMPLATE)
  const [busy, setBusy] = useState<"create" | "upload" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const nameValid = /^[A-Za-z0-9_-]{1,64}$/.test(name)
  const base = getApiUrl()

  const create = async () => {
    if (busy || !nameValid || !content.trim()) return
    setBusy("create")
    setError(null)
    try {
      const res = await apiRequest(`${base}/api/skills/create`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, skill_md: content, scope: "personal" }),
      })
      if (!res.ok) { setError(await skillResponseError(res, t("skills.create.createFailed", { status: res.status }))); return }
      const created = await res.json() as { name: string }
      router.push(`/skills/${encodeURIComponent(created.name)}`)
    } catch {
      setError(t("skills.networkError"))
    } finally {
      setBusy(null)
    }
  }

  const upload = async (file: File) => {
    if (busy) return
    if (name && !nameValid) { setError(t("skills.create.invalidName")); return }
    setBusy("upload")
    setError(null)
    const form = new FormData()
    form.append("file", file)
    form.append("scope", "personal")
    if (name) form.append("name", name)
    try {
      // Do not set Content-Type: the browser supplies the multipart boundary.
      const res = await apiRequest(`${base}/api/skills/upload`, { method: "POST", body: form })
      const parsed = await parseApiResponse(res)
      if (!res.ok) {
        setError(getUploadErrorMessage(res, parsed, {
          generic: t("skills.create.uploadFailed", { status: res.status }),
          tooLarge: t("skills.create.uploadTooLarge"),
          proxy: t("skills.create.uploadProxyError"),
        }))
        return
      }
      const createdName = parsed.data && !Array.isArray(parsed.data) && typeof parsed.data.name === "string"
        ? parsed.data.name : null
      router.push(createdName ? `/skills/${encodeURIComponent(createdName)}` : "/skills")
    } catch {
      setError(t("skills.networkError"))
    } finally {
      setBusy(null)
    }
  }

  return <main className="h-full overflow-y-auto bg-background px-6 py-10">
    <div className="mx-auto max-w-5xl">
      <Link href="/skills" className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ChevronLeft className="h-4 w-4" />{t("skills.create.back")}</Link>
      <div className="mb-6 flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">{t("skills.create.title")}</h1>
        <button type="button" onClick={() => { void create() }} disabled={!nameValid || !content.trim() || !!busy}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50">
          {busy === "create" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          {busy === "create" ? t("skills.create.creating") : t("skills.create.create")}
        </button>
      </div>
      <label className="mb-6 block text-sm font-medium">{t("skills.create.name")}
        <input value={name} disabled={!!busy} onChange={event => setName(event.target.value)} placeholder="my-skill"
          className="mt-2 block h-10 w-full rounded-md border bg-background px-3 text-sm" />
        <span className="mt-1 block text-xs font-normal text-muted-foreground">{t("skills.create.nameHint")}</span>
        {name && !nameValid && <span className="block text-xs text-destructive">{t("skills.create.invalidName")}</span>}
      </label>
      <UploadZone busy={!!busy} uploading={busy === "upload"} upload={file => { void upload(file) }} onError={setError} />
      <MarkdownEditor value={content} onChange={setContent} rows={26} />
      {error && <p role="alert" className="mt-4 rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}
    </div>
  </main>
}
