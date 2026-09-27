"use client"

import React, { useState } from "react"

export default function RunInput({
  placeholder,
  hint,
  onSend,
}: {
  placeholder: string
  hint: string
  onSend: (content: string) => Promise<void>
}) {
  const [value, setValue] = useState("")
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    const text = value.trim()
    if (!text || loading) return
    setLoading(true)
    try {
      await onSend(text)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-2 rounded-xl border bg-card px-4 py-3 shadow-sm focus-within:border-primary/50 transition-colors">
        <textarea
          className="flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground/60 min-h-[44px] max-h-[160px]"
          placeholder={placeholder}
          value={value}
          rows={1}
          onChange={(e) => {
            setValue(e.target.value)
            e.target.style.height = "auto"
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              void submit()
            }
          }}
        />
        <button
          disabled={!value.trim() || loading}
          onClick={submit}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-opacity disabled:opacity-40"
        >
          <svg className="h-4 w-4 rotate-90" fill="currentColor" viewBox="0 0 20 20">
            <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
          </svg>
        </button>
      </div>
      <p className="text-center text-xs text-muted-foreground">{hint}</p>
    </div>
  )
}
