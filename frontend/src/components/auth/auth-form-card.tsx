"use client"

import type { ReactNode } from "react"
import Image from "next/image"
import { Card } from "@/components/ui/card"

interface AuthFormCardProps {
  appName: string
  logoPath: string
  logoAlt: string
  modeLabel: string
  title: string
  description: string
  children: ReactNode
  footer: ReactNode
}

export function AuthFormCard({
  appName,
  logoPath,
  logoAlt,
  modeLabel,
  title,
  description,
  children,
  footer,
}: AuthFormCardProps) {
  return (
    <Card className="overflow-hidden rounded-[28px] border border-[#E6EAF2] bg-white/95 py-0 text-[#111827] shadow-[0_25px_80px_rgba(47,84,235,0.14),0_10px_30px_rgba(15,23,42,0.08)] backdrop-blur-xl">
      <div className="border-b border-[#EEF2F7] px-7 pb-6 pt-7">
        <div className="mb-6 flex items-center gap-3">
          <Image src={logoPath} alt={logoAlt} width={96} height={24} className="h-6 w-auto object-contain" />
          <span className="text-sm font-semibold text-[#2F54EB]">{appName}</span>
          <span className="h-4 w-px bg-[#D9E0EC]" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#8B95A7]">
            {modeLabel} {appName}
          </span>
        </div>

        <div className="space-y-2">
          <h2 className="text-[2rem] font-semibold tracking-[-0.03em] text-[#171A2F]">
            {title}
          </h2>
          <p className="text-sm leading-6 text-[#7B8496]">{description}</p>
        </div>
      </div>

      <div className="px-7">{children}</div>

      <div className="px-7 pb-7 text-center text-sm text-[#8B95A7]">{footer}</div>
    </Card>
  )
}
