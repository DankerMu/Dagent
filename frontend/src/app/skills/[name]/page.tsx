import SkillDetailPage from "./page-client"

// Static export emits a shell; the client reads the actual skill name from the URL.
export function generateStaticParams() {
  return [{ name: "__shell__" }]
}

export default function Page() {
  return <SkillDetailPage />
}
