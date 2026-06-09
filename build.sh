#!/bin/bash
set -e

echo "=== Building Video Downloader Bot ==="

# Download FFmpeg static binary
echo "Downloading FFmpeg..."
curl -sL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz -o /tmp/ffmpeg.tar.xz
tar -xf /tmp/ffmpeg.tar.xz -C /tmp
cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg .
cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe .
chmod +x ffmpeg ffprobe
echo "FFmpeg installed successfully!"

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Force upgrade yt-dlp to the absolute latest version
# This is critical because YouTube frequently breaks and yt-dlp fixes it fast
echo "Upgrading yt-dlp to latest version..."
pip install --upgrade yt-dlp

# Show yt-dlp version
echo "yt-dlp version: $(yt-dlp --version)"

echo "=== Build complete ==="
