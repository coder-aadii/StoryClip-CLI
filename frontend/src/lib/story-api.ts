"use client";

const STORY_API_BASE = "/api/story";

export interface StoryUser {
  id: string;
  username: string;
  role: string;
}

export interface StorySystemHealth {
  status: string;
  tools: { ffmpeg: boolean; ffprobe: boolean; xfade: boolean };
  api_keys: { gemini: boolean; pexels: boolean; cloudflare: boolean; jamendo: boolean };
  local_daemons: { voicestudio: boolean; indic_parler: boolean };
}

export interface StoryPipelineOptions {
  languages: string[];
  formats: string[];
  resolutions: string[];
  tts_providers: string[];
  image_providers: string[];
  outro_profiles: string[];
  builtin_bgm: string[];
}

export interface StoryProjectSummary {
  name: string;
  title: string;
  status: string;
  language: string;
  scenes_count: number;
  has_final_video: boolean;
  has_subtitles: boolean;
  updated_at: number;
}

export interface StoryJobStatus {
  id: string;
  project_slug: string;
  status: "pending" | "running" | "completed" | "failed" | "interrupted";
  progress: number;
  stage: string;
  logs: string[];
  created_at: string;
  updated_at: string;
}

export interface StoryRenderPayload {
  idea?: string;
  script?: string;
  language: string;
  minutes: number;
  tts: string;
  format: string;
  image_provider: string;
  outro: string;
  theme?: string;
  bgm_volume: number;
  captions: boolean;
  output_name?: string;
  gemini_model?: string;
}

export class StoryUnauthorizedError extends Error {
  constructor(message = "Authentication required") {
    super(message);
    this.name = "StoryUnauthorizedError";
  }
}

function getCsrfToken(): string | null {
  if (typeof document !== "undefined") {
    const match = document.cookie.match(/(^| )csrf_token=([^;]+)/);
    if (match) return match[2];
  }
  return typeof sessionStorage !== "undefined" ? sessionStorage.getItem("storyclip_csrf") : null;
}

function getAuthToken(): string | null {
  return typeof sessionStorage !== "undefined" ? sessionStorage.getItem("storyclip_token") : null;
}

function saveAuthTokens(token?: string, csrf?: string) {
  if (typeof sessionStorage === "undefined") return;
  if (token) sessionStorage.setItem("storyclip_token", token);
  if (csrf) sessionStorage.setItem("storyclip_csrf", csrf);
}

export function clearStoryTokens() {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem("storyclip_token");
  sessionStorage.removeItem("storyclip_csrf");
}

function authHeaders(json = true): Record<string, string> {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  const token = getAuthToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const csrf = getCsrfToken();
  if (csrf) headers["X-CSRF-Token"] = csrf;
  return headers;
}

async function readErrorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown; message?: string };
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.message === "string") return data.message;
  } catch {
    // Ignore non-JSON error bodies
  }
  return fallback;
}

export async function getStorySetupStatus(): Promise<{ needs_setup: boolean }> {
  const res = await fetch(`${STORY_API_BASE}/auth/setup-status`, { credentials: "include" });
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to check studio setup status"));
  return res.json();
}

export async function registerStoryOperator(username: string, password: string): Promise<StoryUser> {
  const res = await fetch(`${STORY_API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(await readErrorMessage(res, "Operator registration failed"));
  const data = (await res.json()) as { token?: string; csrf_token?: string; user: StoryUser };
  saveAuthTokens(data.token, data.csrf_token);
  return data.user;
}

export async function loginStory(username: string, password: string): Promise<StoryUser> {
  const res = await fetch(`${STORY_API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(await readErrorMessage(res, "Invalid username or password"));
  const data = (await res.json()) as { token?: string; csrf_token?: string; user: StoryUser };
  saveAuthTokens(data.token, data.csrf_token);
  return data.user;
}

export async function logoutStory(): Promise<void> {
  await fetch(`${STORY_API_BASE}/auth/logout`, {
    method: "POST",
    headers: authHeaders(false),
    credentials: "include",
  });
  clearStoryTokens();
}

export async function getStoryMe(): Promise<StoryUser> {
  const res = await fetch(`${STORY_API_BASE}/auth/me`, {
    headers: authHeaders(false),
    credentials: "include",
  });
  if (res.status === 401) throw new StoryUnauthorizedError();
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to load studio session"));
  const data = (await res.json()) as { user: StoryUser };
  return data.user;
}

export async function fetchStoryHealth(): Promise<StorySystemHealth> {
  const res = await fetch(`${STORY_API_BASE}/health`, { credentials: "include" });
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to fetch studio health"));
  return res.json();
}

export async function fetchStoryOptions(): Promise<StoryPipelineOptions> {
  const res = await fetch(`${STORY_API_BASE}/options`, { credentials: "include" });
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to load pipeline options"));
  return res.json();
}

export async function fetchStoryProjects(): Promise<StoryProjectSummary[]> {
  const res = await fetch(`${STORY_API_BASE}/projects`, {
    headers: authHeaders(false),
    credentials: "include",
  });
  if (res.status === 401) throw new StoryUnauthorizedError();
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to fetch Story Clip projects"));
  return res.json();
}

export async function triggerStoryRender(payload: StoryRenderPayload): Promise<{ status: string; job_id: string }> {
  const res = await fetch(`${STORY_API_BASE}/render`, {
    method: "POST",
    headers: authHeaders(true),
    credentials: "include",
    body: JSON.stringify(payload),
  });
  if (res.status === 401) throw new StoryUnauthorizedError();
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to start render job"));
  return res.json();
}

export async function getStoryJobStatus(jobId: string): Promise<StoryJobStatus> {
  const res = await fetch(`${STORY_API_BASE}/jobs/${encodeURIComponent(jobId)}`, {
    headers: authHeaders(false),
    credentials: "include",
  });
  if (res.status === 401) throw new StoryUnauthorizedError();
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to load render job status"));
  return res.json();
}

export async function stopStoryRender(jobId: string): Promise<void> {
  const res = await fetch(`${STORY_API_BASE}/jobs/${encodeURIComponent(jobId)}/stop`, {
    method: "POST",
    headers: authHeaders(false),
    credentials: "include",
  });
  if (res.status === 401) throw new StoryUnauthorizedError();
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to stop render job"));
}

export async function createStoryWsTicket(): Promise<string> {
  const res = await fetch(`${STORY_API_BASE}/auth/ws-ticket`, {
    method: "POST",
    headers: authHeaders(false),
    credentials: "include",
  });
  if (res.status === 401) throw new StoryUnauthorizedError();
  if (!res.ok) throw new Error(await readErrorMessage(res, "Failed to issue WebSocket ticket"));
  const data = (await res.json()) as { ticket: string };
  return data.ticket;
}

export function storyWsUrl(ticket: string, jobId: string): string {
  const base = (process.env.NEXT_PUBLIC_STUDIO_WS_URL?.trim() || "ws://127.0.0.1:8190").replace(/\/$/, "");
  return `${base}/ws/render?ticket=${encodeURIComponent(ticket)}&job_id=${encodeURIComponent(jobId)}`;
}

export function storyMediaUrl(projectName: string, filename: string): string {
  return `${STORY_API_BASE}/media/${encodeURIComponent(projectName)}/${encodeURIComponent(filename)}`;
}
