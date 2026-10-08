#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/launcher.py start "$@"
