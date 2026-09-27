"use client"

import React from "react"
import { useI18n } from "@/contexts/i18n-context"
import type { SkillSource } from "@/types/local-skill"

const colors: Record<SkillSource, string> = {
  builtin: "border-violet-500/30 bg-violet-500/10 text-violet-600",
  user: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
  team: "border-blue-500/30 bg-blue-500/10 text-blue-600",
  external: "border-amber-500/30 bg-amber-500/10 text-amber-600",
}

export function SourceBadge({ source }: { source: SkillSource }) {
  const { t } = useI18n()
  return <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${colors[source]}`}>
    {t(`skills.scope.${source}`)}
  </span>
}
