#!/usr/bin/env bash
# One-command demo environment (Linux / macOS).
#
#   ./scripts/demo.sh
#
# Brings the full stack up and loads the bundled GitHub demo knowledge base so
# you can present Mnema in minutes. Safe to re-run — it resets demo state.
#
# After it finishes, open http://localhost:8000 and sign in as a demo user
# (password: Mnema123!) or the admin from your .env.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "[demo] No .env found - generating one..."
    python scripts/create_env.py
fi

echo "[demo] Building and starting the stack (first run pulls the model)..."
docker compose up --build -d

echo "[demo] Waiting for the app to become healthy..."
deadline=$(( $(date +%s) + 600 ))
status=""
while [ "$status" != "healthy" ] && [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 5
    status="$(docker inspect --format '{{.State.Health.Status}}' mnema_app 2>/dev/null || echo starting)"
    echo "      app health: $status"
done

if [ "$status" != "healthy" ]; then
    echo "[demo] App did not become healthy in time. Check: docker compose logs app" >&2
    exit 1
fi

echo "[demo] Seeding the GitHub demo knowledge base..."
docker exec mnema_app python scripts/seed_github_demo.py

echo
echo "[demo] Ready -> http://localhost:8000"
echo "       Demo users share password: Mnema123!  (roles: everyone / engineering / devops)"
echo "       Admin: see ADMIN_USERNAME / ADMIN_PASSWORD in your .env"
