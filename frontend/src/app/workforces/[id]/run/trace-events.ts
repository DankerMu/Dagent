import type { WorkforceAgentExecutionTraceEvent } from "@/types/workforce"

function normalizeTraceEventData(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {}
  return value as Record<string, unknown>
}

export function sanitizeAgentExecutionTraceEvents(
  value: unknown,
): WorkforceAgentExecutionTraceEvent[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((candidate) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      return []
    }
    const event = candidate as Record<string, unknown>
    return [{
      event_id: typeof event.event_id === "string" ? event.event_id : undefined,
      event_type: typeof event.event_type === "string" ? event.event_type : undefined,
      step_id: typeof event.step_id === "string" || event.step_id === null
        ? event.step_id
        : undefined,
      timestamp: typeof event.timestamp === "number" ||
        typeof event.timestamp === "string" ||
        event.timestamp === null
        ? event.timestamp
        : undefined,
      data: normalizeTraceEventData(event.data),
      parent_event_id: typeof event.parent_event_id === "string" ||
        event.parent_event_id === null
        ? event.parent_event_id
        : undefined,
    }]
  })
}

export function mergeAgentExecutionTraceEvents(
  historicalEvents: unknown,
  liveEvents: unknown,
  workerTaskId: string,
): WorkforceAgentExecutionTraceEvent[] {
  const events = sanitizeAgentExecutionTraceEvents(historicalEvents)
  const knownEventIds = new Set(events.map((event) => event.event_id).filter(Boolean))
  for (const event of sanitizeAgentExecutionTraceEvents(liveEvents)) {
    const eventData = event.data ?? {}
    if (
      eventData["source"] !== "xagent-agent-tool-child" ||
      String(eventData["worker_task_id"] ?? "") !== workerTaskId ||
      (event.event_id && knownEventIds.has(event.event_id))
    ) {
      continue
    }
    events.push({
      ...event,
      data: eventData,
    })
    if (event.event_id) knownEventIds.add(event.event_id)
  }
  return events
}

function formatAgentConclusion(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null
  if (!value || typeof value !== "object") return null
  try {
    return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``
  } catch {
    return null
  }
}

function taskCompletionConclusion(data: Record<string, unknown>): string | null {
  const result = data.result
  if (result && typeof result === "object") {
    const fields = result as Record<string, unknown>
    const conclusion = formatAgentConclusion(
      fields.chat_response ?? fields.content ?? fields.output ?? fields.message,
    )
    if (conclusion) return conclusion
  }
  return formatAgentConclusion(result ?? data.content ?? data.output)
}

function reactCompletionConclusion(result: unknown): string | null {
  if (result && typeof result === "object") {
    const fields = result as Record<string, unknown>
    const conclusion = formatAgentConclusion(
      fields.output ?? fields.content ?? fields.message,
    )
    if (conclusion) return conclusion
  }
  return formatAgentConclusion(result)
}

function eventConclusion(event: WorkforceAgentExecutionTraceEvent): string | null {
  const data = event.data
  if (!data) return null
  switch (event.event_type) {
    case "task_completion":
      return taskCompletionConclusion(data)
    case "ai_message":
    case "agent_message":
      return formatAgentConclusion(data.content ?? data.message)
    case "react_task_end":
    case "task_end_react":
      return reactCompletionConclusion(data.result)
    default:
      return null
  }
}

export function getAgentExecutionConclusion(
  events: WorkforceAgentExecutionTraceEvent[],
): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const conclusion = eventConclusion(events[index])
    if (conclusion) return conclusion
  }
  return null
}
