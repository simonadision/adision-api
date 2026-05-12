# Lancement local Ad BUD API sur port 8000, BD locale adision (schéma ad_budget).
# api.py lit DATABASE_URL via os.environ ; on l'injecte ici, pas via .env
# (le .env Railway prod ne doit pas écraser ce override).
Set-Location -Path $PSScriptRoot
$env:DATABASE_URL = "postgresql://postgres:6268605Ss@localhost:5432/adision"
# JWT_SECRET identique à celui d'Ad MAT / Ad App pour SSO cross-app.
# api.py ne charge pas dotenv, on l'injecte ici.
$env:JWT_SECRET = "wCwMbfSFxaErBjsdazQZsHWaqeeL2HYRWo9Dl0I+XqM="
& "$PSScriptRoot\venv\Scripts\python.exe" -m uvicorn api:app --reload --port 8000
