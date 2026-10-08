#!/bin/bash
# Unix entry point; delegate to the validated cross-platform restore implementation.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
exec python3 "$ROOT/scripts/restore.py" "$@"