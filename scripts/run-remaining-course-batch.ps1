[CmdletBinding()]
param(
    [string]$SourceId = "BV1pS4y1g7D9",
    [int]$FirstPart = 1,
    [int]$LastPart = 25,
    [string]$ApiRoot = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$downloads = Join-Path $root "downloads"
$batchRoot = Join-Path $root "course-workflow\data\batches"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$batchId = "remaining-$timestamp"
$reportPath = Join-Path $batchRoot "$batchId.json"
$eventsPath = Join-Path $batchRoot "$batchId.events.jsonl"
$stdoutPath = Join-Path $batchRoot "$batchId.stdout.log"

New-Item -ItemType Directory -Force -Path $batchRoot | Out-Null

function Write-Event {
    param(
        [string]$Type,
        [hashtable]$Data
    )
    $event = [ordered]@{
        time = (Get-Date).ToString("o")
        type = $Type
        data = $Data
    }
    Add-Content -LiteralPath $eventsPath -Value ($event | ConvertTo-Json -Depth 8 -Compress) -Encoding UTF8
}

function Save-Report {
    param([hashtable]$Report)
    $Report.updated_at = (Get-Date).ToString("o")
    $temporary = "$reportPath.tmp"
    $Report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $reportPath -Force
}

function Get-Series {
    Invoke-RestMethod -Method Get -Uri "$ApiRoot/api/series" -TimeoutSec 15
}

function Test-ReusableDownload {
    param([int]$Part)
    $pattern = "^$([regex]::Escape($SourceId))-p0*$Part(?:\D|$)"
    foreach ($directory in Get-ChildItem -LiteralPath $downloads -Directory -ErrorAction SilentlyContinue) {
        if ($directory.Name -match $pattern) {
            $video = Get-ChildItem -LiteralPath $directory.FullName -Recurse -Filter *.mp4 -File -ErrorAction SilentlyContinue
            $subtitle = Get-ChildItem -LiteralPath $directory.FullName -Recurse -Filter *.srt -File -ErrorAction SilentlyContinue
            if ($video -and $subtitle) {
                return $true
            }
        }
    }
    return $false
}

$series = Get-Series
$completed = @(
    $series.lessons |
        Where-Object { $_.series_id -eq $SourceId } |
        ForEach-Object { [int]$_.part } |
        Sort-Object -Unique
)
$remaining = @(
    $FirstPart..$LastPart |
        Where-Object { $completed -notcontains $_ } |
        ForEach-Object {
            [pscustomobject]@{
                part = [int]$_
                reusable_download = [bool](Test-ReusableDownload -Part $_)
            }
        } |
        Sort-Object @{Expression = "reusable_download"; Descending = $true}, @{Expression = "part"; Descending = $false}
)

$report = [ordered]@{
    batch_id = $batchId
    status = "running"
    source_id = $SourceId
    started_at = (Get-Date).ToString("o")
    updated_at = (Get-Date).ToString("o")
    completed_before_start = @($completed)
    queue = @($remaining)
    finished_in_batch = @()
    skipped_existing = @()
    failed = @()
    current = $null
    report_path = $reportPath
    events_path = $eventsPath
    stdout_path = $stdoutPath
}
Save-Report -Report $report
Write-Event -Type "batch_started" -Data @{ completed = @($completed); queue = @($remaining) }

foreach ($item in $remaining) {
    $part = [int]$item.part
    $report.current = [ordered]@{
        part = $part
        reusable_download = [bool]$item.reusable_download
        status = "submitting"
        job_id = $null
    }
    Save-Report -Report $report
    Write-Event -Type "part_submitting" -Data @{ part = $part; reusable_download = [bool]$item.reusable_download }

    $body = @{
        source = $SourceId
        part = $part
        reuse_download = $true
        force_rebuild = $false
        ppt_complete = $true
        strict_validation = $true
    } | ConvertTo-Json

    try {
        $created = Invoke-RestMethod -Method Post -Uri "$ApiRoot/api/jobs" -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 30
    }
    catch {
        $failure = [ordered]@{
            part = $part
            stage = "submit"
            error = $_.Exception.Message
            time = (Get-Date).ToString("o")
        }
        $report.failed = @($report.failed) + $failure
        $report.current = $failure
        $report.status = "failed"
        Save-Report -Report $report
        Write-Event -Type "batch_failed" -Data $failure
        exit 1
    }

    if ($created.confirmation_required) {
        $report.skipped_existing = @($report.skipped_existing) + $part
        Write-Event -Type "part_skipped_existing" -Data @{ part = $part; lesson_id = $created.existing_lesson_id }
        Save-Report -Report $report
        continue
    }

    $jobId = [string]$created.id
    $report.current.job_id = $jobId
    $report.current.status = "running"
    Save-Report -Report $report
    Write-Event -Type "part_started" -Data @{ part = $part; job_id = $jobId }

    $lastStage = ""
    while ($true) {
        Start-Sleep -Seconds 15
        try {
            $job = Invoke-RestMethod -Method Get -Uri "$ApiRoot/api/jobs/$jobId" -TimeoutSec 15
        }
        catch {
            $failure = [ordered]@{
                part = $part
                job_id = $jobId
                stage = "monitor"
                error = $_.Exception.Message
                time = (Get-Date).ToString("o")
            }
            $report.failed = @($report.failed) + $failure
            $report.current = $failure
            $report.status = "failed"
            Save-Report -Report $report
            Write-Event -Type "batch_failed" -Data $failure
            exit 1
        }

        $report.current = [ordered]@{
            part = $part
            reusable_download = [bool]$item.reusable_download
            status = $job.status
            stage = $job.stage
            stage_label = $job.stage_label
            progress = $job.progress
            job_id = $jobId
            job_updated_at = $job.updated_at
        }
        Save-Report -Report $report

        if ([string]$job.stage -ne $lastStage) {
            $lastStage = [string]$job.stage
            Write-Event -Type "part_stage" -Data @{
                part = $part
                job_id = $jobId
                stage = $job.stage
                stage_label = $job.stage_label
                progress = $job.progress
            }
        }

        if ($job.status -eq "complete") {
            $finished = [ordered]@{
                part = $part
                job_id = $jobId
                lesson_id = $job.lesson_id
                lesson_url = $job.lesson_url
                completed_at = $job.updated_at
                reused_download = [bool]$item.reusable_download
            }
            $report.finished_in_batch = @($report.finished_in_batch) + $finished
            $report.current = $null
            Save-Report -Report $report
            Write-Event -Type "part_complete" -Data $finished
            break
        }

        if ($job.status -eq "failed") {
            $failure = [ordered]@{
                part = $part
                job_id = $jobId
                stage = $job.stage
                error = $job.error
                error_log = $job.error_log
                workflow_log = $job.log_path
                time = $job.updated_at
            }
            $report.failed = @($report.failed) + $failure
            $report.current = $failure
            $report.status = "failed"
            Save-Report -Report $report
            Write-Event -Type "batch_failed" -Data $failure
            exit 1
        }
    }
}

$report.status = "complete"
$report.current = $null
$report.completed_at = (Get-Date).ToString("o")
Save-Report -Report $report
Write-Event -Type "batch_complete" -Data @{ finished = @($report.finished_in_batch); skipped = @($report.skipped_existing) }
exit 0
