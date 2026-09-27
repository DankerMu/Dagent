"use client"

import React, { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { apiRequest } from "@/lib/api-wrapper"
import { getApiUrl } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { ArrowLeft, Bot } from "lucide-react"
import { useI18n } from "@/contexts/i18n-context"
import { useApp } from "@/contexts/app-context-chat"
import { ChatStartScreen } from "@/components/chat/ChatStartScreen"
import { toast } from "@/components/ui/sonner"
import { useRouteParam } from "@/hooks/use-route-param"

function getModelDetailUrl(modelId: string | number): string {
  return `${getApiUrl()}/api/models/by-id/${encodeURIComponent(String(modelId))}`
}

interface Agent {
  id: number
  name: string
  description: string
  logo_url: string | null
  instructions: string | null
  execution_mode: string
  suggested_prompts: string[]
  models?: {
    general?: number
    small_fast?: number
    visual?: number
    compact?: number
  }
}

export default function AgentChatPage() {
  const agentId = useRouteParam("/agent/[id]", "id")
  return agentId ? <AgentChatContent key={agentId} agentId={agentId} /> : null
}

function AgentChatContent({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const { dispatch, sendMessage } = useApp()
  const router = useRouter()

  const [agent, setAgent] = useState<Agent | null>(null)
  const [loading, setLoading] = useState(true)
  const [agentModelId, setAgentModelId] = useState<string>("")
  const [isSending, setIsSending] = useState(false)
  const [inputValue, setInputValue] = useState("")
  const [files, setFiles] = useState<File[]>([])
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    dispatch({ type: "RESET_STATE" })
  }, [dispatch, agentId])

  // Load agent
  useEffect(() => {
    let active = true
    const fetchAgent = async () => {
      try {
        setLoading(true)
        const response = await apiRequest(`${getApiUrl()}/api/agents/${encodeURIComponent(agentId)}`)
        if (!active) return
        if (response.ok) {
          const data = await response.json()
          if (!active) return
          setAgent(data)

          if (data.models?.general) {
            try {
              const modelResponse = await apiRequest(getModelDetailUrl(data.models.general))
              if (modelResponse.ok) {
                const modelData = await modelResponse.json()
                if (active) setAgentModelId(modelData.model_id || modelData.name || "")
              }
            } catch (err) {
              if (active) console.error("Failed to load model name:", err)
            }
          }
        } else if (active) {
          toast.error(t('builds.list.chat.notFound'))
        }
      } catch (err) {
        if (active) {
          console.error("Failed to load agent:", err)
          toast.error(t('builds.list.chat.failed'))
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void fetchAgent()
    return () => { active = false }
  }, [agentId, t])

  const handleSendMessage = async (content: string, filesToSend: File[]) => {
    setIsSending(true)

    try {
      const parsedAgentId = parseInt(agentId, 10)
      if (Number.isNaN(parsedAgentId)) {
        throw new Error(t('builds.list.chat.notFound'))
      }

      await sendMessage(content, { agentId: parsedAgentId }, filesToSend)
      if (mountedRef.current) {
        setInputValue("")
        setFiles([])
      }
    } catch (err) {
      if (mountedRef.current) {
        console.error("Failed to send message:", err)
        toast.error(err instanceof Error ? err.message : t('builds.list.chat.sendFailed'))
      }
    } finally {
      if (mountedRef.current) setIsSending(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <Bot className="h-12 w-12 mx-auto mb-4 animate-pulse text-muted-foreground" />
          <p className="text-muted-foreground">{t('builds.list.chat.loading')}</p>
        </div>
      </div>
    )
  }

  return (
    <>
      {!agent ? <div className="flex h-full items-center justify-center">
        <div className="max-w-md w-full text-center space-y-6">
          {/* Icon */}
          <div className="flex justify-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
              <Bot className="h-6 w-6 text-muted-foreground" />
            </div>
          </div>

          {/* Title */}
          <div className="space-y-2">
            <h2 className="text-lg font-semibold">
              {t('builds.list.chat.notFound')}
            </h2>
            <p className="text-sm text-muted-foreground">
              {t('builds.list.chat.notFoundDescription')}
            </p>
          </div>

          {/* Action */}
          <Button
            className="w-full"
            onClick={() => router.push("/build")}
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            {t('builds.list.header.create')}
          </Button>
        </div>
      </div> : <div className="h-full bg-background flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto">
          <main className="container max-w-4xl mx-auto px-4 py-8">
            <ChatStartScreen
              title={agent.name}
              description={agent.description || undefined}
              prompts={agent.suggested_prompts}
              onSend={(msg, filesToSend) => handleSendMessage(msg, filesToSend)}
              isSending={isSending}
              inputValue={inputValue}
              onInputChange={setInputValue}
              files={files}
              onFilesChange={setFiles}
              readOnlyConfig={true}
              taskConfig={{ model: agentModelId }}
              autoFocus={true}
            />
          </main>
        </div>
      </div>}
    </>
  )
}
