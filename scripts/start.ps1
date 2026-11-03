param(
    [string]$RuntimeRoot = 'D:\TiFigureLab',
    [ValidateRange(1024,65535)][int]$Port = 8765,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$expectedData = Join-Path $RuntimeRoot 'data'
function Assert-ServerIdentity($health) {
    if ($health.name -ne 'TiFigure Lab') { throw 'The selected port belongs to another application.' }
    if (-not $health.project_root -or [IO.Path]::GetFullPath($health.project_root) -ne $projectRoot -or [IO.Path]::GetFullPath($health.storage) -ne $expectedData) {
        throw 'This port belongs to a different or older TiFigure Lab installation. Choose another -Port or stop that installation explicitly.'
    }
}
$pythonPath = Join-Path $RuntimeRoot 'runtime\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts\setup.ps1 first, or specify the correct RuntimeRoot.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\app.js'))) { throw 'Build the UI with scripts\setup.ps1 first.' }
$healthUrl = "http://127.0.0.1:$Port/api/health"
$healthy = $null
try { $healthy = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 } catch { }
if ($healthy) { Assert-ServerIdentity $healthy }
if (-not $healthy) {
    New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeRoot 'logs'),(Join-Path $RuntimeRoot 'tmp') | Out-Null
    $env:TIFIGURE_DATA = Join-Path $RuntimeRoot 'data'
    $env:TEMP = Join-Path $RuntimeRoot 'tmp'
    $env:TMP = $env:TEMP
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RuntimeRoot 'logs\server.out.log') -RedirectStandardError (Join-Path $RuntimeRoot 'logs\server.err.log') -PassThru
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $healthy = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1; break } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $healthy) { throw "Server did not start. Read $RuntimeRoot\logs\server.err.log" }
    Assert-ServerIdentity $healthy
    @{ pid=$process.Id; python=$pythonPath; port=$Port; project=$projectRoot } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeRoot 'server.json') -Encoding UTF8
}
Write-Output "TiFigure Lab is ready: http://127.0.0.1:$Port"
Write-Output "Workspace data: $($healthy.storage)"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" -WindowStyle Hidden }
