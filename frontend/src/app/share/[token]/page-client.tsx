"use client"

import { useRouteParam } from "@/hooks/use-route-param"
import { PublicAgentChatPage } from "@/components/widget/public-agent-chat-page"

export default function ShareChatPage() {
  const token = useRouteParam("/share/[token]", "token")
  if (!token) return null

  return (
    <PublicAgentChatPage key={token}
      authMode="share"
      routeToken={token}
    />
  )
}
