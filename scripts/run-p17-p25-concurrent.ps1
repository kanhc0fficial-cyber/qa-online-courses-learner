[CmdletBinding()]
param(
    [int]$MaxConcurrency = 3,
    [string]$SourceId = "BV1pS4y1g7D9",
    [string]$ApiRoot = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$batchRoot = Join-Path $root "course-workflow\data\batches"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$batchId = "p17-p25-concurrent-$timestamp"
$reportPath = Join-Path $batchRoot "$batchId.json"
$eventsPath = Join-Path $batchRoot "$batchId.events.jsonl"
New-Item -ItemType Directory -Force -Path $batchRoot | Out-Null

function Write-Event {
    param([string]$Type, [hashtable]$Data)
    $event = [ordered]@{
        time = (Get-Date).ToString("o")
        type = $Type
        data = $Data
    }
    Add-Content -LiteralPath $eventsPath `
        -Value ($event | ConvertTo-Json -Depth 8 -Compress) -Encoding UTF8
}

function Save-Report {
    $script:report.updated_at = (Get-Date).ToString("o")
    $temporary = "$reportPath.tmp"
    $script:report | ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $reportPath -Force
}

function Get-Series {
    Invoke-RestMethod -Method Get -Uri "$ApiRoot/api/series" -TimeoutSec 15
}

$series = Get-Series
$completed = @(
    $series.lessons |
        Where-Object {
            $_.series_id -eq $SourceId -and
            [int]$_.part -ge 17 -and
            [int]$_.part -le 25
        } |
        ForEach-Object { [int]$_.part } |
        Sort-Object -Unique
)
$pending = [System.Collections.Generic.Queue[int]]::new()
foreach ($part in 17..25) {
    if ($completed -notcontains $part) {
        $pending.Enqueue($part)
    }
}

$active = @{}
$report = [ordered]@{
    batch_id = $batchId
    status = "running"
    source_id = $SourceId
    scope = "P17-P25 only"
    max_concurrency = $MaxConcurrency
    started_at = (Get-Date).ToString("o")
    updated_at = (Get-Date).ToString("o")
    completed_before_start = @($completed)
    active = @()
    completed = @()
    failed = @()
    remaining = @($pending.ToArray())
    report_path = $reportPath
    events_path = $eventsPath
}
Save-Report
Write-Event -Type "batch_started" -Data @{
    scope = "P17-P25"
    max_concurrency = $MaxConcurrency
    pending = @($pending.ToArray())
}

while ($pending.Count -gt 0 -or $active.Count -gt 0) {
    while ($pending.Count -gt 0 -and $active.Count -lt $MaxConcurrency) {
        $part = $pending.Dequeue()
        $body = @{
            source = $SourceId
            part = $part
            reuse_download = $true
            force_rebuild = $false
            ppt_complete = $true
            strict_validation = $true
        } | ConvertTo-Json
        try {
            $created = Invoke-RestMethod -Method Post -Uri "$ApiRoot/api/jobs" `
                -ContentType "application/json; charset=utf-8" `
                -Body $body -TimeoutSec 30
            if ($created.confirmation_required) {
                $report.completed = @($report.completed) + [ordered]@{
                    part = $part
                    lesson_id = $created.existing_lesson_id
                    status = "existing"
                }
                Write-Event -Type "part_skipped_existing" -Data @{
                    part = $part
                    lesson_id = $created.existing_lesson_id
                }
            }
            else {
                $active[[string]$created.id] = [ordered]@{
                    part = $part
                    job_id = [string]$created.id
                    status = "running"
                    stage = $created.stage
                    progress = $created.progress
                }
                Write-Event -Type "part_started" -Data @{
                    part = $part
                    job_id = [string]$created.id
                }
            }
        }
        catch {
            $failure = [ordered]@{
                part = $part
                stage = "submit"
                error = $_.Exception.Message
                time = (Get-Date).ToString("o")
            }
            $report.failed = @($report.failed) + $failure
            Write-Event -Type "part_failed" -Data $failure
        }
        $report.active = @($active.Values)
        $report.remaining = @($pending.ToArray())
        Save-Report
    }

    if ($active.Count -eq 0) {
        continue
    }

    Start-Sleep -Seconds 15
    foreach ($jobId in @($active.Keys)) {
        try {
            $job = Invoke-RestMethod -Method Get `
                -Uri "$ApiRoot/api/jobs/$jobId" -TimeoutSec 15
        }
        catch {
            $active[$jobId].status = "monitor_error"
            $active[$jobId].error = $_.Exception.Message
            continue
        }
        $entry = $active[$jobId]
        $previousStage = [string]$entry.stage
        $entry.status = $job.status
        $entry.stage = $job.stage
        $entry.stage_label = $job.stage_label
        $entry.progress = $job.progress
        $entry.updated_at = $job.updated_at
        if ([string]$job.stage -ne $previousStage) {
            Write-Event -Type "part_stage" -Data @{
                part = $entry.part
                job_id = $jobId
                stage = $job.stage
                stage_label = $job.stage_label
                progress = $job.progress
            }
        }
        if ($job.status -eq "complete") {
            $report.completed = @($report.completed) + [ordered]@{
                part = $entry.part
                job_id = $jobId
                lesson_id = $job.lesson_id
                lesson_url = $job.lesson_url
                completed_at = $job.updated_at
                reused_download = $true
            }
            Write-Event -Type "part_complete" -Data @{
                part = $entry.part
                job_id = $jobId
                lesson_id = $job.lesson_id
            }
            $active.Remove($jobId)
        }
        elseif ($job.status -eq "failed") {
            $failure = [ordered]@{
                part = $entry.part
                job_id = $jobId
                stage = $job.stage
                error = $job.error
                error_log = $job.error_log
                workflow_log = $job.log_path
                time = $job.updated_at
            }
            $report.failed = @($report.failed) + $failure
            Write-Event -Type "part_failed" -Data $failure
            $active.Remove($jobId)
        }
    }
    $report.active = @($active.Values)
    $report.remaining = @($pending.ToArray())
    Save-Report
}

$report.status = if (@($report.failed).Count -gt 0) {
    "completed_with_failures"
}
else {
    "complete"
}
$report.completed_at = (Get-Date).ToString("o")
$report.active = @()
$report.remaining = @()
Save-Report
Write-Event -Type "batch_finished" -Data @{
    status = $report.status
    completed = @($report.completed)
    failed = @($report.failed)
}
