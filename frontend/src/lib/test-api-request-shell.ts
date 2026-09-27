import { vi } from "vitest"
import { mockApiWrapper } from "./test-api-wrapper"

// Test files are isolated by Vitest; each suite resets its own request handler.
export const apiRequestMock = vi.fn()
vi.mock("@/lib/api-wrapper", () => mockApiWrapper(apiRequestMock))
