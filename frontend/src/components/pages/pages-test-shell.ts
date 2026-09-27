import { vi } from "vitest"
import { mockApiWrapper, mockLanApiUrls } from "@/lib/test-api-wrapper"
// Keep the request handler in this module: re-exporting a mock whose module
// registers its own hoisted factory can read that binding before initialization.
export const apiRequestMock = vi.fn()

// Files uploads and conversation logs both consume the same LAN API boundary.
vi.mock("@/lib/api-wrapper", () => mockApiWrapper(apiRequestMock))
vi.mock("@/lib/utils", () => mockLanApiUrls(true))
