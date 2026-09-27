import { useCallback, useEffect, useRef } from "react"

// A sidebar task request remains owned until both its headers and body have settled.
export function useSidebarTaskRequestLifetime(onRestore: () => void) {
  const onRestoreRef = useRef(onRestore)
  onRestoreRef.current = onRestore
  const activeRef = useRef(true)
  const requestsRef = useRef(new Set<AbortController>())
  const lifetimeRef = useRef(0)

  useEffect(() => {
    activeRef.current = true
    const cancel = () => {
      activeRef.current = false
      lifetimeRef.current += 1
      requestsRef.current.forEach(controller => controller.abort())
    }
    const restore = (event: PageTransitionEvent) => {
      if (!event.persisted) return
      activeRef.current = true
      onRestoreRef.current()
    }
    window.addEventListener("pagehide", cancel)
    window.addEventListener("pageshow", restore)
    return () => {
      window.removeEventListener("pagehide", cancel)
      window.removeEventListener("pageshow", restore)
      cancel()
    }
  }, [])

  const begin = useCallback(() => {
    if (!activeRef.current) return null
    const controller = new AbortController()
    requestsRef.current.add(controller)
    return controller
  }, [])
  const isActive = useCallback((controller?: AbortController, lifetime?: number) =>
    activeRef.current && !controller?.signal.aborted &&
    (lifetime === undefined || lifetime === lifetimeRef.current), [])
  const finish = useCallback((controller: AbortController) => {
    requestsRef.current.delete(controller)
  }, [])
  const currentLifetime = useCallback(() => lifetimeRef.current, [])

  return { begin, isActive, finish, currentLifetime }
}
