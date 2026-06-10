#!/bin/bash
set -e

echo "=== Building Video Downloader Bot ==="

# Download FFmpeg static binary
echo "Downloading FFmpeg..."
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
curl -sL "$FFMPEG_URL" -o /tmp/ffmpeg.tar.xz

# Check if download succeeded
if [ ! -s /tmp/ffmpeg.tar.xz ]; then
    echo "WARNING: FFmpeg download failed, trying alternative URL..."
    FFMPEG_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    curl -sL "$FFMPEG_URL" -o /tmp/ffmpeg.tar.xz
fi

# Try to extract
if tar -xf /tmp/ffmpeg.tar.xz -C /tmp 2>/dev/null; then
    # Find ffmpeg binary
    FFMPEG_BIN=$(find /tmp -name "ffmpeg" -type f 2>/dev/null | head -1)
    FFMPEG_PROBE=$(find /tmp -name "ffprobe" -type f 2>/dev/null | head -1)
    
    if [ -n "$FFMPEG_BIN" ]; then
        cp "$FFMPEG_BIN" ./ffmpeg
        [ -n "$FFMPEG_PROBE" ] && cp "$FFMPEG_PROBE" ./ffprobe
        chmod +x ffmpeg ffprobe 2>/dev/null || true
        echo "FFmpeg installed successfully!"
    else
        echo "WARNING: FFmpeg binary not found after extraction"
    fi
else
    echo "WARNING: FFmpeg extraction failed, continuing without FFmpeg"
    echo "Video conversion will use rename-only fallback"
fi

# Verify ffmpeg works
if ./ffmpeg -version 2>/dev/null; then
    echo "FFmpeg is working!"
else
    echo "WARNING: FFmpeg not available, some features may be limited"
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Force upgrade yt-dlp to the ABSOLUTE latest version
echo "Upgrading yt-dlp to latest version..."
pip install --upgrade --no-cache-dir yt-dlp

# Verify yt-dlp version
YTDLP_VERSION=$(yt-dlp --version 2>/dev/null || echo "unknown")
echo "yt-dlp version: ${YTDLP_VERSION}"

echo "=== Build complete ==="
