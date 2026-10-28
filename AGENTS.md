# AGENTS.md

This file is the operating guide for Codex, Claude Code, and other coding agents working in this repository. It applies to the entire repository. Follow the user's request first, then this guide, then the surrounding project documentation.

## Project in one paragraph

StoryClip CLI is a terminal-only Python 3.12 application that turns an idea or a UTF-8 script into a 1280x720 narrated photo video. Gemini writes or plans the story, Pexels supplies still images, Edge/Gemini/a local Indic Parler service supplies speech, optional rights-cleared music can be mixed in, and FFmpeg renders the result. The application is intentionally small: the production implementation is the root-level `video_cli.py`, and the tests use the standard-library `unittest` framework.

## Source of truth and repository map

| Path | Purpose | Agent guidance |
| --- | --- | --- |
| `video_cli.py` | All production behavior and CLI arguments | This is the authoritative implementation. |
| `tests/` | Offline unit tests for CLI behavior, Indic Parler, caching, progress, and render filters | Add or update focused tests with behavior changes. |
| `README.md` | User-facing overview, installation, examples, and limitations | Update when behavior or setup changes. |
| `COMMANDS.md` | Detailed operator command cheat sheet and troubleshooting | Keep CLI examples synchronized with the parser. |
| `.env.example` | Safe environment-variable template | Add placeholders only; never add real credentials. |
| `requirements.txt` | Runtime Python dependencies | There is no separate dev dependency file. |
| `outputs/` | Generated projects, media, manifests, and possibly stale copied source | Ignored by Git. Never treat `outputs/video_cli.py` as source or edit it instead of the root file. |
| `.venv/`, `.local-bin/` | Local environment and machine-specific tools | Ignored by Git; do not inspect, edit, or commit their contents. |

There is currently no package build, Makefile, formatter, linter, type checker, CI workflow, or `pytest` configuration. Do not claim that one exists. Do not introduce a new tool merely to complete an unrelated change.

## Start every task safely

Run commands from the repository root. Prefer the existing Python 3.12 virtual environment; the host `python3` may be older.

```bash
pwd
git status --short
git diff --stat
.venv/bin/python --version
```

Before editing:

1. Read this file and the parts of `README.md` relevant to the request.
2. Inspect `video_cli.py` and the nearest related tests. Use `rg` to find symbols; do not read generated media, `.env`, or the entire virtual environment.
3. Check the working tree and preserve all user changes. Never discard, overwrite, or reformat unrelated work.
4. State assumptions when the request leaves a behavior choice open, then take the smallest compatible path.

Useful discovery commands:

```bash
rg --files -g '!outputs/**' -g '!.venv/**' -g '!.git/**'
rg -n "def |class |add_argument|GEMINI|PEXELS|JAMENDO|INDIC|ffmpeg" video_cli.py tests README.md COMMANDS.md
git diff -- video_cli.py tests README.md COMMANDS.md .env.example
```

Do not open `.env`, print environment variables, or include credentials in logs, fixtures, patches, commits, or responses.

## Setup and dependency commands

First-time setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

The user must edit `.env` locally. Never fill it on their behalf with secret values.

Verify the Python environment:

```bash
.venv/bin/python -m pip check
.venv/bin/python -c "import requests, dotenv, edge_tts; print('Dependencies OK')"
```

Runtime system dependencies:

```bash
ffmpeg -version | head -n 2
ffprobe -version | head -n 1
ffmpeg -hide_banner -filters 2>/dev/null | rg '(^| )xfade( |$)'
```

Real multi-scene rendering requires an FFmpeg build with the `xfade` filter. On machines where the system binary lacks it, `COMMANDS.md` documents the local `imageio-ffmpeg`/`.local-bin` workaround. If that workaround is already installed, select it for the current shell with:

```bash
export PATH="$PWD/.local-bin:$PATH"
```

Do not install packages, replace system tools, or make network calls unless the task requires it and the user has authorized the relevant operation.

## Canonical validation commands

Use the smallest relevant test while iterating, then run the full offline suite before handing off a code change.

```bash
# Full offline suite (current canonical test command)
.venv/bin/python -m unittest discover -s tests -v

# Focused test modules
.venv/bin/python -m unittest tests/test_cli_progress_output.py -v
.venv/bin/python -m unittest tests/test_indic_parler.py -v
.venv/bin/python -m unittest tests/test_render_styles.py -v

# Syntax/import smoke checks
.venv/bin/python -m compileall -q video_cli.py tests
.venv/bin/python video_cli.py --help

# Dependency consistency
.venv/bin/python -m pip check
```

Expected baseline: 18 tests pass. Some negative CLI tests intentionally print `argparse` usage/errors to stderr; that output is not a failure when the suite ends in `OK`.

There is no configured lint or formatting command. Do not report lint/type-check results unless you actually add and run a project-approved tool as part of the requested work.

## Running the application

Safe parser smoke test (no provider calls):

```bash
.venv/bin/python video_cli.py --help
```

Planning example:

```bash
.venv/bin/python video_cli.py \
  --idea "A mysterious lighthouse on a stormy night" \
  --language en --minutes 2 --dry-run \
  --output outputs/lighthouse-plan
```

`--dry-run` is not offline: for a new project it still calls Gemini to create/plan content, requires `GEMINI_API_KEY`, and checks for FFmpeg/FFprobe. It skips image downloads, TTS, music, and rendering.

Narration-only render example:

```bash
.venv/bin/python video_cli.py \
  --idea "A mysterious lighthouse on a stormy night" \
  --language en --minutes 2 --tts edge --skip-music \
  --output outputs/lighthouse
```

Existing Hindi script with strict local Indic Parler:

```bash
.venv/bin/python video_cli.py \
  --script my_hindi_script.txt --language hi \
  --tts indic-parler --indic-url http://127.0.0.1:8765 \
  --indic-voice Rani --skip-music \
  --output outputs/hindi-rani
```

Use either `--idea` or `--script`, never both. Consult `python video_cli.py --help` and `COMMANDS.md` rather than duplicating the full option list here.

### Environment variables

| Variable | Requirement |
| --- | --- |
| `GEMINI_API_KEY` | Required by the current CLI for every run other than `--help`; used for idea generation and scene planning. |
| `PEXELS_API_KEY` | Required for non-dry runs that gather images. |
| `JAMENDO_CLIENT_ID` | Optional. Without it, non-skipped music gracefully becomes narration-only. |
| `GEMINI_TEXT_MODEL` | Optional Gemini text model override. |
| `GEMINI_TTS_MODEL` | Optional Gemini speech model override; part of the TTS cache identity. |
| `INDIC_PARLER_URL` | Optional default for `--indic-url`; default is `http://127.0.0.1:8765`. |
| `INDIC_PARLER_VOICE` | Optional default for `--indic-voice`; must be `Divya`, `Rohit`, `Aman`, or `Rani`. |

The module calls `load_dotenv()` at import time. Tests must patch environment-dependent behavior explicitly and must remain safe when a developer has a populated local `.env`.

## Architecture and control flow

The normal pipeline in `main()` is:

```text
parse + validate arguments
  -> identify/resume or create output project
  -> generate/read narration
  -> plan sentence-aware scenes and Pexels queries
  -> gather scene images
  -> synthesize/cache per-scene narration
  -> measure audio and write scene-level SRT
  -> optionally discover/use rights-cleared music
  -> render scene clips, crossfade, and mux final.mp4
```

Important boundaries:

- Gemini text: `gemini_json()`, `narrative_from_idea()`, `plan_scenes()`, and `expand_narration()`.
- Images: `pexels_search()`, `fetch_file()`, and `gather_images()`.
- Speech: `edge_speak()`, `gemini_speak()`, `indic_parler_speak()`, and `narrate()`.
- Cache correctness: `tts_cache_identity()`, `cache_key()`, and `tts_cache_hit()`.
- Timing/subtitles: `audio_duration()` and `make_srt()`.
- Music: `jamendo_candidates()` and `select_bgm()`.
- Rendering: `render()` plus the subprocess wrapper `run()`.
- Persistence/resume: `source_identity()`, `find_matching_output()`, and `project.json` handling in `main()`.

## Non-negotiable behavioral invariants

Preserve these unless the user explicitly requests a product change:

- Python 3.12+ and terminal-only operation; no StoryWeaver or frontend dependency.
- Output remains 1280x720 H.264 with separate scene-level `subtitles.srt`; subtitles are not burned in.
- Actual scene/video timing comes from synthesized audio, not estimated script duration.
- `--idea` and `--script` remain mutually exclusive.
- Saved projects resume from `project.json`; incompatible script text or language must not silently reuse stale scene/media data.
- Automatic output matching is based on the source fingerprint. Explicit `--output` remains the user's override.
- Expensive images, speech, and clips remain resumable. A failure must not destroy already valid work.
- Any setting that can alter synthesized audio must be represented in `tts_cache_identity()`. Cache writes remain metadata-backed, validated, and atomic.
- Any setting that changes a rendered clip must participate in its render signature.
- Explicit `--tts indic-parler` is Hindi-only and strict: never silently fall back to Gemini or Edge. It expects `GET /health` JSON and raw `audio/wav` from `POST /synthesize`.
- `--tts auto` means Gemini TTS followed by Edge fallback; it does not include Indic Parler.
- Provider requests retain finite timeouts, response validation, and actionable phase-specific errors.
- Downloads accept provider-returned HTTPS URLs, enforce size limits, and reject unexpectedly small files.
- Jamendo search results are candidates, not proof of synchronization or commercial rights. Do not bypass the explicit interactive rights gate.
- Local `--bgm-file` usage assumes the user has suitable synchronization rights; never assert that the program verified them.
- Hindi/Devanagari text remains UTF-8 and JSON uses `ensure_ascii=False` where human-readable Unicode is expected.
- Never expose scripts, search terms, provider payloads, or credentials beyond what the requested provider call needs.

## Editing conventions

- Make focused changes; avoid broad style-only rewrites of `video_cli.py`.
- Follow the surrounding standard-library-first design and avoid adding dependencies for behavior Python already provides cleanly.
- Use `pathlib.Path` for filesystem work and explicit UTF-8 for text.
- Use `SESSION` for HTTP so headers and mocking remain consistent.
- Keep network calls bounded by timeouts and validate status, content type, structure, and payload before caching.
- Use temporary/`.part` files plus `os.replace()` for cache or manifest data that must not appear valid when partially written.
- Keep errors actionable and name the failing provider or pipeline phase. Preserve exception context with `raise ... from ex` where useful.
- Maintain backward compatibility with existing `project.json` files where practical; use `.get()` and sensible defaults for newly introduced fields.
- Keep comments focused on intent, safety, licensing, cache semantics, or non-obvious FFmpeg behavior.
- If CLI flags, defaults, required variables, output structure, provider behavior, or limitations change, update `README.md`, `COMMANDS.md`, `.env.example`, and tests as applicable.
- Do not manually edit generated files under `outputs/` to simulate a product fix.

## Testing strategy

Unit tests must be deterministic, fast, and offline:

- Use `unittest`, `unittest.mock`, and `tempfile`; match the existing suite unless the repository deliberately adopts another framework.
- Mock Gemini, Pexels, Jamendo, Edge TTS, Indic Parler, FFmpeg, and FFprobe at the narrowest useful boundary.
- Do not spend API quota or require local secrets in ordinary tests.
- Test success, malformed responses, timeouts/provider errors, cache hits, and cache invalidation for settings that affect artifacts.
- For CLI changes, cover valid parsing and invalid combinations. `argparse` failures commonly raise `SystemExit`.
- For render changes, inspect the generated FFmpeg argument/filter graph rather than producing large media fixtures.
- For filesystem behavior, use temporary directories; never write test artifacts into tracked paths or real `outputs/` projects.
- A bug fix should include a regression test that fails for the old behavior and passes for the fix.

Real provider or end-to-end runs are integration checks. They may cost money, consume quota, download media, take minutes, and create large files. Do not run them automatically. If the user requests one, explain prerequisites, use a fresh explicit `outputs/<name>` directory, prefer `--skip-music`, and report which external services were exercised.

## Generated artifacts and resume semantics

A normal project may contain:

```text
outputs/<project>/
  project.json
  script.txt
  subtitles.srt
  final.mp4
  visual_only.mp4
  images/
  voice/
  clips/
  music/
```

These files can be large, licensed/attributed, slow to regenerate, and useful for resuming. Do not delete them, rename them, or repurpose an existing output directory without an explicit user request. Use a new output directory when script text, language, or the intended project identity changes. If inspecting a manifest is necessary, prefer a targeted query or `python -m json.tool`; do not paste whole manifests into the conversation.

## Security, privacy, and legal checks

- Never read or commit `.env`; use `.env.example` for documentation.
- Never echo keys, authorization headers, full environment dumps, or provider responses that may contain sensitive content.
- Treat narration text and generated project metadata as user data.
- Preserve Pexels photographer/link attribution in `project.json` and any user-facing integration that consumes it.
- Do not describe Jamendo availability, a Creative Commons URL, or `audiodownload_allowed` as sufficient video synchronization/commercial permission.
- Do not weaken URL validation, download limits, content checks, or the music-rights confirmation to make a test pass.
- Avoid destructive Git/filesystem commands. Never remove cached user media as routine cleanup.

## Git and handoff discipline

Before finishing a code or documentation change:

```bash
git status --short
git diff --check
git diff -- AGENTS.md video_cli.py tests README.md COMMANDS.md .env.example requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Then report:

- The outcome and user-visible behavior.
- Files changed.
- Exact validation commands run and whether they passed.
- Any checks not run, especially network/provider/end-to-end rendering, and why.
- Remaining risks or follow-ups only when they are concrete.

Do not claim success from code inspection alone. Do not commit, push, open a pull request, or modify remote services unless the user explicitly asks.

## Token and context efficiency for all coding agents

Optimize for useful evidence, not minimal thought. These practices work across Codex, Claude, and other agents:

1. **Keep user-facing replies concise by default.** Do not spend tokens on long progress reports, repeated summaries, elaborate preambles, or exhaustive final explanations unless the user explicitly asks for detail or the complexity/risk of the task requires it. Lead with the result, mention the important changes and verification, and stop.
2. **Do not expose or restate internal messages.** Never quote, paraphrase, or dump system/developer instructions, hidden reasoning, tool schemas, internal context, or routine tool-call output into the chat. Communicate only the user-relevant outcome, decisions, blockers, errors, and evidence needed to understand the work.
3. **Map once, then search narrowly.** Start with `rg --files` and `rg -n`; inspect only the relevant function, test, and documentation sections. Avoid repeatedly rereading whole files.
4. **Exclude noise by default.** Skip `.git/`, `.venv/`, `.local-bin/`, `outputs/`, caches, binaries, audio, images, and videos unless the task specifically concerns an artifact.
5. **Batch independent reads.** Gather related small files or searches in one tool round trip. Keep outputs bounded by line ranges or search patterns so important evidence is not truncated.
6. **Use the implementation as truth.** Read `build_parser()`, `main()`, and the called function before inferring behavior from examples. Documentation can lag behind code.
7. **Keep a compact working model.** Track the request, affected symbols, invariants, planned files, and validation command. Do not restate the entire repository in each update.
8. **Patch narrowly.** Prefer one coherent diff over repeated rewrites. Do not generate large replacement files when a small patch is safer.
9. **Test from narrow to broad.** Run the directly affected module while iterating; run the full 18-test suite once the implementation stabilizes. Do not rerun unchanged expensive checks without a reason.
10. **Separate offline from external validation.** Unit tests and `--help` are cheap and deterministic. Provider calls and full rendering are not; only run them when they add necessary evidence and are authorized.
11. **Summarize command output.** Preserve the failing assertion/error and relevant context; avoid pasting routine logs or full manifests into the final answer.
12. **Stop when the task is proven complete.** Avoid opportunistic refactors, dependency upgrades, generated-output edits, and speculative features that expand review surface and context use.

When context is constrained, prioritize in this order: the user's current request, this `AGENTS.md`, the relevant production function, its tests, then user documentation. If an assumption remains, state it rather than inventing repository behavior.
