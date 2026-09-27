import { vi } from "vitest"
import { mockApiWrapper, mockLanApiUrls } from "@/lib/test-api-wrapper"

// ToolsPage and ConnectMcpDialog share the authenticated LAN API boundary.
export const apiRequestMock = vi.fn()
vi.mock("@/lib/api-wrapper", () => mockApiWrapper(apiRequestMock))
vi.mock("@/lib/utils", () => mockLanApiUrls())
