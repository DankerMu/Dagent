import { vi, type Mock } from "vitest"

// Keep the real upload/error helpers; only the network request crosses the test boundary.
export async function mockApiWrapper(apiRequest: Mock) {
  const actual = await vi.importActual<Record<string, unknown>>("@/lib/api-wrapper")
  return { ...actual, apiRequest }
}

export async function mockLanApiUrls(includeUploads = false) {
  const actual = await vi.importActual<Record<string, unknown>>("@/lib/utils")
  return {
    ...actual,
    getApiUrl: () => "http://api.local",
    ...(includeUploads ? { getUploadApiUrl: () => "http://api.local" } : {}),
  }
}
