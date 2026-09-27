export type SkillSource = "builtin" | "user" | "team" | "external"

export interface SkillSummary {
  name: string
  description: string
  when_to_use: string
  tags: string[]
  source: SkillSource
  scope: string | null
  effective: boolean
  shadowed_by: string | null
}

export interface SkillDetail extends SkillSummary {
  content: string
  execution_flow: string
  files: string[]
  path: string
}

export async function skillResponseError(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === "object" && "detail" in body) {
      const detail = body.detail
      if (typeof detail === "string" && detail.trim()) return detail
      if (Array.isArray(detail)) {
        const messages = detail.flatMap((item: unknown) =>
          item && typeof item === "object" && "msg" in item && typeof item.msg === "string"
            ? [item.msg] : [])
        if (messages.length) return messages.join("; ")
      }
    }
  } catch {
    // Non-JSON errors use the localized fallback rather than displaying HTML.
  }
  return fallback
}
