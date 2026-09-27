import { vi } from "vitest"
import { identityI18n } from "./test-i18n"

// UI interaction suites use real components with the same identity translations.
vi.mock("@/contexts/i18n-context", () => ({ useI18n: identityI18n }))
