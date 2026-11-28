"use client";

import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";

const HomeApp = dynamic(() => import("@/components/home-app"), {
  ssr: false,
  loading: () => (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-2xl space-y-4">
        <Skeleton className="mx-auto h-9 w-64" />
        <Skeleton className="h-40 w-full rounded-2xl" />
      </div>
    </div>
  ),
});

export default function VideosToClipPage() {
  return <HomeApp />;
}
