$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $root
$python = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$url = "http://127.0.0.1:8765/"

try {
    $response = Invoke-WebRequest -Uri "$url/api/series" -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
        Start-Process $url
        exit 0
    }
} catch {}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$stdout = Join-Path $root "server.stdout.log"
$stderr = Join-Path $root "server.stderr.log"
Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8765") `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri "$url/api/series" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Start-Process $url
            exit 0
        }
    } catch {}
}
throw "Course workflow did not start. Check server.stderr.log."
