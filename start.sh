#!/bin/bash
# Start script for Render Web Service
# Upgrades yt-dlp BEFORE Python imports it, then runs the bot

echo "=== Starting Video Downloader Bot ==="

# Upgrade yt-dlp to the absolute latest version
echo "Upgrading yt-dlp to latest version..."
pip install --upgrade --no-cache-dir yt-dlp 2>&1 || true

# Show version for debugging
YTDLP_VER=$(yt-dlp --version 2>/dev/null || echo "unknown")
echo "yt-dlp CLI version: ${YTDLP_VER}"

# Verify yt-dlp can import properly
python -c "import yt_dlp; print(f'Python yt-dlp version: {yt_dlp.version.__version__}')" 2>&1 || true

# Start the bot directly
echo "Starting bot..."
exec python -m app.main
