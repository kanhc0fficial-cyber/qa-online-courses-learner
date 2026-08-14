$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $root
$python = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$baseUrl = "http://127.0.0.1:8765"
$url = "$baseUrl/"
$listenAddress = "0.0.0.0"
$canonicalRoot = [System.IO.Path]::GetFullPath($root).TrimEnd("\")

function Test-CanonicalService {
    try {
        $response = Invoke-WebRequest -Uri "$baseUrl/api/series" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $false }
        $payload = $response.Content | ConvertFrom-Json
        if (-not $payload.canonical) { return $false }
        $reportedRoot = [System.IO.Path]::GetFullPath([string]$payload.canonical_root).TrimEnd("\")
        return $reportedRoot -eq $canonicalRoot
    } catch {
        return $false
    }
}

function Test-LanListener {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    return $null -ne $listener -and $listener.LocalAddress -in @("0.0.0.0", "::")
}

if ((Test-CanonicalService) -and (Test-LanListener)) {
    Start-Process $url
    exit 0
}

$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($process.CommandLine -notmatch "uvicorn\s+server:app") {
        throw "8765 端口被非课程服务占用，拒绝停止：$($process.CommandLine)"
    }
    Stop-Process -Id $listener.OwningProcess
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:COURSE_MAX_ACTIVE_JOBS = "15"
$stdout = Join-Path $root "server.stdout.log"
$stderr = Join-Path $root "server.stderr.log"
Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "server:app", "--host", $listenAddress, "--port", "8765") `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        if (Test-CanonicalService) {
            Start-Process $url
            exit 0
        }
    } catch {}
}
throw "Course workflow did not start. Check server.stderr.log."
