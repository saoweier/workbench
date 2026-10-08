#!/bin/bash
# Unix entry point; keep SQLite snapshot and secret-exclusion behavior in one implementation.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
exec python3 "$ROOT/scripts/backup.py" "$@"