#!/bin/bash
# Setup script for Karaoke Generator

set -e

echo "=========================================="
echo "Karaoke Generator Setup"
echo "=========================================="

# Check Python version
echo ""
echo "Checking Python version..."
python_version=$(python --version 2>&1 | awk '{print $2}')
echo "✓ Python $python_version found"

# Check if FFmpeg is installed
echo ""
echo "Checking FFmpeg..."
if command -v ffmpeg &> /dev/null; then
    ffmpeg_version=$(ffmpeg -version | head -n1)
    echo "✓ FFmpeg found: $ffmpeg_version"
else
    echo "❌ FFmpeg not found!"
    echo "   Please install FFmpeg:"
    echo "   macOS: brew install ffmpeg"
    echo "   Ubuntu: sudo apt-get install ffmpeg"
    exit 1
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
pip install -r requirements.txt
echo "✓ Dependencies installed"

# Create .env file if it doesn't exist
echo ""
if [ -f ".env" ]; then
    echo "✓ .env file already exists"
else
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "✓ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env file with your Airtable credentials:"
    echo "   - AIRTABLE_API_KEY"
    echo "   - AIRTABLE_BASE_ID"
    echo "   - AIRTABLE_TABLE_NAME"
fi

# Create output directory
echo ""
echo "Creating output directory..."
mkdir -p output
echo "✓ Output directory created"

# Check if font exists
echo ""
if [ -f "legacy/karaoke/fonts/SpaceMono-Regular.ttf" ]; then
    echo "✓ Font file found"
else
    echo "⚠️  Font file not found at legacy/karaoke/fonts/SpaceMono-Regular.ttf"
    echo "   Please ensure the font file exists or update KARAOKE_FONT_PATH in .env"
fi

# Check if logo exists
echo ""
if [ -f "Logo-YellowDropShadow.png" ]; then
    echo "✓ Logo file found"
else
    echo "⚠️  Logo file not found at Logo-YellowDropShadow.png"
    echo "   Please ensure the logo file exists or update KARAOKE_LOGO_PATH in .env"
fi

# Make scripts executable
echo ""
echo "Making scripts executable..."
chmod +x run_api.sh run_cli.sh examples/cli_examples.sh
echo "✓ Scripts are executable"

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env with your Airtable credentials"
echo "2. Test configuration: python -m src.cli config"
echo "3. Start API server: ./run_api.sh"
echo "4. Or use CLI: python -m src.cli --help"
echo ""
echo "For more information, see:"
echo "- README.md for full documentation"
echo "- QUICKSTART.md for quick start guide"
echo "- examples/ for usage examples"
echo ""
