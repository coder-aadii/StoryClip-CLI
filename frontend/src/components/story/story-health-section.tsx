"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { fetchStoryHealth } from "@/lib/story-api";

function StatusPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-background px-3 py-2 text-xs">
      <span className="font-medium">{label}</span>
      {ok ? (
        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="size-3.5" />
          Ready
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <XCircle className="size-3.5" />
          Unavailable
        </span>
      )}
    </div>
  );
}

export function StoryHealthSection() {
  const healthQuery = useQuery({
    queryKey: ["story-health"],
    queryFn: fetchStoryHealth,
    retry: false,
  });

  const data = healthQuery.data;

  return (
    <section className="rounded-2xl border bg-card p-6 shadow-xs">
      <div className="mb-4 flex items-center gap-2.5">
        <div className="flex size-8 items-center justify-center rounded-lg bg-brand/10 text-brand">
          <Activity className="size-4" />
        </div>
        <div>
          <h2 className="font-display text-base font-semibold">Story Clip Backend (studio_api)</h2>
          <p className="text-xs text-muted-foreground">
            Local FFmpeg tools, API keys, and optional TTS daemons on port 8190.
          </p>
        </div>
      </div>

      {healthQuery.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Checking studio_api health...
        </div>
      ) : healthQuery.isError || !data ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
          Cannot reach studio_api at /api/story/health. Start services/studio_api/server.py on port 8190.
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-3">
          <StatusPill label="FFmpeg" ok={data.tools.ffmpeg} />
          <StatusPill label="FFprobe" ok={data.tools.ffprobe} />
          <StatusPill label="xfade Filter" ok={data.tools.xfade} />
          <StatusPill label="Gemini Key" ok={data.api_keys.gemini} />
          <StatusPill label="Pexels Key" ok={data.api_keys.pexels} />
          <StatusPill label="Cloudflare Key" ok={data.api_keys.cloudflare} />
          <StatusPill label="Jamendo Key" ok={data.api_keys.jamendo} />
          <StatusPill label="VoiceStudio" ok={data.local_daemons.voicestudio} />
          <StatusPill label="Indic Parler" ok={data.local_daemons.indic_parler} />
        </div>
      )}
    </section>
  );
}
