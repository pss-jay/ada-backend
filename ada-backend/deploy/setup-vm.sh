#!/bin/bash
# ============================================================
# Ada Platform - Azure VM Setup Script (Docker)
# ============================================================
# Run this once on a fresh Ubuntu 22.04 Azure VM.
# Installs Docker and Docker Compose, then launches the stack.
#
# Usage: sudo bash setup-vm.sh
# ============================================================
set -euo pipefail

APP_DIR="/opt/ada-backend"

echo "=== Ada Platform VM Setup (Docker) ==="

# --- Install Docker ---
echo "[1/4] Installing Docker..."
if ! command -v docker &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo "  Docker installed."
else
    echo "  Docker already installed."
fi

# --- Ensure Docker Compose v2 ---
echo "[2/4] Verifying Docker Compose..."
docker compose version || { echo "ERROR: docker compose plugin not found"; exit 1; }

# --- Set up app directory ---
echo "[3/4] Setting up application directory..."
mkdir -p "$APP_DIR"
if [ -d "/tmp/ada-backend" ]; then
    cp -r /tmp/ada-backend/* "$APP_DIR/"
fi

cd "$APP_DIR"

# --- Check .env ---
if [ ! -f .env ]; then
    if [ -f .env.production ]; then
        echo "  Copying .env.production → .env (edit with your real values)"
        cp .env.production .env
    else
        echo "  WARNING: No .env file found. Create one from .env.production before starting."
    fi
fi

# --- Build and start ---
echo "[4/4] Building and starting Docker containers..."
docker compose build
docker compose up -d

sleep 5

echo ""
echo "=== Setup complete ==="
echo ""

# Health check
if curl -sf http://localhost/api/health > /dev/null 2>&1; then
    echo "Health check: PASSED"
    curl -s http://localhost/api/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost/api/health
else
    echo "Health check: WAITING (containers may still be starting)"
fi

echo ""
echo "Useful commands:"
echo "  docker compose -f $APP_DIR/docker-compose.yml logs -f     # Follow logs"
echo "  docker compose -f $APP_DIR/docker-compose.yml ps           # Container status"
echo "  docker compose -f $APP_DIR/docker-compose.yml restart app  # Restart backend"
echo "  docker compose -f $APP_DIR/docker-compose.yml down         # Stop everything"
echo "  docker compose -f $APP_DIR/docker-compose.yml up -d --build  # Rebuild & restart"
echo ""
