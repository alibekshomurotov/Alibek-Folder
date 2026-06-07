"""Application Configuration Module"""

import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    """Bot configuration settings"""
    token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: List[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
    ])
    username: str = os.getenv("BOT_USERNAME", "")

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


@dataclass
class DatabaseConfig:
    """Database configuration settings"""
    url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db")


@dataclass
class RedisConfig:
    """Redis configuration settings"""
    url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    enabled: bool = os.getenv("REDIS_ENABLED", "false").lower() == "true"


@dataclass
class WebhookConfig:
    """Webhook configuration settings"""
    url: str = os.getenv("WEBHOOK_URL", "")
    host: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port: int = int(os.getenv("WEBHOOK_PORT", "8443"))
    enabled: bool = os.getenv("USE_WEBHOOK", "false").lower() == "true"


@dataclass
class DownloadConfig:
    """Download configuration settings"""
    max_file_size_mb: int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
    default_quality: int = int(os.getenv("DEFAULT_QUALITY", "720"))
    timeout: int = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
    cookies_file: str = os.getenv("COOKIES_FILE", "cookies.txt")
    ffmpeg_available: bool = False

    def __post_init__(self):
        self.ffmpeg_available = shutil.which("ffmpeg") is not None
        # Also check project directory for static ffmpeg (Render deployment)
        if not self.ffmpeg_available:
            project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_ffmpeg = os.path.join(project_dir, "ffmpeg")
            if os.path.isfile(local_ffmpeg) and os.access(local_ffmpeg, os.X_OK):
                self.ffmpeg_available = True
                os.environ["PATH"] = project_dir + ":" + os.environ.get("PATH", "")


@dataclass
class RateLimitConfig:
    """Rate limiting configuration - disabled, all users are free"""
    downloads: int = int(os.getenv("RATE_LIMIT_DOWNLOADS", "999"))
    period: int = int(os.getenv("RATE_LIMIT_PERIOD", "1"))  # seconds


@dataclass
class PremiumConfig:
    """Premium referral reward configuration"""
    referral_5_days: int = int(os.getenv("PREMIUM_REFERRAL_5", "3"))
    referral_20_days: int = int(os.getenv("PREMIUM_REFERRAL_20", "30"))


@dataclass
class Config:
    """Main application configuration"""
    bot: BotConfig = field(default_factory=BotConfig)
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    premium: PremiumConfig = field(default_factory=PremiumConfig)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


# Global config instance
config = Config()

# Supported platforms
SUPPORTED_PLATFORMS = {
    "tiktok": {
        "name": "TikTok",
        "emoji": "🎵",
        "domains": ["tiktok.com", "vt.tiktok.com", "vm.tiktok.com"],
    },
    "instagram": {
        "name": "Instagram",
        "emoji": "📸",
        "domains": ["instagram.com", "instagr.am"],
    },
    "youtube": {
        "name": "YouTube",
        "emoji": "▶️",
        "domains": ["youtube.com", "youtu.be", "youtube shorts"],
    },
    "facebook": {
        "name": "Facebook",
        "emoji": "📘",
        "domains": ["facebook.com", "fb.watch", "fb.com"],
    },
    "twitter": {
        "name": "X (Twitter)",
        "emoji": "🐦",
        "domains": ["twitter.com", "x.com", "t.co"],
    },
    "pinterest": {
        "name": "Pinterest",
        "emoji": "📌",
        "domains": ["pinterest.com", "pin.it"],
    },
    "snapchat": {
        "name": "Snapchat",
        "emoji": "👻",
        "domains": ["snapchat.com", "t.snapchat.com"],
    },
    "threads": {
        "name": "Threads",
        "emoji": "🧵",
        "domains": ["threads.net"],
    },
}

CHANNEL_TYPES = {
    "telegram": {"name": "Telegram", "emoji": "📢", "hint": "Kanal linkini yuboring (masalan: @channel_name yoki https://t.me/channel_name)"},
    "instagram": {"name": "Instagram", "emoji": "📸", "hint": "Instagram sahifa linkini yuboring (masalan: https://instagram.com/username)"},
    "youtube": {"name": "YouTube", "emoji": "▶️", "hint": "YouTube kanal linkini yuboring (masalan: https://youtube.com/@channel)"},
    "tiktok": {"name": "TikTok", "emoji": "🎵", "hint": "TikTok akkaunt linkini yuboring (masalan: https://tiktok.com/@username)"},
    "facebook": {"name": "Facebook", "emoji": "📘", "hint": "Facebook sahifa linkini yuboring (masalan: https://facebook.com/page)"},
    "twitter": {"name": "X (Twitter)", "emoji": "🐦", "hint": "X/Twitter akkaunt linkini yuboring (masalan: https://x.com/username)"},
    "other": {"name": "Boshqa", "emoji": "🔗", "hint": "Sahifa/kanal linkini yuboring"},
}

# Neon Blue color theme
THEME = {
    "primary": "#00BFFF",
    "accent": "#0080FF",
    "success": "#00FF88",
    "warning": "#FFB800",
    "error": "#FF4444",
    "premium": "#FFD700",
}