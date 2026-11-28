"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clapperboard, FileText, Loader2, Scissors, SlidersHorizontal, Sparkles, Volume2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { StoryRenderPanel } from "@/components/story/story-render-panel";
import {
  StoryUnauthorizedError,
  fetchStoryOptions,
  fetchStoryProjects,
  triggerStoryRender,
} from "@/lib/story-api";

export function StoryClipStudio() {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"idea" | "script">("idea");
  const [prompt, setPrompt] = useState("");
  const [language, setLanguage] = useState("en");
  const [format, setFormat] = useState("shorts");
  const [tts, setTts] = useState("edge-male-energetic");
  const [minutes, setMinutes] = useState(1);

  // Advanced popover state
  const [imageProvider, setImageProvider] = useState("pexels-video");
  const [outro, setOutro] = useState("default");
  const [bgmVolume, setBgmVolume] = useState(0.14);
  const [captions, setCaptions] = useState(true);
  const [theme, setTheme] = useState("");
  const [outputName, setOutputName] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(() => {
    if (typeof sessionStorage === "undefined") return null;
    return sessionStorage.getItem("storyclip_active_job_id");
  });

  function updateActiveJobId(jobId: string | null) {
    setActiveJobId(jobId);
    if (typeof sessionStorage !== "undefined") {
      if (jobId) sessionStorage.setItem("storyclip_active_job_id", jobId);
      else sessionStorage.removeItem("storyclip_active_job_id");
    }
  }

  const optionsQuery = useQuery({
    queryKey: ["story-options"],
    queryFn: fetchStoryOptions,
  });

  const projectsQuery = useQuery({
    queryKey: ["story-projects"],
    queryFn: fetchStoryProjects,
  });

  async function handleGenerate() {
    if (!prompt.trim() || submitting) return;
    setSubmitting(true);
    try {
      const res = await triggerStoryRender({
        idea: mode === "idea" ? prompt.trim() : undefined,
        script: mode === "script" ? prompt.trim() : undefined,
        language,
        minutes: mode === "idea" ? minutes : 1,
        tts,
        format,
        image_provider: imageProvider,
        outro,
        theme: theme.trim() || undefined,
        bgm_volume: bgmVolume,
        captions,
        output_name: outputName.trim() || undefined,
      });
      updateActiveJobId(res.job_id);
      void queryClient.invalidateQueries({ queryKey: ["story-projects"] });
    } catch (err) {
      if (err instanceof StoryUnauthorizedError) {
        void queryClient.invalidateQueries({ queryKey: ["story-me"] });
      }
      toast.error(err instanceof Error ? err.message : "Failed to start video generation");
    } finally {
      setSubmitting(false);
    }
  }

  const opts = optionsQuery.data;
  const languages = opts?.languages ?? ["en", "hi"];
  const formats = opts?.formats ?? ["shorts", "landscape"];
  const ttsProviders = opts?.tts_providers ?? ["edge-male-energetic", "edge-female", "gemini", "indic-parler", "voicestudio"];
  const imageProviders = opts?.image_providers ?? ["pexels-video", "pexels", "cloudflare-flux"];
  const outroProfiles = opts?.outro_profiles ?? ["default", "facts-sun-le-bhai", "shorts"];
  const recentProjects = (projectsQuery.data ?? []).slice(0, 4);

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-4 py-10 sm:px-6">
      <div className="mb-6 flex items-center justify-center">
        <div className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
          <Clapperboard className="size-3.5 text-brand" />
          Story Clip Studio
        </div>
      </div>

      {activeJobId ? (
        <StoryRenderPanel
          jobId={activeJobId}
          onReset={() => {
            updateActiveJobId(null);
            void queryClient.invalidateQueries({ queryKey: ["story-projects"] });
          }}
        />
      ) : (
        <>
          <div className="mb-7 text-center">
            <h1 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
              Turn an idea or script into a narrated video
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              One-click story planning, voiceover synthesis, stock/AI visuals, and subtitle rendering.
            </p>
          </div>

          <div className="rounded-2xl border bg-card p-4 shadow-xs">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setMode("idea")}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    mode === "idea" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  <Sparkles className="size-3.5" />
                  From Idea
                </button>
                <button
                  type="button"
                  onClick={() => setMode("script")}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    mode === "script" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  <FileText className="size-3.5" />
                  From Script
                </button>
              </div>

              <Popover>
                <PopoverTrigger asChild>
                  <Button type="button" variant="outline" size="sm" className="h-8 gap-1.5 rounded-lg text-xs">
                    <SlidersHorizontal className="size-3.5" />
                    Advanced
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="end" className="w-80 space-y-3 p-4">
                  <div className="space-y-1">
                    <Label className="text-xs">Visual Provider</Label>
                    <Select value={imageProvider} onValueChange={setImageProvider}>
                      <SelectTrigger size="sm" className="w-full text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {imageProviders.map((p) => (
                          <SelectItem key={p} value={p} className="text-xs">{p}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-1">
                    <Label className="text-xs">Outro Profile</Label>
                    <Select value={outro} onValueChange={setOutro}>
                      <SelectTrigger size="sm" className="w-full text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {outroProfiles.map((p) => (
                          <SelectItem key={p} value={p} className="text-xs">{p}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs">BGM Volume</Label>
                      <span className="text-xs tabular-nums text-muted-foreground">{Math.round(bgmVolume * 100)}%</span>
                    </div>
                    <Slider
                      value={[bgmVolume]}
                      onValueChange={(values) => setBgmVolume(values[0] ?? 0.14)}
                      min={0}
                      max={0.5}
                      step={0.02}
                      aria-label="BGM Volume"
                    />
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <Label htmlFor="story-captions" className="text-xs">Generate Subtitles (.srt)</Label>
                    <Switch id="story-captions" checked={captions} onCheckedChange={setCaptions} />
                  </div>

                  <div className="space-y-1">
                    <Label htmlFor="story-theme" className="text-xs">Visual Theme (optional)</Label>
                    <Input
                      id="story-theme"
                      value={theme}
                      onChange={(e) => setTheme(e.target.value)}
                      placeholder="e.g. moody noir, warm documentary"
                      className="h-8 text-xs"
                    />
                  </div>

                  <div className="space-y-1">
                    <Label htmlFor="story-output" className="text-xs">Output Folder (optional)</Label>
                    <Input
                      id="story-output"
                      value={outputName}
                      onChange={(e) => setOutputName(e.target.value)}
                      placeholder="e.g. lighthouse-story"
                      className="h-8 text-xs"
                    />
                  </div>
                </PopoverContent>
              </Popover>
            </div>

            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={5}
              placeholder={
                mode === "idea"
                  ? "Describe your story idea (e.g., A mysterious lighthouse on a stormy night)..."
                  : "Paste your UTF-8 narration script..."
              }
              className="resize-none border-0 bg-transparent p-0 text-base shadow-none focus-visible:ring-0"
            />

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-3">
              <div className="flex flex-wrap items-center gap-2">
                <Select value={language} onValueChange={setLanguage}>
                  <SelectTrigger size="sm" aria-label="Language" className="rounded-lg text-xs font-medium">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {languages.map((lang) => (
                      <SelectItem key={lang} value={lang} className="text-xs">
                        {lang === "hi" ? "Hindi (hi)" : lang === "en" ? "English (en)" : lang}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={format} onValueChange={setFormat}>
                  <SelectTrigger size="sm" aria-label="Format" className="rounded-lg text-xs font-medium">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {formats.map((f) => (
                      <SelectItem key={f} value={f} className="text-xs">
                        {f === "shorts" ? "9:16 Shorts" : "16:9 Landscape"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={tts} onValueChange={setTts}>
                  <SelectTrigger size="sm" aria-label="TTS Provider" className="rounded-lg text-xs font-medium">
                    <Volume2 className="size-3.5 text-muted-foreground" />
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ttsProviders.map((provider) => (
                      <SelectItem key={provider} value={provider} className="text-xs">
                        {provider}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                {mode === "idea" ? (
                  <Select value={String(minutes)} onValueChange={(v) => setMinutes(parseFloat(v))}>
                    <SelectTrigger size="sm" aria-label="Duration" className="rounded-lg text-xs font-medium">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="0.5" className="text-xs">30 sec</SelectItem>
                      <SelectItem value="1" className="text-xs">1 min</SelectItem>
                      <SelectItem value="2" className="text-xs">2 min</SelectItem>
                      <SelectItem value="3" className="text-xs">3 min</SelectItem>
                      <SelectItem value="5" className="text-xs">5 min</SelectItem>
                    </SelectContent>
                  </Select>
                ) : null}
              </div>

              <Button type="button" onClick={handleGenerate} disabled={!prompt.trim() || submitting} className="rounded-xl">
                {submitting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
                Generate Video
              </Button>
            </div>
          </div>

          {recentProjects.length > 0 ? (
            <div className="mt-6">
              <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
                <span>Recent Story Projects</span>
                <Link href="/list" className="hover:text-foreground">View Library →</Link>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {recentProjects.map((project) => (
                  <Link
                    key={project.name}
                    href="/list"
                    className="flex items-center justify-between rounded-xl border bg-card/70 px-3 py-2.5 text-xs transition-colors hover:bg-card"
                  >
                    <span className="truncate font-medium">{project.title || project.name}</span>
                    <span className="ml-2 shrink-0 rounded-md bg-accent px-2 py-0.5 text-[10px] text-muted-foreground">
                      {project.language} · {project.scenes_count} scenes
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          ) : null}

          <div className="mt-6 flex items-center justify-center text-xs text-muted-foreground">
            <Link href="/clip" className="inline-flex items-center gap-1.5 hover:text-foreground">
              <Scissors className="size-3.5" />
              Need to cut a long video into shorts? Open Videos to Clip
            </Link>
          </div>
        </>
      )}
    </main>
  );
}
