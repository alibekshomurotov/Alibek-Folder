#!/bin/bash
# Start script for Render Web Service
# Upgrades yt-dlp BEFORE Python imports it, then runs the bot
# Also works with python -m app which has its own auto-upgrade

set -e

echo "=== Starting Video Downloader Bot ==="

# Upgrade yt-dlp to the absolute latest version
# This is CRITICAL because YouTube constantly changes and old yt-dlp versions
# can only see storyboard formats (sb0-sb3), not actual video/audio formats.
echo "Upgrading yt-dlp to latest version..."
pip install --upgrade --no-cache-dir yt-dlp 2>&1 || true

# Show version for debugging
YTDLP_VER=$(yt-dlp --version 2>/dev/null || echo "unknown")
echo "yt-dlp CLI version: ${YTDLP_VER}"

# Verify yt-dlp can import properly
python -c "import yt_dlp; print(f'Python yt-dlp version: {yt_dlp.version.__version__}')" 2>&1 || true

# Start the bot using __main__.py (which has its own auto-upgrade check)
echo "Starting bot..."
exec python -m app
