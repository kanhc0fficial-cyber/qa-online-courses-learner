# Bilibili interactive course (portable Windows package)

This repository is the minimal local workflow for downloading a Bilibili video,
creating an auditable course record, and serving an interactive lesson locally.

It deliberately excludes downloaded videos, generated records, model caches,
login state, and API keys. Those are recreated on each machine.

## First-time setup

1. Install Python 3.11+ and make `ffmpeg` available on `PATH`.
2. Run `./setup.ps1` from PowerShell.
3. Copy `.env.example` to `.env` and set the two MiMo values. The provided
   entry scripts load this file only into their own PowerShell process.
4. Run `.\\.venv\\Scripts\\yutto.exe auth login` and complete Bilibili login on this machine.

```powershell
Copy-Item .env.example .env
.\\.venv\\Scripts\\yutto.exe auth login
```

## Build a lesson

Use a BV number or Bilibili URL. `-Part` selects a multi-part episode.

```powershell
.\scripts\download-and-build.ps1 -Source "BV1xxxxxxxxx" -Part 7
```

The source video is written to `downloads/`; its complete record is written to
`records/`. Both are intentionally ignored by Git.

## Run the local course site

```powershell
.\start-course.ps1
```

Open <http://127.0.0.1:8765/>. The server can also download and generate a
lesson through its page, using the same local credentials and environment
variables.

## Git policy

Track source, tests, prompts, dependency declarations, scripts, and docs.
Never commit `.env`, Bilibili login data, downloaded media, generated frame
caches, or logs. If a completed lesson must be preserved, create a release
archive or copy its record separately rather than adding it to Git history.
