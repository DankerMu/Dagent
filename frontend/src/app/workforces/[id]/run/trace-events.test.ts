import { describe, expect, it } from "vitest"
import { getAgentExecutionConclusion, mergeAgentExecutionTraceEvents } from "./trace-events"

describe("Workforce execution traces", () => {
  it("merges streaming child traces into the selected Agent execution", () => {
    const merged = mergeAgentExecutionTraceEvents(
      [{
        event_id: "persisted-start",
        event_type: "react_task_start",
        data: {
          source: "xagent-agent-tool-child",
          worker_task_id: "agent_17_live",
        },
      }],
      [{
        event_id: "live-progress",
        event_type: "agent_progress",
        data: {
          source: "xagent-agent-tool-child",
          worker_task_id: "agent_17_live",
          message: "Generating scene 4",
        },
      }, {
        event_id: "other-worker",
        event_type: "agent_progress",
        data: {
          source: "xagent-agent-tool-child",
          worker_task_id: "agent_18_other",
        },
      }],
      "agent_17_live",
    )

    expect(merged.map((event) => event.event_id)).toEqual([
      "persisted-start",
      "live-progress",
    ])
  })

  it("ignores malformed streaming child trace payloads", () => {
    expect(mergeAgentExecutionTraceEvents(
      [],
      [
        null,
        42,
        "not-an-event",
        { event_id: "null-data", data: null },
        { event_id: "string-data", data: "not-an-object" },
        { event_id: "array-data", data: [] },
      ],
      "agent_17_live",
    )).toEqual([])
  })

  it("extracts a delegated Agent conclusion from terminal trace events", () => {
    expect(getAgentExecutionConclusion([{
      event_type: "react_task_end",
      data: { result: { output: "Fallback conclusion" } },
    }, {
      event_type: "ai_message",
      data: { content: "Agent's final conclusion" },
    }, {
      event_type: "task_completion",
      data: { result: { content: "Canonical completion" } },
    }])).toBe("Canonical completion")
  })

})
