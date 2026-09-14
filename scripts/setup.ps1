param([switch]$NoStart)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop (Linux containers) is required.' }
docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required.' }
if (-not (Test-Path -LiteralPath '.env')) {
    $frontierRandom = New-Object byte[] 32
    $frontierGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $frontierGenerator.GetBytes($frontierRandom)
    $frontierGenerator.Dispose()
    $frontierToken = [Convert]::ToBase64String($frontierRandom)
    $frontierConfig = [IO.File]::ReadAllText((Join-Path (Get-Location) '.env.example')).Replace('replace-with-generated-random-token', $frontierToken)
    [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $frontierConfig)
}
if (-not $NoStart) {
    docker compose up --build -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Check docker compose logs.' }
    Write-Host 'Frontier is ready. Open http://localhost:8765 (or WORKBENCH_PORT in .env).'
}
