set -e

echo "==> Installing FFmpeg static binary..."
curl -sL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz -o /tmp/ffmpeg.tar.xz
tar -xf /tmp/ffmpeg.tar.xz -C /tmp
cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg .
cp /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe .
chmod +x ffmpeg ffprobe
rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-master-latest-linux64-gpl
echo "==> FFmpeg installed successfully!"

echo "==> Installing Python dependencies..."
pip install -r requirements.txt
echo "==> Python dependencies installed!"
