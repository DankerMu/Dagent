"use client"

import hljs from "highlight.js"
import { useEffect, useRef } from "react"
import "highlight.js/styles/github.css"

interface JSONSyntaxHighlighterProps {
  data: unknown
  className?: string
}

export function JSONSyntaxHighlighter({ data, className = "" }: JSONSyntaxHighlighterProps) {
  const codeRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (codeRef.current) hljs.highlightElement(codeRef.current)
  }, [data])

  const formatJSON = (data: unknown): string => {
    try {
      return JSON.stringify(data, null, 2)
    } catch {
      return String(data)
    }
  }


  return (
    <pre className={`bg-muted p-4 rounded-lg overflow-x-auto ${className}`}>
      <code ref={codeRef} className="json-highlight language-json text-sm">
        {formatJSON(data)}
      </code>
    </pre>
  )
}
