FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force upgrade yt-dlp to latest (critical for YouTube)
RUN pip install --upgrade --no-cache-dir yt-dlp

COPY . .

# Use start.sh which upgrades yt-dlp before each start
CMD ["bash", "start.sh"]
