"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ChevronLeft, FileText, Loader2, Pencil, Save, Trash2, X } from "lucide-react"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { MarkdownRenderer } from "@/components/ui/markdown-renderer"
import { MarkdownEditor } from "@/components/skills/markdown-editor"
import { SourceBadge } from "@/components/skills/source-badge"
import { useI18n } from "@/contexts/i18n-context"
import { apiRequest } from "@/lib/api-wrapper"
import { getApiUrl } from "@/lib/utils"
import { useRouteParam } from "@/hooks/use-route-param"
import { skillResponseError, type SkillDetail } from "@/types/local-skill"

function SkillBody({ skill }: { skill: SkillDetail }) {
  const { t } = useI18n()
  return <>
    {skill.source !== "user" && <p className="mb-4 rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">{t("skills.detail.readOnly")}</p>}
    <section className="mb-6 rounded-xl border bg-card p-6">
      <h2 className="mb-3 text-xs font-semibold text-muted-foreground">{t("skills.editor.skillMd")}</h2>
      {skill.content ? <MarkdownRenderer content={skill.content} localImagesOnly className="prose-sm text-foreground prose-headings:text-foreground prose-code:text-foreground" />
        : <p className="text-sm italic text-muted-foreground">{t("skills.editor.empty")}</p>}
    </section>
    {skill.files.length > 0 && <section className="mb-6 rounded-xl border bg-card p-6">
      <h2 className="mb-3 text-xs font-semibold text-muted-foreground">{t("skills.detail.files")}</h2>
      <ul className="space-y-1">{skill.files.map(file => <li key={file} className="flex items-center gap-2 text-xs">
        <FileText className="h-3 w-3 text-muted-foreground" />{file}</li>)}</ul>
    </section>}
    <p className="text-xs text-muted-foreground">{t("skills.detail.path")} <code>{skill.path}</code></p>
  </>
}

function SkillHeader({ skill, editing, saving, draft, onEdit, onCancel, onSave, onDelete }: {
  skill: SkillDetail
  editing: boolean
  saving: boolean
  draft: string
  onEdit: () => void
  onCancel: () => void
  onSave: () => void
  onDelete: () => void
}) {
  const { t } = useI18n()
  return <div className="mb-6 flex flex-wrap items-start gap-3">
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-2"><h1 className="truncate text-2xl font-bold">{skill.name}</h1><SourceBadge source={skill.source} /></div>
      {skill.description && <p className="mt-1 text-sm text-muted-foreground">{skill.description}</p>}
      {skill.tags.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{skill.tags.map(tag =>
        <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-xs">{tag}</span>)}</div>}
      {!skill.effective && skill.shadowed_by && <p className="mt-2 text-xs text-muted-foreground">
        {t("skills.shadowed", { source: skill.shadowed_by })}</p>}
    </div>
    {skill.source === "user" && (editing ? <div className="flex gap-2">
      <button type="button" disabled={saving} onClick={onCancel} className="inline-flex items-center gap-1 rounded-md border px-3 py-2 text-xs">
        <X className="h-4 w-4" />{t("skills.detail.cancel")}</button>
      <button type="button" disabled={saving || draft === skill.content || !draft.trim()} onClick={onSave}
        className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-2 text-xs text-primary-foreground disabled:opacity-50">
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        {saving ? t("skills.detail.saving") : t("skills.detail.save")}</button>
    </div> : <div className="flex gap-2">
      <button type="button" onClick={onEdit} className="inline-flex items-center gap-1 rounded-md border px-3 py-2 text-xs">
        <Pencil className="h-4 w-4" />{t("skills.detail.edit")}</button>
      <button type="button" onClick={onDelete} className="inline-flex items-center gap-1 rounded-md border border-destructive/40 px-3 py-2 text-xs text-destructive">
        <Trash2 className="h-4 w-4" />{t("skills.delete")}</button>
    </div>)}
  </div>
}

export default function SkillDetailPage() {
  const name = useRouteParam("/skills/[name]", "name")
  return name ? <SkillDetailContent key={name} name={name} /> : null
}

function SkillDetailContent({ name }: { name: string }) {
  const { t } = useI18n()
  const router = useRouter()
  const base = getApiUrl()
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])
  const [skill, setSkill] = useState<SkillDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    if (!active.current) return false
    try {
      const res = await apiRequest(`${base}/api/skills/installed/${encodeURIComponent(name)}`)
      if (!res.ok) {
        const message = await skillResponseError(res, t("skills.loadFailed", { status: res.status }))
        if (active.current) setError(message)
        return false
      }
      const loaded = await res.json() as SkillDetail
      if (!active.current) return false
      setSkill(loaded)
      setError(null)
      return true
    } catch {
      if (active.current) setError(t("skills.networkError"))
      return false
    } finally {
      if (active.current) setLoading(false)
    }
  }, [base, name, t])
  useEffect(() => {
    setLoading(true)
    setSkill(null)
    setEditing(false)
    setDraft("")
    setError(null)
    void load()
  }, [load])
  const save = async () => {
    if (!skill || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      const res = await apiRequest(`${base}/api/skills/installed/${encodeURIComponent(skill.name)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skill_md: draft }),
      })
      if (!res.ok) {
        const message = await skillResponseError(res, t("skills.detail.saveFailed", { status: res.status }))
        if (active.current) setSaveError(message)
        return
      }
      if (!active.current) return
      if (await load()) { setEditing(false); setDraft("") }
      else if (active.current) setSaveError(t("skills.networkError"))
    } catch {
      if (active.current) setSaveError(t("skills.networkError"))
    } finally {
      if (active.current) setSaving(false)
    }
  }

  const remove = async () => {
    if (!skill || deleting) return
    setDeleting(true)
    setError(null)
    try {
      const res = await apiRequest(`${base}/api/skills/installed/${encodeURIComponent(skill.name)}`, { method: "DELETE" })
      if (!res.ok) {
        const message = await skillResponseError(res, t("skills.deleteFailed", { status: res.status }))
        if (active.current) setError(message)
        return
      }
      if (active.current) router.push("/skills")
    } catch {
      if (active.current) setError(t("skills.networkError"))
    } finally {
      if (active.current) {
        setDeleting(false)
        setConfirmDelete(false)
      }
    }
  }
  if (loading || (skill !== null && skill.name !== name)) return <p className="flex h-full items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" />{t("skills.loading")}</p>
  if (!skill) return <p role="alert" className="p-6 text-destructive">{error || t("skills.detail.notFound")}</p>
  return <main className="h-full overflow-y-auto bg-background px-6 py-10">
    <div className="mx-auto max-w-5xl">
      <Link href="/skills" className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ChevronLeft className="h-4 w-4" />{t("skills.detail.back")}</Link>
      <SkillHeader skill={skill} editing={editing} saving={saving} draft={draft}
        onEdit={() => { setDraft(skill.content); setSaveError(null); setEditing(true) }}
        onCancel={() => { setEditing(false); setSaveError(null) }}
        onSave={() => { void save() }} onDelete={() => setConfirmDelete(true)} />
      {editing ? <><MarkdownEditor value={draft} onChange={setDraft} rows={26} />
        {saveError && <p role="alert" className="mt-4 rounded-md border border-destructive/40 p-3 text-sm text-destructive">{saveError}</p>}</>
        : <SkillBody skill={skill} />}
      {error && <p role="alert" className="mt-4 rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}
    </div>
    <ConfirmDialog isOpen={confirmDelete} onOpenChange={open => { if (!open && !deleting) setConfirmDelete(false) }}
      onConfirm={() => { void remove() }} title={t("skills.deleteTitle")}
      description={t("skills.deleteDescription", { name: skill.name })} confirmText={t("skills.delete")} isLoading={deleting} />
  </main>
}
