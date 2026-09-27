import { vi } from "vitest";
import { mockApiWrapper, mockLanApiUrls } from "@/lib/test-api-wrapper";

export const apiRequestMock = vi.fn();
export const toastErrorMock = vi.fn();
export const routerPushMock = vi.fn();

function createTemplateTestI18n() {
  return {
    t: (key: string, vars?: Record<string, string | number>) =>
      vars ? `${key}:${JSON.stringify(vars)}` : key,
    tDynamic: (_key: string, fallback: string) => fallback,
    locale: "en",
  };
}

vi.mock("@/lib/api-wrapper", () => mockApiWrapper(apiRequestMock));
vi.mock("@/lib/utils", () => mockLanApiUrls());
vi.mock("sonner", () => ({ toast: { error: toastErrorMock } }));
vi.mock("@/contexts/i18n-context", () => ({ useI18n: createTemplateTestI18n }));
