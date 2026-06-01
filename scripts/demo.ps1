# One-command demo environment (Windows / PowerShell).
#
#   .\scripts\demo.ps1
#
# Brings the full stack up and loads the bundled GitHub demo knowledge base so
# you can present Mnema in minutes. Safe to re-run — it resets demo state.
#
# After it finishes, open http://localhost:8000 and sign in as a demo user
# (password: Mnema123!) or the admin from your .env.

$ErrorActionPreference = "Stop"

# Ensure a .env exists (generates SECRET_KEY + POSTGRES_PASSWORD if missing).
if (-not (Test-Path ".env")) {
    Write-Host "[demo] No .env found - generating one..." -ForegroundColor Cyan
    python scripts/create_env.py
}

Write-Host "[demo] Building and starting the stack (first run pulls the model)..." -ForegroundColor Cyan
docker compose up --build -d

Write-Host "[demo] Waiting for the app to become healthy..." -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(10)
do {
    Start-Sleep -Seconds 5
    $status = (docker inspect --format '{{.State.Health.Status}}' mnema_app 2>$null)
    Write-Host "      app health: $status"
} until ($status -eq "healthy" -or (Get-Date) -gt $deadline)

if ($status -ne "healthy") {
    Write-Host "[demo] App did not become healthy in time. Check: docker compose logs app" -ForegroundColor Red
    exit 1
}

Write-Host "[demo] Seeding the GitHub demo knowledge base..." -ForegroundColor Cyan
docker exec mnema_app python scripts/seed_github_demo.py

Write-Host ""
Write-Host "[demo] Ready -> http://localhost:8000" -ForegroundColor Green
Write-Host "       Demo users share password: Mnema123!  (roles: everyone / engineering / devops)"
Write-Host "       Admin: see ADMIN_USERNAME / ADMIN_PASSWORD in your .env"
