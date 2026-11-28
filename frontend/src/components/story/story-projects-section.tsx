"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard, Download, FileText, Film, Loader2, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SrtPreviewDialog } from "@/components/story/srt-preview-dialog";
import {
  StoryUnauthorizedError,
  fetchStoryProjects,
  storyMediaUrl,
  type StoryProjectSummary,
} from "@/lib/story-api";

export function StoryProjectsSection() {
  const [previewProject, setPreviewProject] = useState<StoryProjectSummary | null>(null);
  const [srtProject, setSrtProject] = useState<StoryProjectSummary | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["story-projects"],
    queryFn: fetchStoryProjects,
    retry: false,
  });

  const projects = projectsQuery.data ?? [];

  return (
    <section className="mb-10">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Clapperboard className="size-4 text-brand" />
          <h2 className="font-display text-lg font-semibold tracking-tight">Story Clip Projects</h2>
          {projects.length > 0 ? (
            <span className="rounded-full bg-accent px-2 py-0.5 text-xs text-muted-foreground">
              {projects.length}
            </span>
          ) : null}
        </div>
        <Link href="/">
          <Button variant="outline" size="sm" className="h-8 text-xs">
            New Story
          </Button>
        </Link>
      </div>

      {projectsQuery.isLoading ? (
        <div className="flex items-center gap-2 rounded-xl border bg-card p-6 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading Story Clip projects...
        </div>
      ) : projectsQuery.error instanceof StoryUnauthorizedError ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-4 text-sm">
          <span className="text-muted-foreground">
            Sign in on the Story Clip tab to view your rendered story projects.
          </span>
          <Link href="/">
            <Button size="sm" variant="outline">Go to Story Clip</Button>
          </Link>
        </div>
      ) : projectsQuery.isError ? (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-600 dark:text-amber-400">
          Could not reach studio_api (/api/story/projects). Start services/studio_api/server.py on port 8190.
        </div>
      ) : projects.length === 0 ? (
        <div className="rounded-xl border bg-card p-6 text-center text-sm text-muted-foreground">
          No Story Clip projects yet. Create one from the Story Clip tab.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <div
              key={project.name}
              className="flex flex-col justify-between rounded-xl border bg-card p-4 shadow-xs"
            >
              <div>
                <div className="mb-1 flex items-start justify-between gap-2">
                  <h3 className="line-clamp-2 font-medium leading-snug">
                    {project.title || project.name}
                  </h3>
                  <span className="shrink-0 rounded-md bg-accent px-2 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
                    {project.language}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">
                  {project.scenes_count} scenes · {project.status}
                </p>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                {project.has_final_video ? (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      className="h-7 gap-1 px-2.5 text-xs"
                      onClick={() => setPreviewProject(project)}
                    >
                      <Play className="size-3" />
                      Play
                    </Button>
                    <a
                      href={storyMediaUrl(project.name, "final.mp4")}
                      download={`${project.name}.mp4`}
                      className="inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2.5 text-xs font-medium hover:bg-accent"
                    >
                      <Download className="size-3" />
                      MP4
                    </a>
                  </>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                    <Film className="size-3.5" />
                    No video yet
                  </span>
                )}
                {project.has_subtitles ? (
                  <button
                    type="button"
                    onClick={() => setSrtProject(project)}
                    className="inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2.5 text-xs font-medium hover:bg-accent"
                  >
                    <FileText className="size-3" />
                    SRT
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}

      {previewProject ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs">
          <div className="w-full max-w-2xl rounded-2xl border bg-card p-4 shadow-xl">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="truncate font-display text-base font-semibold">
                {previewProject.title || previewProject.name}
              </h3>
              <button
                type="button"
                onClick={() => setPreviewProject(null)}
                className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                aria-label="Close preview"
              >
                <X className="size-4" />
              </button>
            </div>
            <video
              src={storyMediaUrl(previewProject.name, "final.mp4")}
              controls
              autoPlay
              playsInline
              className="max-h-[70vh] w-full rounded-xl bg-black object-contain"
            />
          </div>
        </div>
      ) : null}
      {srtProject ? (
        <SrtPreviewDialog
          open={Boolean(srtProject)}
          onOpenChange={(open) => {
            if (!open) setSrtProject(null);
          }}
          srtUrl={storyMediaUrl(srtProject.name, "subtitles.srt")}
          filename={`${srtProject.name}.srt`}
          title={`${srtProject.title || srtProject.name} — Subtitles (.srt)`}
        />
      ) : null}
    </section>
  );
}
