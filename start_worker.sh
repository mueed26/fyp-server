#!/bin/bash
# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Celery Worker (foreground)..."

# ! In case you are not using Poetry
# celery -A src.services.celery:celery_app worker --loglevel=info --pool=threads

# ! In case you are using Poetry
poetry run celery -A src.services.celery:celery_app worker --loglevel=info --pool=threads