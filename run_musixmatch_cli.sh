#!/bin/bash
# Quick script to run CLI with Musixmatch flag

RECORD_ID=$1

if [ -z "$RECORD_ID" ]; then
    echo "Usage: ./run_musixmatch_cli.sh <RECORD_ID>"
    exit 1
fi

source .venv/bin/activate

python -m src.cli generate \
    --record-id "$RECORD_ID" \
    --use-musixmatch \
    --fast

echo ""
echo "✅ Done! Check the output directory for your karaoke video."
