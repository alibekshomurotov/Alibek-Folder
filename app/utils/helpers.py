"""Helper utilities"""

import re
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_url_from_text(text: str) -> Optional[str]:
    """Extract URL from text message"""
    url_pattern = re.compile(
        r'https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_+.~#?&/=]*'
    )
    match = url_pattern.search(text)
    return match.group(0) if match else None


def is_valid_telegram_channel_link(link: str) -> bool:
    """Check if the link is a valid Telegram channel link"""
    patterns = [
        r'^@[\w_]{5,32}$',  # @channel_name
        r'^https?://t\.me/[\w_]{5,32}$',  # https://t.me/channel_name
        r'^-100\d+$',  # -1001234567890 (channel ID)
    ]
    for pattern in patterns:
        if re.match(pattern, link.strip()):
            return True
    return False


def parse_telegram_channel_id(link: str) -> Optional[str]:
    """Parse Telegram channel username or ID from link"""
    link = link.strip()

    # @channel_name
    if link.startswith("@"):
        return link

    # https://t.me/channel_name
    match = re.match(r'https?://t\.me/([\w_]{5,32})', link)
    if match:
        return f"@{match.group(1)}"

    # -1001234567890 (raw ID)
    if link.startswith("-100"):
        return link

    return None


def generate_promo_code(length: int = 8) -> str:
    """Generate a random promo code"""
    import random
    import string
    prefix = "PREM"
    chars = string.ascii_uppercase + string.digits
    return f"{prefix}-{''.join(random.choices(chars, k=length))}"


def format_datetime(dt: datetime) -> str:
    """Format datetime for display"""
    if dt is None:
        return "N/A"
    return dt.strftime("%d.%m.%Y %H:%M")


def humanize_number(n: int) -> str:
    """Humanize a number (1000 -> 1K)"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def truncate_text(text: str, max_length: int = 100) -> str:
    """Truncate text with ellipsis"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


class RateLimiter:
    """Simple in-memory rate limiter"""

    def __init__(self, max_requests: int = 10, period: int = 60):
        self.max_requests = max_requests
        self.period = period
        self._requests: dict[int, list[float]] = {}

    def is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to make a request"""
        import time
        now = time.time()

        if user_id not in self._requests:
            self._requests[user_id] = []

        # Clean old requests
        self._requests[user_id] = [
            t for t in self._requests[user_id] if now - t < self.period
        ]

        if len(self._requests[user_id]) >= self.max_requests:
            return False

        self._requests[user_id].append(now)
        return True

    def get_remaining(self, user_id: int) -> int:
        """Get remaining requests for user"""
        import time
        now = time.time()
        if user_id not in self._requests:
            return self.max_requests
        active = [t for t in self._requests[user_id] if now - t < self.period]
        return max(0, self.max_requests - len(active))
