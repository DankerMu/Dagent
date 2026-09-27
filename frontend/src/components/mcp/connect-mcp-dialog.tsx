import React, { useEffect, useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { getApiUrl } from "@/lib/utils"
import { Globe, Link2, Loader2, Plug } from "lucide-react"
import { useI18n } from "@/contexts/i18n-context"
import { apiRequest } from "@/lib/api-wrapper"
import { toast } from "@/components/ui/sonner"
import {
  isValidMcpName,
  buildCustomApiPayload,
  buildMcpServerPayload,
} from "@/lib/mcp-utils"
import { CustomApiForm, MCPServerFormData } from "./custom-api-form"
import { CustomMcpForm } from "./custom-mcp-form"
import {
  getRuntimeConfigError,
  type RuntimeConfigErrorKey,
} from "./runtime-inputs-form"


interface ConnectMcpDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: (createdName: string) => void
}

export function ConnectMcpDialog({
  open,
  onOpenChange,
  onSuccess,
}: ConnectMcpDialogProps) {
  const { t } = useI18n()
  const [activeTab, setActiveTab] = useState("custom")
  const [isSavingCustom, setIsSavingCustom] = useState(false)
  const [customApiEnv, setCustomApiEnv] = useState<{ key: string, value: string }[]>([{ key: "", value: "" }])
  const [mcpFormData, setMcpFormData] = useState<MCPServerFormData>({
    name: "",
    transport: "stdio",
    description: "",
    config: {},
  })
  const [runtimeValidationError, setRuntimeValidationError] = useState<RuntimeConfigErrorKey | null>(null)

  const resetForm = () => {
    setMcpFormData({
      name: "",
      transport: activeTab === "custom_api" ? "custom_api" : "stdio",
      description: "",
      config: activeTab === "custom_api" ? { env: {} } : {},
      user_env: {},
      can_edit_global: true,
    })
    setCustomApiEnv([{ key: "", value: "" }])
    setRuntimeValidationError(null)
  }

  useEffect(() => {
    if (open) {
      setActiveTab("custom")
      setMcpFormData({
        name: "",
        transport: "stdio",
        description: "",
        config: {},
        user_env: {},
        can_edit_global: true,
      })
      setCustomApiEnv([{ key: "", value: "" }])
      setRuntimeValidationError(null)
    }
  }, [open])

  const requestClose = () => {
    onOpenChange(false)
    setRuntimeValidationError(null)
  }

  const saveConnector = async (endpoint: string, payload: object, name: string, errorContext: string) => {
    setIsSavingCustom(true)
    try {
      const response = await apiRequest(`${getApiUrl()}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (response.ok) {
        toast.success(t("tools.mcp.buttons.save"))
        onSuccess?.(name)
        requestClose()
        resetForm()
      } else {
        const error = await response.json()
        toast.error(error.detail || t("tools.mcp.alerts.saveFailed"))
      }
    } catch (error) {
      console.error(errorContext, error)
      toast.error(t("tools.mcp.alerts.saveFailed"))
    } finally {
      setIsSavingCustom(false)
    }
  }

  const handleSaveCustomMcp = async () => {
    if (!mcpFormData.name.trim()) {
      toast.error(t("tools.mcp.alerts.nameRequired"))
      return
    }
    if (!isValidMcpName(mcpFormData.name)) {
      toast.error(t("tools.mcp.alerts.nameInvalidFormat") || "Name can only contain letters, numbers, hyphens and underscores")
      return
    }

    const formPayload = { ...mcpFormData }
    const connectorType = formPayload.transport === "custom_api" ? "custom_api" : "mcp"
    const runtimeError = runtimeValidationError || getRuntimeConfigError(formPayload, connectorType)
    if (runtimeError) {
      toast.error(t(runtimeError))
      return
    }

    if (formPayload.transport === "custom_api") {
      if (!mcpFormData.url?.trim()) {
        toast.error(t("tools.mcp.alerts.urlRequired"))
        return
      }
      const buildResult = buildCustomApiPayload(formPayload, customApiEnv)
      if (!buildResult.isValid) {
        toast.error(t(buildResult.errorKey || "tools.mcp.alerts.atLeastOneSecret"))
        return
      }
      await saveConnector("/api/custom-apis", buildResult.payload, formPayload.name, "Failed to save custom API:")
      return
    }

    await saveConnector("/api/mcp/servers", buildMcpServerPayload(formPayload), formPayload.name, "Failed to save custom MCP server:")
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          requestClose()
          return
        }
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="sm:max-w-5xl md:max-w-6xl w-[95vw] h-[85vh] flex flex-col p-0 overflow-hidden gap-0 bg-slate-50">
        <DialogHeader className="px-6 py-4 border-b bg-white shrink-0 pr-10">
          <DialogTitle className="text-xl flex items-center gap-2 font-bold text-left">
            <Plug className="h-5 w-5 text-blue-600 shrink-0" /> {t("tools.mcp.dialog.connector")}
          </DialogTitle>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={(value) => {
          setActiveTab(value)
          setRuntimeValidationError(null)
          setMcpFormData({
            name: "",
            transport: value === "custom_api" ? "custom_api" : "stdio",
            description: "",
            config: value === "custom_api" ? { env: {} } : {},
            user_env: {},
            can_edit_global: true,
          })
          setCustomApiEnv([{ key: "", value: "" }])
        }} className="flex-1 flex flex-col overflow-hidden bg-white">
          <div className="px-6 border-b shrink-0 bg-white overflow-x-auto overflow-y-hidden">
            <TabsList className="bg-transparent h-14 p-0 border-b-0 space-x-6 min-w-max">
              <TabsTrigger
                value="custom_api"
                className="data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-blue-600 data-[state=active]:text-blue-600 rounded-none h-full px-0 font-semibold flex items-center gap-2 text-slate-500"
              >
                <Globe className="h-4 w-4" /> {t("tools.mcp.dialog.customApi")}
              </TabsTrigger>
              <TabsTrigger
                value="custom"
                className="data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-blue-600 data-[state=active]:text-blue-600 rounded-none h-full px-0 font-semibold flex items-center gap-2 text-slate-500"
              >
                <Link2 className="h-4 w-4" /> {t("tools.mcp.dialog.customMcp")}
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="custom_api" className="flex-1 overflow-y-auto p-6 m-0 bg-slate-50/50">
            <div className="max-w-2xl mx-auto w-full">
              <div className="mb-6">
                <h2 className="text-xl font-bold">{t("tools.mcp.dialog.addCustomApi")}</h2>
                <p className="text-sm text-slate-500 mt-1">{t("tools.mcp.dialog.customApiDescription")}</p>
              </div>
              <div className="space-y-4">
                <CustomApiForm
                  key="new"
                  mcpFormData={mcpFormData}
                  setMcpFormData={setMcpFormData}
                  customApiEnv={customApiEnv}
                  setCustomApiEnv={setCustomApiEnv}
                  onRuntimeValidationErrorChange={setRuntimeValidationError}
                  originalEnvObj={{}}
                />
              </div>
              <div className="flex justify-end gap-3 mt-8 pt-4 border-t">
                <Button variant="outline" onClick={requestClose}>
                  {t("tools.mcp.buttons.cancel")}
                </Button>
                <Button
                  onClick={handleSaveCustomMcp}
                  disabled={
                    isSavingCustom ||
                    !mcpFormData.name?.trim() ||
                    !mcpFormData.url?.trim() ||
                    (customApiEnv.length > 0 && customApiEnv.some((env) => env.key.trim() && !env.value.trim()))
                  }
                >
                  {isSavingCustom && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("tools.mcp.buttons.save")}
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="custom" className="flex-1 overflow-y-auto p-6 m-0 bg-slate-50/50">
            <div className="max-w-2xl mx-auto w-full">
              <div className="mb-6">
                <h2 className="text-xl font-bold">{t("tools.mcp.dialog.addTitle")}</h2>
                <p className="text-sm text-slate-500 mt-1">{t("tools.mcp.dialog.description")}</p>
              </div>
              <div className="space-y-4">
                <CustomMcpForm
                  key="new"
                  mcpFormData={mcpFormData}
                  setMcpFormData={setMcpFormData}
                  onRuntimeValidationErrorChange={setRuntimeValidationError}
                />
              </div>
              <div className="flex justify-end gap-3 mt-8">
                <Button variant="outline" onClick={requestClose}>
                  {t("tools.mcp.buttons.cancel")}
                </Button>
                <Button onClick={handleSaveCustomMcp} disabled={isSavingCustom}>
                  {isSavingCustom && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {t("tools.mcp.buttons.save")}
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
