export function identityI18n() {
  return {
    t: (key: string) => key,
    tDynamic: (_key: string, fallback: string) => fallback,
  }
}
