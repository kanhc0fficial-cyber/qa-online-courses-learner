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
    if (-not $command) {
        throw "未找到 yutto。请先运行 setup.ps1。"
    }
    $yutto = $command.Source
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$transcript = Join-Path $logRoot "p16-p25-720p-$timestamp.log"
Start-Transcript -LiteralPath $transcript | Out-Null

try {
    Write-Host "检查 Bilibili 登录状态……" -ForegroundColor Cyan
    & $yutto auth status
    if ($LASTEXITCODE -ne 0) {
        throw "yutto 登录无效。请先运行 yutto auth login。"
    }

    $failed = @()
    foreach ($part in 16..25) {
        $destination = Join-Path $downloadRoot ("BV1pS4y1g7D9-p{0:D2}-720p" -f $part)
        New-Item -ItemType Directory -Force -Path $destination | Out-Null

        $completedVideo = Get-ChildItem -LiteralPath $destination -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".mp4", ".mkv", ".mov") -and $_.Length -gt 1MB } |
            Select-Object -First 1
        if ($completedVideo) {
            Write-Host "p$part 已存在视频，跳过：$($completedVideo.FullName)" -ForegroundColor DarkGray
            continue
        }

        Write-Host "`n开始下载 p$part（720p）……" -ForegroundColor Green
        $url = "https://www.bilibili.com/video/BV1pS4y1g7D9?p=$part"
        & $yutto download $url --video-quality 64 --dir $destination --with-metadata --login-strict
        if ($LASTEXITCODE -ne 0) {
            $failed += $part
            Write-Host "p$part 下载失败，继续下一集。" -ForegroundColor Red
        }
    }

    if ($failed.Count -gt 0) {
        Write-Host "`n下载结束。失败分集：$($failed -join ', ')；重新双击脚本即可重试。" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "`np16-p25 的 720p 资源已全部下载或确认存在。" -ForegroundColor Green
}
finally {
    Stop-Transcript | Out-Null
    Write-Host "日志：$transcript"
}
