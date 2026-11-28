"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertCircle, Check, ChevronDown, Download, FileText, Loader2, RotateCcw, Square, Terminal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { SrtPreviewDialog } from "@/components/story/srt-preview-dialog";
import {
  createStoryWsTicket,
  getStoryJobStatus,
  stopStoryRender,
  storyMediaUrl,
  storyWsUrl,
  type StoryJobStatus,
} from "@/lib/story-api";
import { cn } from "@/lib/utils";

const STAGES = [
  { id: "planning", label: "Planning" },
  { id: "visuals", label: "Visuals" },
  { id: "voice", label: "Voice" },
  { id: "rendering", label: "Rendering" },
  { id: "finalizing", label: "Finalizing" },
] as const;

function stageIndexFor(stageText: string, status: string): number {
  if (status === "completed") return STAGES.length;
  const s = stageText.toLowerCase();
  if (s.includes("assembling") || s.includes("finalizing") || s.includes("complete")) return 4;
  if (s.includes("rendering")) return 3;
  if (s.includes("voice") || s.includes("speech") || s.includes("tts")) return 2;
  if (s.includes("visual")) return 1;
  return 0;
}

export function StoryRenderPanel({
  jobId,
  onReset,
}: {
  jobId: string;
  onReset: () => void;
}) {
  const [status, setStatus] = useState<StoryJobStatus["status"]>("pending");
  const [stage, setStage] = useState("Queued");
  const [progress, setProgress] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [projectSlug, setProjectSlug] = useState<string | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);
  const [srtOpen, setSrtOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    let ws: WebSocket | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    async function syncJob() {
      try {
        const job = await getStoryJobStatus(jobId);
        if (!active) return;
        setStatus(job.status);
        const terminal = job.status === "completed" || job.status === "failed" || job.status === "interrupted";
        if (terminal || (job.stage && job.stage !== "Queued")) {
          setStage(job.stage || job.status);
        }
        setProgress((prev) => (terminal ? (job.progress ?? prev) : Math.max(prev, job.progress || 0)));
        setProjectSlug(job.project_slug);
        if (Array.isArray(job.logs) && job.logs.length > 0) {
          setLogs(job.logs);
        }
        if (job.status === "completed" || job.status === "failed" || job.status === "interrupted") {
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
        }
      } catch {
        // Ignore transient poll errors while WS is active
      }
    }

    async function connectWs() {
      try {
        const ticket = await createStoryWsTicket();
        if (!active) return;
        ws = new WebSocket(storyWsUrl(ticket, jobId));
        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data) as {
              type?: string;
              data?: { progress?: number; stage?: string; log?: string; status?: StoryJobStatus["status"] };
            };
            if (msg.type === "progress" && msg.data) {
              if (typeof msg.data.progress === "number") setProgress(msg.data.progress);
              if (msg.data.stage) setStage(msg.data.stage);
              if (msg.data.log) {
                const line = msg.data.log;
                setLogs((prev) => (prev[prev.length - 1] === line ? prev : [...prev.slice(-300), line]));
              }
              setStatus("running");
            } else if (msg.type === "job_finished" && msg.data) {
              if (msg.data.status) setStatus(msg.data.status);
              if (msg.data.stage) setStage(msg.data.stage);
              if (typeof msg.data.progress === "number") setProgress(msg.data.progress);
              void syncJob();
            }
          } catch {
            // Ignore non-JSON ping/pong
          }
        };
      } catch {
        // Polling fallback handles status updates if WS fails
      }
    }

    void syncJob();
    void connectWs();
    pollTimer = setInterval(() => {
      void syncJob();
    }, 3000);

    return () => {
      active = false;
      if (pollTimer) clearInterval(pollTimer);
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close();
      }
    };
  }, [jobId]);

  useEffect(() => {
    if (logsOpen) {
      logEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, logsOpen]);

  async function handleStop() {
    setStopping(true);
    try {
      await stopStoryRender(jobId);
      setStatus("interrupted");
      setStage("Stopped by user");
    } finally {
      setStopping(false);
    }
  }

  const activeStageIndex = stageIndexFor(stage, status);
  const isFinished = status === "completed" || status === "failed" || status === "interrupted";
  const videoSrc = projectSlug ? storyMediaUrl(projectSlug, "final.mp4") : null;
  const srtSrc = projectSlug ? storyMediaUrl(projectSlug, "subtitles.srt") : null;

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border bg-card p-5 shadow-xs">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {projectSlug ? `Project: ${projectSlug}` : "Render Job"}
            </p>
            <h2 className="font-display text-lg font-bold tracking-tight">{stage}</h2>
          </div>

          <div className="flex items-center gap-2">
            {!isFinished ? (
              <Button type="button" variant="outline" size="sm" onClick={handleStop} disabled={stopping}>
                {stopping ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <Square className="mr-1.5 size-3.5" />}
                Stop
              </Button>
            ) : (
              <Button type="button" variant="outline" size="sm" onClick={onReset}>
                <RotateCcw className="mr-1.5 size-3.5" />
                New Story
              </Button>
            )}
          </div>
        </div>

        <Progress value={Math.min(100, Math.max(0, progress))} className="mb-4 h-2" />

        <div className="grid grid-cols-5 gap-2">
          {STAGES.map((item, idx) => {
            const done = status === "completed" || idx < activeStageIndex;
            const current = !isFinished && idx === activeStageIndex;
            return (
              <div
                key={item.id}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-xs font-medium transition-colors",
                  done && "border-brand/30 bg-brand/10 text-foreground",
                  current && "border-foreground/30 bg-accent text-foreground",
                  !done && !current && "border-border/60 text-muted-foreground",
                )}
              >
                {done ? (
                  <Check className="size-3.5 shrink-0 text-brand" />
                ) : current ? (
                  <Loader2 className="size-3.5 shrink-0 animate-spin" />
                ) : (
                  <span className="size-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
                )}
                <span className="truncate">{item.label}</span>
              </div>
            );
          })}
        </div>

        {(status === "failed" || status === "interrupted") && (
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="size-4 shrink-0" />
            <span>Render ended with status: {status}. Open the logs below for details.</span>
          </div>
        )}

        <div className="mt-4 border-t pt-3">
          <button
            type="button"
            onClick={() => setLogsOpen((open) => !open)}
            className="flex w-full items-center justify-between text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            <span className="inline-flex items-center gap-1.5">
              <Terminal className="size-3.5" />
              Terminal Logs ({logs.length})
            </span>
            <ChevronDown className={cn("size-4 transition-transform", logsOpen && "rotate-180")} />
          </button>

          {logsOpen ? (
            <div className="mt-2 max-h-56 overflow-y-auto rounded-xl border bg-background p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
              {logs.length === 0 ? (
                <p>Waiting for output...</p>
              ) : (
                logs.map((line, i) => (
                  <div key={`${i}-${line.slice(0, 16)}`} className="whitespace-pre-wrap break-all">
                    {line}
                  </div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          ) : null}
        </div>
      </div>

      {status === "completed" && videoSrc ? (
        <div className="rounded-2xl border bg-card p-5 shadow-xs">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-display text-base font-semibold">Final Video</h3>
            <div className="flex items-center gap-2">
              <a
                href={videoSrc}
                download={`${projectSlug || "storyclip"}.mp4`}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-3 text-xs font-medium hover:bg-accent"
              >
                <Download className="size-3.5" />
                MP4
              </a>
              {srtSrc ? (
                <button
                  type="button"
                  onClick={() => setSrtOpen(true)}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-3 text-xs font-medium hover:bg-accent"
                >
                  <FileText className="size-3.5" />
                  Subtitles (.srt)
                </button>
              ) : null}
            </div>
          </div>

          <video
            src={videoSrc}
            controls
            playsInline
            className="max-h-[480px] w-full rounded-xl border bg-black object-contain"
          />

          {srtSrc ? (
            <SrtPreviewDialog
              open={srtOpen}
              onOpenChange={setSrtOpen}
              srtUrl={srtSrc}
              filename={`${projectSlug || "storyclip"}.srt`}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
