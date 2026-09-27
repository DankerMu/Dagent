"use client"

import React, { useCallback } from "react"
import { MarkdownRenderer } from "@/components/ui/markdown-renderer"
import { useI18n } from "@/contexts/i18n-context"

export function MarkdownEditor({ value, onChange, rows = 22 }: {
  value: string
  onChange: (next: string) => void
  rows?: number
}) {
  const { t } = useI18n()
  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Tab") return
    event.preventDefault()
    const textarea = event.currentTarget
    const start = textarea.selectionStart
    onChange(`${value.slice(0, start)}  ${value.slice(textarea.selectionEnd)}`)
    requestAnimationFrame(() => { textarea.selectionStart = textarea.selectionEnd = start + 2 })
  }, [value, onChange])

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <label className="flex flex-col gap-2 text-xs font-semibold text-muted-foreground">
        {t("skills.editor.skillMd")}
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          rows={rows}
          spellCheck={false}
          className="w-full flex-1 resize-y rounded-md border bg-background p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <div className="flex flex-col gap-2 text-xs font-semibold text-muted-foreground">
        {t("skills.editor.preview")}
        <div className="flex-1 overflow-y-auto rounded-md border bg-card p-4">
          {value.trim() ? <MarkdownRenderer content={value} localImagesOnly className="prose-sm text-foreground prose-headings:text-foreground prose-code:text-foreground" />
            : <span className="font-normal italic">{t("skills.editor.empty")}</span>}
        </div>
      </div>
    </div>
  )
}
