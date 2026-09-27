"use client"

import React, { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { Loader2, Plus, Search, Trash2 } from "lucide-react"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { PageHeader } from "@/components/ui/page-header"
import { SourceBadge } from "@/components/skills/source-badge"
import { useI18n } from "@/contexts/i18n-context"
import { apiRequest } from "@/lib/api-wrapper"
import { getApiUrl } from "@/lib/utils"
import { skillResponseError, type SkillSummary } from "@/types/local-skill"

function SkillCard({ skill, onDelete }: { skill: SkillSummary; onDelete: (name: string) => void }) {
  const { t } = useI18n()
  return <article className="flex flex-col gap-2 rounded-xl border bg-card p-5">
    <div className="flex items-start justify-between gap-2">
      <Link href={`/skills/${encodeURIComponent(skill.name)}`} className="min-w-0 truncate text-sm font-semibold hover:underline">{skill.name}</Link>
      <SourceBadge source={skill.source} />
    </div>
    <p className="flex-1 text-xs text-muted-foreground">{skill.description || t("skills.noDescription")}</p>
    {skill.tags.length > 0 && <div className="flex flex-wrap gap-1">{skill.tags.map(tag =>
      <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-[10px]">{tag}</span>)}</div>}
    {!skill.effective && skill.shadowed_by && <p className="text-xs text-muted-foreground">
      {t("skills.shadowed", { source: skill.shadowed_by })}
    </p>}
    {skill.source === "user" && <button type="button" onClick={() => onDelete(skill.name)}
      className="inline-flex items-center gap-1 self-start text-xs text-destructive hover:underline">
      <Trash2 className="h-3 w-3" />{t("skills.delete")}
    </button>}
  </article>
}

export default function SkillsPage() {
  const { t } = useI18n()
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const base = getApiUrl()
  const reload = useCallback(async () => {
    try {
      const res = await apiRequest(`${base}/api/skills/installed`)
      if (!res.ok) { setError(await skillResponseError(res, t("skills.loadFailed", { status: res.status }))); return }
      setSkills(await res.json() as SkillSummary[])
      setError(null)
    } catch {
      setError(t("skills.networkError"))
    } finally {
      setLoading(false)
    }
  }, [base, t])
  useEffect(() => { void reload() }, [reload])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return skills.filter(skill => !needle || [skill.name, skill.description, ...skill.tags]
      .some(value => value.toLowerCase().includes(needle)))
  }, [skills, query])

  const remove = async () => {
    if (!deleteTarget || deleting) return
    setDeleting(true)
    setError(null)
    try {
      const res = await apiRequest(`${base}/api/skills/installed/${encodeURIComponent(deleteTarget)}`, { method: "DELETE" })
      if (!res.ok) { setError(await skillResponseError(res, t("skills.deleteFailed", { status: res.status }))); return }
      setDeleteTarget(null)
      await reload()
    } catch {
      setError(t("skills.networkError"))
    } finally {
      setDeleting(false)
    }
  }

  return <div className="flex h-full flex-col overflow-y-auto bg-background">
    <PageHeader title={t("skills.title")} description={t("skills.subtitle")}
      actions={<Link href="/skills/new" className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
        <Plus className="h-4 w-4" />{t("skills.new")}</Link>} />
    <main className="px-6 py-6 md:px-8">
      <label className="mb-6 flex max-w-lg items-center gap-2 rounded-md border px-3">
        <Search className="h-4 w-4 text-muted-foreground" />
        <input aria-label={t("skills.search")} value={query} onChange={event => setQuery(event.target.value)}
          placeholder={t("skills.search")} className="h-10 flex-1 bg-transparent text-sm outline-none" />
      </label>
      {error && <p role="alert" className="mb-4 rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}
      {loading ? <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t("skills.loading")}</p>
        : !error && filtered.length === 0 ? <p className="text-sm text-muted-foreground">{query ? t("skills.noMatch") : t("skills.empty")}</p>
        : <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{filtered.map(skill =>
          <SkillCard key={`${skill.name}:${skill.source}`} skill={skill} onDelete={setDeleteTarget} />)}</div>}
    </main>
    <ConfirmDialog isOpen={deleteTarget !== null} onOpenChange={open => { if (!open && !deleting) setDeleteTarget(null) }}
      onConfirm={() => { void remove() }} title={t("skills.deleteTitle")}
      description={t("skills.deleteDescription", { name: deleteTarget || "" })} confirmText={t("skills.delete")} isLoading={deleting} />
  </div>
}
