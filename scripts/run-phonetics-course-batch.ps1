[CmdletBinding()]
param(
    [string]$Source = "BV1iV411z7Nj",
    [int]$StartPart = 3,
    [int]$EndPart = 47
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $root "scripts\load-env.ps1")
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "C:\Users\goldenwhale\miniconda3\python.exe"
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_DISABLE_XET = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"

& $python (Join-Path $root "scripts\run_phonetics_course_batch.py") `
    --source $Source `
    --start-part $StartPart `
    --end-part $EndPart
exit $LASTEXITCODE
