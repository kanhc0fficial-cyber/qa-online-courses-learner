[CmdletBinding()]
param()

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $root "scripts\\load-env.ps1")
& (Join-Path $root "course-workflow\\start.ps1")
