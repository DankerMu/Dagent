"use client"

import { AgentBuilder } from "@/components/build/agent-builder"
import { useRouteParam } from "@/hooks/use-route-param"

export default function BuildDetailPage() {
  const id = useRouteParam("/build/[id]", "id")
  return id ? <AgentBuilder key={id} agentId={id} /> : null
}
