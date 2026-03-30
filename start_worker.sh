#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "Starting Celery Worker (foreground)..."
poetry run celery -A src.services.celery:celery_app worker --loglevel=info --pool=threads