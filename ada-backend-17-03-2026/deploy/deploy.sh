#!/bin/bash
# ============================================================
# Ada Platform - Docker Deployment / Update Script
# ============================================================
# Deploys code updates by rebuilding the Docker image and
# restarting the stack. Includes automatic rollback on failure.
#
# Usage: bash deploy.sh [/path/to/source]
# ============================================================
set -euo pipefail

APP_DIR="/opt/ada-backend"
SOURCE="${1:-/tmp/ada-backend}"

echo "=== Ada Platform Docker Deployment ==="
echo "Source: $SOURCE"
echo "Target: $APP_DIR"
echo ""

# --- Pre-flight checks ---
if [ ! -d "$SOURCE" ]; then
    echo "ERROR: Source directory not found: $SOURCE"
    echo "Usage: bash deploy.sh /path/to/ada-backend"
    exit 1
fi

if [ ! -f "$SOURCE/main.py" ]; then
    echo "ERROR: main.py not found in source. Is this the right directory?"
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker not installed. Run setup-vm.sh first."
    exit 1
fi

# --- Tag current image as rollback ---
echo "[1/5] Tagging current image for rollback..."
CURRENT_IMAGE=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep ada-backend | head -1)
if [ -n "$CURRENT_IMAGE" ]; then
    docker tag "$CURRENT_IMAGE" ada-backend:rollback 2>/dev/null || true
    echo "  Rollback image: ada-backend:rollback"
else
    echo "  No existing image (first deployment)."
fi

# --- Sync code (preserve .env and data volumes) ---
echo "[2/5] Syncing code..."
rsync -av --exclude='.env' --exclude='.env.local' \
    --exclude='app/data/' --exclude='uploads/' --exclude='output/' \
    "$SOURCE/" "$APP_DIR/"

# --- Rebuild image ---
echo "[3/5] Building new Docker image..."
cd "$APP_DIR"
docker compose build --no-cache app

# --- Restart stack ---
echo "[4/5] Restarting containers..."
docker compose up -d

# --- Verify ---
echo "[5/5] Verifying deployment..."
sleep 5

# Wait up to 30s for health check
for i in $(seq 1 6); do
    if curl -sf http://localhost/api/health > /dev/null 2>&1; then
        echo ""
        echo "=== Deployment successful ==="
        curl -s http://localhost/api/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost/api/health
        echo ""
        docker compose ps
        exit 0
    fi
    echo "  Waiting for health check... (attempt $i/6)"
    sleep 5
done

# --- Rollback ---
echo ""
echo "=== WARNING: Health check failed after 30s ==="
echo "Container logs:"
docker compose logs --tail=30 app
echo ""

if docker image inspect ada-backend:rollback &>/dev/null; then
    echo "Rolling back to previous image..."
    docker tag ada-backend:rollback ada-backend:latest
    docker compose up -d
    sleep 5
    if curl -sf http://localhost/api/health > /dev/null 2>&1; then
        echo "Rollback successful."
    else
        echo "Rollback also failed. Manual intervention required."
        echo "  docker compose logs -f app"
    fi
else
    echo "No rollback image available. Check logs: docker compose logs -f app"
fi
exit 1
