"""StoryClip Studio Backend Bridge (FastAPI).

Provides:
- Security: Localhost binding, explicit CORS whitelist, HttpOnly cookie auth, CSRF validation
- Crash-resilient render job persistence in SQLite (WAL mode)
- Staged execution: Script planning, asset querying, TTS auditioning
- Live video rendering with streaming WebSocket log and progress events via single-use tickets
"""
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.studio_api.db import (
    get_db,
    init_db,
    reconcile_interrupted_jobs,
)
from services.studio_api.auth import (
    clear_auth_cookies,
    consume_ws_ticket,
    get_current_user,
    hash_password,
    issue_ws_ticket,
    set_auth_cookies,
    verify_password,
)
from video_cli import (
    BUILTIN_BGM_FILES,
    OUTRO_PROFILES,
    VIDEO_RESOLUTIONS,
    available_output_path,
    source_identity,
    narrative_from_idea,
    build_video_scenes,
    build_avatar_plan,
)

app = FastAPI(title="StoryClip Studio API", version="2.0.0")

# Security: Explicit CORS whitelist matching client dev and local ports
CORS_ORIGINS = [
    "http://localhost:3190",
    "http://127.0.0.1:3190",
    "http://localhost:3107",
    "http://127.0.0.1:3107",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8190",
    "http://127.0.0.1:8190",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """Initialize database and reconcile orphaned jobs after reboot."""
    init_db()
    interrupted = reconcile_interrupted_jobs()
    if interrupted > 0:
        print(f"[StoryClip API] Startup: Reconciled {interrupted} interrupted render job(s).")


# Active WebSocket connections by job ID
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, job_id: str, websocket: WebSocket):
        await websocket.accept()
        if job_id not in self.active_connections:
            self.active_connections[job_id] = []
        self.active_connections[job_id].append(websocket)

    def disconnect(self, job_id: str, websocket: WebSocket):
        if job_id in self.active_connections:
            if websocket in self.active_connections[job_id]:
                self.active_connections[job_id].remove(websocket)
            if not self.active_connections[job_id]:
                del self.active_connections[job_id]

    async def broadcast_job(self, job_id: str, message: dict):
        if job_id in self.active_connections:
            for conn in list(self.active_connections[job_id]):
                try:
                    await conn.send_json(message)
                except Exception:
                    self.disconnect(job_id, conn)

manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    username: str
    password: str


class PlanIdeaRequest(BaseModel):
    idea: str = Field(..., description="Prompt idea for the story")
    language: str = Field("hi", description="Language: hi or en")
    minutes: float = Field(1.0, description="Target duration in minutes")
    outro_profile: str = Field("shorts", description="Outro profile: default, facts-sun-le-bhai, shorts")
    is_shorts: bool = Field(True, description="Whether 9:16 vertical shorts layout")
    theme: Optional[str] = Field("cinematic nature landscape", description="Theme search override")
    image_provider: str = Field("pexels-video", description="Visual asset provider")
    tts_provider: str = Field("edge-male-energetic", description="TTS voice engine")
    gemini_model: Optional[str] = Field(None, description="Gemini text model")


class RenderRequest(BaseModel):
    idea: Optional[str] = None
    script: Optional[str] = None
    language: str = "hi"
    minutes: float = 1.0
    tts: str = "edge-male-energetic"
    format: str = "shorts"
    image_provider: str = "pexels-video"
    outro: str = "shorts"
    theme: Optional[str] = None
    bgm_volume: float = 0.09
    captions: bool = True
    output_name: Optional[str] = None
    gemini_model: Optional[str] = None


# ---------------------------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/auth/setup-status")
def get_setup_status():
    """Check if any operator account exists; if none, allow first-time registration."""
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"needs_setup": count == 0}


@app.post("/api/auth/register")
def register_operator(req: RegisterRequest, response: Response):
    """Register initial administrator or add operator."""
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        # Only allow unauthenticated registration if database is empty
        if count > 0:
            raise HTTPException(status_code=403, detail="Registration closed. An administrator already exists.")

        user_id = str(uuid.uuid4())
        hashed = hash_password(req.password)
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, 'admin')",
            (user_id, req.username, hashed),
        )
        conn.commit()

    token, csrf_token = set_auth_cookies(response, user_id, req.username, "admin")
    return {
        "status": "ok",
        "token": token,
        "csrf_token": csrf_token,
        "user": {"id": user_id, "username": req.username, "role": "admin"},
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    """Authenticate with username and password, setting HttpOnly session and CSRF cookies."""
    with get_db() as conn:
        user = conn.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?",
                            (req.username,)).fetchone()
        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    token, csrf_token = set_auth_cookies(response, user["id"], user["username"], user["role"])
    return {
        "status": "ok",
        "token": token,
        "csrf_token": csrf_token,
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    }


@app.post("/api/auth/logout")
def logout(response: Response):
    """Clear session and CSRF cookies."""
    clear_auth_cookies(response)
    return {"status": "ok", "message": "Logged out successfully"}


@app.get("/api/auth/me")
def get_current_user_profile(user: dict = Depends(get_current_user)):
    """Return authenticated user profile."""
    return {"user": user}


@app.post("/api/auth/ws-ticket")
def create_websocket_ticket(user: dict = Depends(get_current_user)):
    """Issue a 30-second single-use ticket for authenticating WebSockets without tokens in URLs."""
    ticket = issue_ws_ticket(user["id"])
    return {"ticket": ticket}


# ---------------------------------------------------------------------------
# Diagnostics & Discovery Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health_check():
    """Verify system tools, API keys, and local TTS daemons."""
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    xfade_ok = False
    if ffmpeg_ok:
        try:
            res = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=2)
            xfade_ok = "xfade" in res.stdout
        except Exception:
            pass

    voicestudio_ok = False
    try:
        from voicestudio_provider import check_voicestudio_health
        health = check_voicestudio_health(timeout=1)
        voicestudio_ok = health.get("status") == "healthy"
    except Exception:
        pass

    indic_ok = False
    try:
        import requests
        r = requests.get(os.getenv("INDIC_PARLER_URL", "http://127.0.0.1:8765") + "/health", timeout=1)
        indic_ok = r.status_code == 200
    except Exception:
        pass

    return {
        "status": "healthy",
        "tools": {"ffmpeg": ffmpeg_ok, "ffprobe": ffprobe_ok, "xfade": xfade_ok},
        "api_keys": {
            "gemini": bool(os.getenv("GEMINI_API_KEY")),
            "pexels": bool(os.getenv("PEXELS_API_KEY")),
            "cloudflare": bool(os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN")),
            "jamendo": bool(os.getenv("JAMENDO_CLIENT_ID")),
        },
        "local_daemons": {"voicestudio": voicestudio_ok, "indic_parler": indic_ok},
    }


@app.get("/api/options")
def get_pipeline_options():
    """Available pipeline presets and voice models."""
    return {
        "languages": ["hi", "en"],
        "formats": ["shorts", "landscape"],
        "resolutions": list(VIDEO_RESOLUTIONS.keys()),
        "tts_providers": [
            "edge-male-energetic",
            "edge-female",
            "gemini",
            "indic-parler",
            "voicestudio",
        ],
        "image_providers": ["pexels-video", "pexels", "cloudflare-flux"],
        "outro_profiles": list(OUTRO_PROFILES),
        "builtin_bgm": list(BUILTIN_BGM_FILES.keys()),
    }


# ---------------------------------------------------------------------------
# Project & Media Endpoints (Authenticated)
# ---------------------------------------------------------------------------
@app.get("/api/projects")
def list_projects(user: dict = Depends(get_current_user)):
    """List all projects in the outputs directory."""
    outputs_dir = PROJECT_ROOT / "outputs"
    if not outputs_dir.exists():
        return []

    projects = []
    for p in sorted(outputs_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() and (p / "project.json").exists():
            try:
                meta = json.loads((p / "project.json").read_text(encoding="utf-8"))
                has_video = (p / "final.mp4").is_file() and (p / "final.mp4").stat().st_size > 0
                has_srt = (p / "subtitles.srt").is_file()
                projects.append({
                    "name": p.name,
                    "title": meta.get("title", p.name),
                    "status": meta.get("status", "unknown"),
                    "language": meta.get("language", "hi"),
                    "scenes_count": len(meta.get("scenes", [])),
                    "has_final_video": has_video,
                    "has_subtitles": has_srt,
                    "updated_at": p.stat().st_mtime,
                })
            except Exception:
                continue
    return projects


@app.get("/api/projects/{project_name}")
def get_project_details(project_name: str, user: dict = Depends(get_current_user)):
    """Fetch complete project manifest and storyboard details for editing or viewing."""
    target = (PROJECT_ROOT / "outputs" / project_name / "project.json").resolve()
    if not str(target).startswith(str((PROJECT_ROOT / "outputs").resolve())):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Project not found")
    return json.loads(target.read_text(encoding="utf-8"))


@app.get("/api/media/{project_name}/{filename}")
def serve_project_media(project_name: str, filename: str, user: dict = Depends(get_current_user)):
    """Serve media files (final.mp4, images, clips, subtitles) with path traversal protection."""
    target = (PROJECT_ROOT / "outputs" / project_name / filename).resolve()
    if not str(target).startswith(str((PROJECT_ROOT / "outputs").resolve())):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "video/mp4" if filename.endswith(".mp4") else (
        "text/plain" if filename.endswith(".srt") or filename.endswith(".txt") else None
    )
    return FileResponse(target, media_type=media_type)


# ---------------------------------------------------------------------------
# Story Planning & Render Jobs (Authenticated)
# ---------------------------------------------------------------------------
@app.post("/api/plan/idea")
def plan_from_idea(req: PlanIdeaRequest, user: dict = Depends(get_current_user)):
    """Plan a story concept and structure scenes without downloading or rendering."""
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY is missing in .env")

    # Use requested Gemini model with fallback handling
    model = req.gemini_model or os.getenv("GEMINI_TEXT_MODEL", "gemini-3.8-flash")
    os.environ["GEMINI_TEXT_MODEL"] = model

    plan = None
    last_err = None
    # If 503 high demand or 404 sunset occurs, attempt fallback models in priority order
    candidate_models = [model]
    for fallback in ["gemini-3.8-flash", "gemini-3-flash-preview", "gemini-2.5-flash"]:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    for m in candidate_models:
        os.environ["GEMINI_TEXT_MODEL"] = m
        try:
            plan = narrative_from_idea(
                idea=req.idea,
                language=req.language,
                minutes=req.minutes,
                outro_profile=req.outro_profile,
                is_shorts=req.is_shorts,
            )
            break
        except Exception as e:
            last_err = e
            err_str = str(e)
            if any(k in err_str for k in ("503", "404", "UNAVAILABLE", "NOT_FOUND", "high demand", "no longer available")):
                continue
            raise HTTPException(status_code=500, detail=f"Planning failed: {str(e)}")

    if not plan:
        raise HTTPException(
            status_code=503,
            detail=f"Gemini API is temporarily experiencing high demand across candidate models. Please try again in a few moments: {last_err}",
        )

    try:
        scenes, intro, outro, video_narration = build_video_scenes(
            content=plan["narration"],
            title=plan.get("title"),
            language=req.language,
            theme=req.theme or "cinematic landscape",
            outro_profile=req.outro_profile,
            tts_provider=req.tts_provider,
            image_provider=req.image_provider,
            is_shorts=req.is_shorts,
        )
        avatar_plan = build_avatar_plan(scenes)

        slug = plan.get("output_slug") or f"storyclip_{int(time.time())}"
        out_dir = PROJECT_ROOT / "outputs" / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        identity = source_identity(
            kind="idea",
            text=req.idea,
            language=req.language,
            minutes=req.minutes,
            theme=req.theme,
            outro_profile=req.outro_profile,
            title=plan.get("title"),
        )

        manifest_data = {
            "format_version": 2,
            "title": plan.get("title", slug),
            "slug": slug,
            "language": req.language,
            "status": "planned",
            "source": identity,
            "outro_profile": req.outro_profile,
            "is_shorts": req.is_shorts,
            "narration": plan["narration"],
            "intro_narration": intro,
            "outro_narration": outro,
            "scenes": scenes,
            "avatar_plan": avatar_plan,
            "tts_provider": req.tts_provider,
            "image_provider": req.image_provider,
            "updated_at": time.time(),
        }
        (out_dir / "project.json").write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "script.txt").write_text(plan["narration"], encoding="utf-8")

        return {
            "title": plan.get("title"),
            "slug": slug,
            "intro_narration": intro,
            "content_narration": plan["narration"],
            "outro_narration": outro,
            "scenes": scenes,
            "avatar_plan": avatar_plan,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scene planning failed: {str(e)}")


async def run_render_process(job_id: str, cmd: list, env: dict):
    """Asynchronous background execution of the CLI render pipeline.
    Runs with 'nice -n 10' priority clamping so video renders do not starve the host.
    """
    exec_cmd = ["nice", "-n", "10", *cmd] if shutil.which("nice") else cmd

    proc = await asyncio.create_subprocess_exec(
        *exec_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=str(PROJECT_ROOT),
    )

    # Persist process PID
    with get_db() as conn:
        conn.execute("UPDATE render_jobs SET pid = ?, status = 'running' WHERE id = ?", (proc.pid, job_id))
        conn.commit()

    logs = []
    progress = 2.0
    stage = "Planning Script & Imagery"
    last_persisted_progress = 0.0
    last_persisted_stage = ""

    STAGE_RANGES = {
        "Planning Script & Imagery": (2.0, 10.0),
        "Gathering Visuals": (10.0, 40.0),
        "Synthesizing Speech": (40.0, 65.0),
        "Rendering Scene Clips": (65.0, 90.0),
        "Assembling Video Transitions": (90.0, 96.0),
        "Finalizing Output": (96.0, 99.0),
    }

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            logs.append(text)
            if len(logs) > 300:
                logs.pop(0)

            sub_pct: Optional[float] = None
            if "%" in text:
                try:
                    part = text.split("%")[0].split()[-1]
                    sub_pct = min(100.0, max(0.0, float(part)))
                except Exception:
                    pass

            if "Generating narration script" in text or "Planning scene" in text:
                stage = "Planning Script & Imagery"
            elif any(k in text for k in ("Downloading", "Search", "Generating scene", "doodle via", "FLUX", "Images [", "Videos [")):
                stage = "Gathering Visuals"
            elif any(k in text for k in ("TTS scene", "Voice [", "Narrating", "synthesize")):
                stage = "Synthesizing Speech"
            elif "Rendering scene" in text or "Render [" in text:
                stage = "Rendering Scene Clips"
            elif "Crossfade" in text:
                stage = "Assembling Video Transitions"
            elif "VIDEO:" in text:
                stage = "Finalizing Output"

            low, high = STAGE_RANGES.get(stage, (0.0, 100.0))
            if sub_pct is not None:
                computed = low + (high - low) * (sub_pct / 100.0)
            else:
                computed = max(progress, low)
            progress = round(min(99.0, max(progress, computed)), 1)

            if stage != last_persisted_stage or progress - last_persisted_progress >= 2.0:
                last_persisted_stage = stage
                last_persisted_progress = progress
                with get_db() as conn:
                    conn.execute(
                        "UPDATE render_jobs SET stage = ?, progress = ?, logs = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (stage, progress, json.dumps(logs), job_id),
                    )
                    conn.commit()

            # Broadcast progress to listening WebSockets
            await manager.broadcast_job(job_id, {
                "type": "progress",
                "data": {"progress": progress, "stage": stage, "log": text}
            })

    rc = await proc.wait()
    final_status = "completed" if rc == 0 else "failed"
    final_stage = "Complete" if rc == 0 else f"Failed (exit code {rc})"

    with get_db() as conn:
        conn.execute("""
            UPDATE render_jobs
            SET status = ?, stage = ?, progress = ?, logs = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (final_status, final_stage, 100.0 if rc == 0 else progress, json.dumps(logs), job_id))
        conn.commit()

    await manager.broadcast_job(job_id, {
        "type": "job_finished",
        "data": {"status": final_status, "stage": final_stage, "progress": 100.0 if rc == 0 else progress}
    })


@app.post("/api/render")
async def trigger_render(req: RenderRequest, user: dict = Depends(get_current_user)):
    """Dispatch video render job with persistent tracking in SQLite."""
    with get_db() as conn:
        # Check if a render job is currently running
        active = conn.execute("SELECT id FROM render_jobs WHERE status = 'running'").fetchone()
        if active:
            raise HTTPException(status_code=409, detail="A render job is already running on the server.")

    job_id = str(uuid.uuid4())
    slug = req.output_name or f"storyclip_{int(time.time())}"

    # Build command list
    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        str(PROJECT_ROOT / "video_cli.py"),
        "--keep-intermediates",
        "--language", req.language,
        "--tts", req.tts,
        "--format", req.format,
        "--image-provider", req.image_provider,
        "--outro", req.outro,
        "--bgm-volume", str(req.bgm_volume),
    ]

    if req.idea:
        cmd.extend(["--idea", req.idea, "--minutes", str(req.minutes)])
    elif req.script:
        temp_script = PROJECT_ROOT / "outputs" / "temp_script.txt"
        temp_script.parent.mkdir(parents=True, exist_ok=True)
        temp_script.write_text(req.script, encoding="utf-8")
        cmd.extend(["--script", str(temp_script)])

    if req.theme:
        cmd.extend(["--theme", req.theme])

    # Validate output directory against existing project.json to prevent outro/language/source conflicts
    outputs_base = PROJECT_ROOT / "outputs"
    target_dir = outputs_base / slug
    if target_dir.exists() and (target_dir / "project.json").exists():
        try:
            existing_meta = json.loads((target_dir / "project.json").read_text(encoding="utf-8"))
            expected_identity = source_identity(
                kind="idea" if req.idea else "script",
                text=req.idea or req.script or "",
                language=req.language,
                minutes=req.minutes if req.idea else None,
                theme=req.theme,
                outro_profile=req.outro,
            )
            # If the source identity, outro profile, or language differs:
            if (
                existing_meta.get("source") != expected_identity
                or existing_meta.get("outro_profile") != req.outro
                or existing_meta.get("language") != req.language
            ):
                target_dir = available_output_path(outputs_base, slug)
                slug = target_dir.name
        except Exception:
            pass

    cmd.extend(["--output", str(target_dir)])
    if not req.captions:
        cmd.append("--no-captions")

    env = os.environ.copy()
    if req.gemini_model:
        env["GEMINI_TEXT_MODEL"] = req.gemini_model
    elif not env.get("GEMINI_TEXT_MODEL"):
        env["GEMINI_TEXT_MODEL"] = "gemini-3.8-flash"

    local_bin = str(PROJECT_ROOT / ".local-bin")
    if Path(local_bin).exists():
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

    # Record initial job state
    with get_db() as conn:
        conn.execute("""
            INSERT INTO render_jobs (id, user_id, project_slug, status, progress, stage, command_args)
            VALUES (?, ?, ?, 'pending', 0.0, 'Queued', ?)
        """, (job_id, user["id"], slug, json.dumps(cmd)))
        conn.commit()

    asyncio.create_task(run_render_process(job_id, cmd, env))
    return {"status": "started", "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str, user: dict = Depends(get_current_user)):
    """Retrieve current state and logs of a render job."""
    with get_db() as conn:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job["id"],
        "project_slug": job["project_slug"],
        "status": job["status"],
        "progress": job["progress"],
        "stage": job["stage"],
        "logs": json.loads(job["logs"]),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str, user: dict = Depends(get_current_user)):
    """Terminate an active render process safely."""
    with get_db() as conn:
        job = conn.execute("SELECT pid, status FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] not in ("running", "pending"):
            return {"status": "ok", "message": f"Job is already {job['status']}"}

        pid = job["pid"]
        conn.execute(
            "UPDATE render_jobs SET status = 'interrupted', stage = 'Stopped by user', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job_id,),
        )
        conn.commit()

    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    await manager.broadcast_job(job_id, {
        "type": "job_finished",
        "data": {"status": "interrupted", "stage": "Stopped by user"}
    })
    return {"status": "ok", "message": "Render job stopped"}


# ---------------------------------------------------------------------------
# Ticket-Authenticated WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws/render")
async def websocket_render_endpoint(websocket: WebSocket, ticket: Optional[str] = None, job_id: Optional[str] = "global"):
    """WebSocket endpoint protected by single-use tickets."""
    if not ticket:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = consume_ws_ticket(ticket)
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(job_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(job_id, websocket)


if __name__ == "__main__":
    import uvicorn
    # Security: Bind to loopback 127.0.0.1 in development/production
    uvicorn.run(app, host="127.0.0.1", port=8190)
