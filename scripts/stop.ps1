param([string]$RuntimeRoot = 'D:\TiFigureLab')
$ErrorActionPreference = 'Stop'
$recordPath = Join-Path $RuntimeRoot 'server.json'
if (-not (Test-Path -LiteralPath $recordPath)) { throw 'No server record in this RuntimeRoot.' }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$expectedProject = Split-Path -Parent $PSScriptRoot
$expectedPython = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) 'runtime\venv\Scripts\python.exe'
if ($record.project -ne $expectedProject -or $record.python -ne $expectedPython) { throw 'Server record belongs to another installation; no process stopped.' }
$running = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
if (-not $running) { Write-Output 'Recorded server is already stopped.'; exit 0 }
if ($running.ExecutablePath -ne $expectedPython -or $running.CommandLine -notmatch 'uvicorn backend.app:app' -or $running.CommandLine -notmatch ('--port\s+' + [regex]::Escape([string]$record.port) + '(\s|$)')) {
    throw 'Process identity does not match the recorded server; no process stopped.'
}
Stop-Process -Id $record.pid -ErrorAction Stop
Write-Output 'Stopped this installation. Workspace data was retained.'
