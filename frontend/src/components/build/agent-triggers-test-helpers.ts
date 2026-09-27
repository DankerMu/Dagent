import type { AgentTrigger } from "@/lib/agent-triggers-api"

export function testTrigger(overrides: Partial<AgentTrigger> & { id: number }): AgentTrigger {
  return {
    user_id: 1,
    agent_id: 42,
    type: "webhook",
    name: "Trigger",
    enabled: true,
    config: {},
    prompt_template: null,
    webhook_token: null,
    webhook_secret: null,
    next_run_at: null,
    last_run_at: null,
    last_error: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}
