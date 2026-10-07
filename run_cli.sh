#!/bin/bash
# Script to run the CLI tool

echo "Karaoke Generator CLI"
echo "Usage: ./run_cli.sh [command] [options]"
echo ""
PY_BIN=""
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
else
  PY_BIN="python3"
fi

"$PY_BIN" -m src.cli "$@"
