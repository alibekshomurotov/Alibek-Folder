#!/bin/bash
# Start script for Render Web Service
# CRITICAL: Upgrades yt-dlp BEFORE Python imports it
# This ensures YouTube format parsing works with the latest yt-dlp

set -e

echo "=== Starting Video Downloader Bot ==="

# Upgrade yt-dlp to the absolute latest version
# This is CRITICAL because YouTube constantly changes and old yt-dlp versions
# can only see storyboard formats (no video/audio)
echo "Upgrading yt-dlp to latest version..."
pip install --upgrade yt-dlp 2>&1

# Show version for debugging
echo "yt-dlp version: $(yt-dlp --version 2>/dev/null || echo 'unknown')"

# Verify yt-dlp can import properly
python -c "import yt_dlp; print(f'Python yt-dlp version: {yt_dlp.version.__version__}')" 2>&1

# Start the bot
echo "Starting bot..."
exec python -m app.main
