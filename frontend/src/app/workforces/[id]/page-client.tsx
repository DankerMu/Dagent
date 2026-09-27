"use client"

import React from "react"
import { useRouteParam } from "@/hooks/use-route-param"
import { WorkforceBuilder } from "@/components/workforce/workforce-builder"

export default function WorkforceDetailPage() {
    const id = useRouteParam("/workforces/[id]", "id")
    return id ? <WorkforceBuilder key={id} workforceId={id} /> : null
}
