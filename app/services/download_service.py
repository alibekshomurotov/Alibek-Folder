import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.download_repo import DownloadRepository
from app.utils.downloader import (
    detect_platform,
    is_video_url,
    extract_video_info,
    download_video_auto_quality,
    download_video,
    cleanup_file,
    format_file_size,
)
from app.config import config

logger = logging.getLogger(__name__)


async def _record_download_bg(user_id: int, platform: str, url: str, quality: str, file_size_mb: float):
    """Downloadni background'da DB ga yozish — foydalanuvchiga ta'sir yo'q."""
    try:
        session_factory = await get_session_factory()
        async with session_factory() as session:
            download_repo = DownloadRepository(session)
            user_repo = UserRepository(session)
            await download_repo.create(
                user_id=user_id,
                platform=platform,
                url=url,
                quality=quality,
                file_size=file_size_mb,
            )
            await user_repo.update_download_count(user_id)
    except Exception as e:
        logger.error(f"Failed to record download: {e}")


class DownloadService:
    """Service for handling video download operations"""

    @staticmethod
    async def process_url(url: str) -> Optional[Dict[str, Any]]:
        """
        Process a video URL: extract info and return video details.

        Returns:
            Dict with 'info', 'platform', 'available_qualities' or None
        """
        platform = detect_platform(url)
        if not platform:
            return None

        info = await extract_video_info(url)
        if not info:
            return None

        # Determine available qualities
        formats = info.get("formats", [])
        available_qualities = set()
        for fmt in formats:
            height = fmt.get("height")
            if height:
                available_qualities.add(height)

        # Sort qualities
        quality_list = sorted([q for q in available_qualities if q], reverse=True)
        quality_strings = [f"{q}p" for q in quality_list if q <= 1080]

        # Limit to common qualities
        common_qualities = ["1080p", "720p", "480p", "360p"]
        filtered_qualities = [q for q in common_qualities if q in quality_strings]
        if not filtered_qualities:
            filtered_qualities = quality_strings[:4] if quality_strings else ["720p"]

        # Get estimated file size
        file_size = None
        for fmt in formats:
            if fmt.get("filesize"):
                file_size = fmt["filesize"]
                break

        return {
            "info": info,
            "platform": platform,
            "available_qualities": filtered_qualities,
            "estimated_size": format_file_size(file_size) if file_size else "N/A",
        }

    @staticmethod
    async def download(url: str, quality: str = "720p",
                       audio_only: bool = False, user_id: int = None) -> Optional[Dict[str, Any]]:
        """
        Download a video/audio file.

        Returns:
            Dict with 'file_path', 'info', 'platform', 'file_size_mb' or None
        """
        platform = detect_platform(url)
        if not platform:
            return None

        quality_num = quality.replace("p", "")

        if audio_only:
            result = await download_video(url, quality_num, audio_only=True)
        else:
            result = await download_video_auto_quality(url, quality_num)

        if result is None:
            return None

        file_path, info = result
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        # Record download in database (BACKGROUND — javob tezligiga ta'sir yo'q)
        if user_id:
            import asyncio
            asyncio.create_task(_record_download_bg(user_id, platform, url, quality, file_size_mb))

        return {
            "file_path": file_path,
            "info": info,
            "platform": platform,
            "file_size_mb": file_size_mb,
            "quality": quality,
        }
        