[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $root "scripts\load-env.ps1")

$downloadRoot = Join-Path $root "downloads"
$logRoot = Join-Path $downloadRoot "_logs"
$courseRoot = Join-Path $root "course-workflow"
New-Item -ItemType Directory -Force -Path $downloadRoot, $logRoot | Out-Null

$yutto = Join-Path $root ".venv\Scripts\yutto.exe"
if (-not (Test-Path $yutto)) {
    $command = Get-Command yutto -ErrorAction SilentlyContinue
    if (-not $command) { throw "yutto was not found. Run setup.ps1 first." }
    $yutto = $command.Source
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not $env:XIAOMI_MIMO_API_KEY_TEM1 -or -not $env:XIAOMI_MIMO_BASE_URL) {
    throw "MiMo environment variables are missing. Fill in .env first."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$transcript = Join-Path $logRoot "p16-p25-full-chain-$timestamp.log"
Start-Transcript -LiteralPath $transcript | Out-Null

function Test-CourseApi {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/series" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-CourseApi {
    if (Test-CourseApi) { return }
    $stdout = Join-Path $courseRoot "server.stdout.log"
    $stderr = Join-Path $courseRoot "server.stderr.log"
    Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8765") `
        -WorkingDirectory $courseRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden | Out-Null
    foreach ($attempt in 1..30) {
        Start-Sleep -Seconds 1
        if (Test-CourseApi) { return }
    }
    throw "Course API failed to start. Check course-workflow/server.stderr.log."
}

function Wait-ForIdleCourseApi {
    while ($true) {
        $series = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/series" -Method Get -TimeoutSec 10
        $active = @($series.jobs | Where-Object { $_.status -in @("queued", "running") })
        if ($active.Count -eq 0) { return }
        Write-Host ("Waiting for active P{0} job..." -f $active[0].part) -ForegroundColor Yellow
        Start-Sleep -Seconds 15
    }
}

function Submit-And-WaitForCourse([int]$part) {
    Wait-ForIdleCourseApi
    $payload = @{
        source = "BV1pS4y1g7D9"
        part = $part
        reuse_download = $true
        force_rebuild = $false
        ppt_complete = $true
        strict_validation = $true
    } | ConvertTo-Json

    $job = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/jobs" `
        -Method Post -ContentType "application/json; charset=utf-8" -Body $payload -TimeoutSec 30
    if ($job.confirmation_required) {
        Write-Host "P$part already has a completed lesson. Skipping rebuild." -ForegroundColor DarkGray
        return $true
    }

    Write-Host "P$part course job started: $($job.id)" -ForegroundColor Cyan
    while ($true) {
        Start-Sleep -Seconds 15
        $state = Invoke-RestMethod -Uri ("http://127.0.0.1:8765/api/jobs/{0}" -f $job.id) `
            -Method Get -TimeoutSec 10
        Write-Host ("P{0}: {1} ({2} percent)" -f $part, $state.stage_label, $state.progress)
        if ($state.status -eq "complete") {
            Write-Host "P$part interactive lesson completed." -ForegroundColor Green
            return $true
        }
        if ($state.status -eq "failed") {
            Write-Host "P$part course processing failed: $($state.error)" -ForegroundColor Red
            return $false
        }
    }
}

try {
    Write-Host "Checking Bilibili login..." -ForegroundColor Cyan
    & $yutto auth status
    if ($LASTEXITCODE -ne 0) { throw "yutto login is invalid. Run yutto auth login first." }

    Start-CourseApi
    $failed = @()
    foreach ($part in 16..25) {
        $destination = Join-Path $downloadRoot ("BV1pS4y1g7D9-p{0:D2}-720p" -f $part)
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        $video = Get-ChildItem -LiteralPath $destination -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".mp4", ".mkv", ".webm", ".mov") -and $_.Length -gt 1MB } |
            Select-Object -First 1
        $subtitle = Get-ChildItem -LiteralPath $destination -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".srt", ".vtt") } |
            Select-Object -First 1

        if ($video -and $subtitle) {
            Write-Host "P$part local video and subtitle found; reusing download." -ForegroundColor DarkGray
        } else {
            Write-Host "Starting P$part 720p download..." -ForegroundColor Green
            $url = "https://www.bilibili.com/video/BV1pS4y1g7D9?p=$part"
            & $yutto download $url --video-quality 64 --dir $destination --with-metadata --login-strict
            if ($LASTEXITCODE -ne 0) {
                $failed += $part
                Write-Host "P$part download failed; continuing with next part." -ForegroundColor Red
                continue
            }
        }

        if (-not (Submit-And-WaitForCourse -part $part)) { $failed += $part }
    }

    if ($failed.Count -gt 0) {
        Write-Host ("Finished with failed parts: {0}. Run this launcher again to retry." -f ($failed -join ", ")) -ForegroundColor Yellow
        exit 1
    }
    Write-Host "P16-P25 downloads and interactive lessons are complete." -ForegroundColor Green
}
finally {
    Stop-Transcript | Out-Null
    Write-Host "Log: $transcript"
}
