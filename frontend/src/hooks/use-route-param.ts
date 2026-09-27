"use client"

import { useSyncExternalStore } from "react"
import { useParams, usePathname } from "next/navigation"

const SHELL_PARAM = "__shell__"

function subscribeToBrowserPath(onChange: () => void) {
  window.addEventListener("popstate", onChange)
  return () => window.removeEventListener("popstate", onChange)
}

function browserPath() {
  return window.location.pathname
}

function serverPath() {
  return null
}

function decodeRouteSegment(segment: string): string | undefined {
  try {
    const value = decodeURIComponent(segment)
    return value && value !== SHELL_PARAM ? value : undefined
  } catch {
    return undefined
  }
}

function matchRoute(pathname: string, pattern: string, key: string): string | undefined | null {
  if (!pathname) return null
  const expected = pattern.split("/").slice(1)
  const actual = pathname.replace(/\/$/, "").split("/").slice(1)
  if (actual.length !== expected.length) return null
  let value: string | undefined
  for (let index = 0; index < expected.length; index++) {
    if (expected[index] === `[${key}]`) {
      value = decodeRouteSegment(actual[index])
    } else if (expected[index] !== actual[index]) {
      return null
    }
  }
  return value
}

/** Resolve Next's real route first; exported shell params use the matching browser path. */
export function useRouteParam(pattern: string, key: string): string | undefined {
  const params = useParams()
  const nextPath = usePathname()
  const browser = useSyncExternalStore(subscribeToBrowserPath, browserPath, serverPath)
  if (browser === null) {
    // Preserve server-rendered Next routes and the first hydration render.
    // Next params are already decoded; never decode them again.
    const param = params?.[key]
    return typeof param === "string" && param !== SHELL_PARAM && param !== "" ? param : undefined
  }
  const nextValue = nextPath === null ? undefined : matchRoute(nextPath, pattern, key)
  if (nextValue === null) return undefined
  if (nextValue !== undefined) return nextValue
  return matchRoute(browser, pattern, key) ?? undefined
}
