# Lancement local Ad BUD API sur port 8000, BD locale adision (schema ad_budget).
# Secrets locaux dans .env.local.ps1 (NON suivi, cf .gitignore) ; copier depuis .env.local.ps1.example.
Set-Location -Path $PSScriptRoot
$localEnv = Join-Path $PSScriptRoot ".env.local.ps1"
if (-not (Test-Path $localEnv)) {
    Write-Error "Manque .env.local.ps1 (copier .env.local.ps1.example). Aucun secret en dur ici."
    exit 1
}
. $localEnv
& "$PSScriptRoot/venv/Scripts/python.exe" -m uvicorn api:app --reload --port 8000
