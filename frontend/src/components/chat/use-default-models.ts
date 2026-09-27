import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api-wrapper";
import { getApiUrl } from "@/lib/utils";

interface ModelRecord {
  id?: number | string;
  model_id?: string;
  model_name?: string;
  is_default?: boolean;
}

interface DefaultModelRecord {
  config_type?: string;
  model?: ModelRecord | null;
}

export interface DefaultAgentModelConfig {
  model: string;
  smallFastModel?: string;
  visualModel?: string;
  compactModel?: string;
}

function resolveDefaults(allModels: ModelRecord[], defaults: DefaultModelRecord[]): DefaultAgentModelConfig {
  const selected: Record<string, ModelRecord | undefined> = {};
  if (Array.isArray(defaults)) {
    defaults.forEach((entry) => {
      if (entry?.config_type && entry.model) selected[entry.config_type] = entry.model;
    });
  }
  if (!selected.general && allModels.length > 0) {
    selected.general = allModels.find((model) => model.is_default) || allModels[0];
  }
  return {
    model: selected.general?.model_id || "",
    smallFastModel: selected.small_fast?.model_id,
    visualModel: selected.visual?.model_id,
    compactModel: selected.compact?.model_id,
  };
}

export function useDefaultModels(
  hideConfig: boolean,
  onDefaultsLoaded: (defaults: DefaultAgentModelConfig) => void,
) {
  const [defaultAgentConfig, setDefaultAgentConfig] = useState<DefaultAgentModelConfig>({ model: "" });
  const [models, setModels] = useState<ModelRecord[]>([]);

  // Keep the request alive through both response bodies, not just the headers.
  useEffect(() => {
    if (hideConfig) return;

    let activeRequest: AbortController | null = null;
    const fetchDefaultModels = async () => {
      const request = new AbortController();
      activeRequest = request;
      try {
        const apiUrl = getApiUrl();

        // Fetch all models first to have the list for display names
        const modelsResponse = await apiRequest(`${apiUrl}/api/models/?category=llm`, {
          headers: {},
          signal: request.signal
        });
        if (request.signal.aborted) return;

        let allModels: ModelRecord[] = [];
        if (modelsResponse.ok) {
          allModels = await modelsResponse.json();
          if (request.signal.aborted) return;
          if (Array.isArray(allModels)) {
            setModels(allModels);
          }
        }

        // Fetch user default models
        const defaultResponse = await apiRequest(`${apiUrl}/api/models/user-default`, {
          headers: {},
          signal: request.signal
        });
        if (request.signal.aborted) return;

        const defaults = defaultResponse.ok ? await defaultResponse.json() : [];
        if (request.signal.aborted) return;
        const newDefaultConfig = resolveDefaults(allModels, defaults);

        setDefaultAgentConfig(newDefaultConfig);
        onDefaultsLoaded(newDefaultConfig);
      } catch (error) {
        if (!request.signal.aborted) {
          console.error('Failed to fetch default models:', error);
        }
      }
    };

    const handlePageHide = () => {
      activeRequest?.abort();
      activeRequest = null;
    };
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) fetchDefaultModels();
    };

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("pageshow", handlePageShow);
    fetchDefaultModels();
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handlePageShow);
      handlePageHide();
    };
  }, [hideConfig, onDefaultsLoaded]);

  return { defaultAgentConfig, models };
}
