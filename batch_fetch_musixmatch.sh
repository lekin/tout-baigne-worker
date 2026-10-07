#!/bin/bash
# Batch fetch Musixmatch synced lyrics for all tracks in Airtable

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Musixmatch Batch Fetch${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  Warning: .env file not found${NC}"
    echo "Please create .env with your API keys"
    exit 1
fi

# Check if pyairtable is installed
if ! python3 -c "import pyairtable" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  pyairtable not installed. Installing...${NC}"
    pip install pyairtable
fi

# Run the batch script
echo -e "${GREEN}Starting batch fetch...${NC}"
echo ""

python3 src/batch_musixmatch_fetch.py "$@"

echo ""
echo -e "${GREEN}✅ Done!${NC}"
