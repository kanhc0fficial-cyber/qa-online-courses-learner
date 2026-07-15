[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required and must be on PATH."
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg is required and must be on PATH before processing videos."
}

if (-not (Test-Path $venv)) {
    python -m venv $venv
}
$python = Join-Path $venv "Scripts\\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $root "video-analyzer\\requirements.txt")
& $python -m pip install -r (Join-Path $root "course-workflow\\requirements.txt")
& $python -m pip install yutto

Write-Host "Setup complete. Create .env from .env.example, set MiMo variables, then run: yutto auth login"
