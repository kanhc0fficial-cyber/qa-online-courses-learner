[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,
    [int]$Part,
    [switch]$GeneralVideo,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root "scripts\\load-env.ps1")
$python = Join-Path $root ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not $env:XIAOMI_MIMO_API_KEY_TEM1 -or -not $env:XIAOMI_MIMO_BASE_URL) {
    throw "Set XIAOMI_MIMO_API_KEY_TEM1 and XIAOMI_MIMO_BASE_URL in this session before building a lesson."
}

$arguments = @(
    (Join-Path $root "video-analyzer\\scripts\\bilibili_workflow.py"),
    $Source,
    "--model", "mimo-v2.5",
    "--api-key-env", "XIAOMI_MIMO_API_KEY_TEM1",
    "--base-url-env", "XIAOMI_MIMO_BASE_URL"
)
if ($Part) { $arguments += @("--part", $Part) }
if (-not $GeneralVideo) { $arguments += "--ppt-complete" }
if ($Resume) { $arguments += "--resume" }

& $python @arguments
