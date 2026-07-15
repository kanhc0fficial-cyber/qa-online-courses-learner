[CmdletBinding()]
param()

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { return }

foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith("#")) { continue }
    $separator = $text.IndexOf("=")
    if ($separator -lt 1) { throw "Invalid .env line: $line" }
    $name = $text.Substring(0, $separator).Trim()
    $value = $text.Substring($separator + 1)
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "Invalid .env variable name: $name" }
    Set-Item -Path "Env:$name" -Value $value
}
