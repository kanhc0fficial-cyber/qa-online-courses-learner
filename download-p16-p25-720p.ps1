[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$downloadRoot = Join-Path $root "downloads"
$logRoot = Join-Path $downloadRoot "_logs"
New-Item -ItemType Directory -Force -Path $downloadRoot, $logRoot | Out-Null

$yutto = Join-Path $root ".venv\Scripts\yutto.exe"
if (-not (Test-Path $yutto)) {
    $command = Get-Command yutto -ErrorAction SilentlyContinue
    if (-not $command) { throw "yutto was not found. Run setup.ps1 first." }
    $yutto = $command.Source
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$transcript = Join-Path $logRoot "p16-p25-720p-download-$timestamp.log"
Start-Transcript -LiteralPath $transcript | Out-Null

try {
    Write-Host "Checking Bilibili login..." -ForegroundColor Cyan
    & $yutto auth status
    if ($LASTEXITCODE -ne 0) { throw "yutto login is invalid. Run yutto auth login first." }

    $failed = @()
    foreach ($part in 16..25) {
        $destination = Join-Path $downloadRoot ("BV1pS4y1g7D9-p{0:D2}-720p" -f $part)
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        Write-Host "Starting or resuming P$part 720p download..." -ForegroundColor Green
        $url = "https://www.bilibili.com/video/BV1pS4y1g7D9?p=$part"
        & $yutto download $url --video-quality 64 --dir $destination --with-metadata --login-strict
        if ($LASTEXITCODE -ne 0) {
            $failed += $part
            Write-Host "P$part download failed; continuing with next part." -ForegroundColor Red
        }
    }

    if ($failed.Count -gt 0) {
        Write-Host ("Finished with failed parts: {0}. Run this launcher again to retry." -f ($failed -join ", ")) -ForegroundColor Yellow
        exit 1
    }
    Write-Host "P16-P25 720p resources are ready for later course generation." -ForegroundColor Green
}
finally {
    Stop-Transcript | Out-Null
    Write-Host "Log: $transcript"
}
