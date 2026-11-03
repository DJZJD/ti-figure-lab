param(
    [string]$Python = 'python',
    [string]$Pnpm = 'pnpm',
    [string]$Node = 'node',
    [string]$RuntimeRoot = 'D:\TiFigureLab'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$runtime = Join-Path $RuntimeRoot 'runtime'
$temporary = Join-Path $RuntimeRoot 'tmp'
$uiRuntime = Join-Path $runtime 'frontend'
$link=Join-Path $projectRoot 'frontend\node_modules'
$dependencyDir=Join-Path $uiRuntime 'node_modules'
if (Test-Path -LiteralPath $link) {
    $existingLink=Get-Item -LiteralPath $link -Force
    if ($existingLink.LinkType -ne 'Junction' -or [IO.Path]::GetFullPath($existingLink.Target[0]) -ne $dependencyDir) {
        throw 'frontend/node_modules belongs to a different setup. Use a fresh source folder for a separate RuntimeRoot; nothing was removed.'
    }
}
New-Item -ItemType Directory -Force -Path $runtime,$temporary,$uiRuntime | Out-Null
$env:TEMP=$temporary; $env:TMP=$temporary
$venvPython = Join-Path $runtime 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv (Join-Path $runtime 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python environment.' }
}
& $venvPython -m pip install --cache-dir (Join-Path $RuntimeRoot 'cache\pip') -r (Join-Path $projectRoot 'requirements-lock.txt') -r (Join-Path $projectRoot 'requirements-paper.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
foreach ($name in @('package.json','pnpm-lock.yaml','pnpm-workspace.yaml')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\$name") -Destination (Join-Path $uiRuntime $name)
}
& $Pnpm --dir $uiRuntime --store-dir (Join-Path $RuntimeRoot 'cache\pnpm') install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw 'UI dependency installation failed.' }
if (-not (Test-Path -LiteralPath $link)) {
    New-Item -ItemType Junction -Path $link -Target $dependencyDir | Out-Null
}
& $Node (Join-Path $link 'typescript\bin\tsc') --project (Join-Path $projectRoot 'frontend\tsconfig.json') --noEmit
if ($LASTEXITCODE -ne 0) { throw 'TypeScript validation failed.' }
& $Node (Join-Path $projectRoot 'frontend\build.cjs')
if ($LASTEXITCODE -ne 0) { throw 'UI build failed.' }
Write-Output 'Setup complete. Run scripts\start.ps1.'
