!/bin/bash
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

# Force upgrade yt-dlp to the ABSOLUTE latest version
# This is CRITICAL because YouTube frequently changes and old yt-dlp versions
# can only see storyboard formats (sb0-sb3) instead of video/audio formats.
echo "Upgrading yt-dlp to latest version..."
pip install --upgrade --no-cache-dir yt-dlp

# Verify yt-dlp version
YTDLP_VERSION=$(yt-dlp --version 2>/dev/null || echo "unknown")
echo "yt-dlp version: ${YTDLP_VERSION}"

# Warn if version looks old
if [[ "${YTDLP_VERSION}" == 2025.* ]]; then
    echo "WARNING: yt-dlp version ${YTDLP_VERSION} may be too old for current YouTube!"
    echo "The start.sh script will also upgrade yt-dlp at runtime."
fi

echo "=== Build complete ==="
