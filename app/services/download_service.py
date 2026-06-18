"""Download Service - Video yuklash biznes mantigi

Barcha platformalar (YouTube, TikTok, Instagram, va boshqalar)
uchun yt-dlp ishlatiladi. YouTube uchun SOCKS5 proxy orqali
to'g'ridan-to'g'ri yt-dlp eng tezkor yo'l (5-12 soniya).

youtube_api.py moduli olib tashlandi — Invidious/Piped/Cobalt
hammasi ishlamayapti va 60+ soniya behuda sarflaydi.
"""

import logging
import os
import time
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
    format_duration,
)
from app.config import config

logger = logging.getLogger(__name__)


class DownloadService:
    """Barcha platformalar uchun video yuklash xizmati"""

    @staticmethod
    async def process_url(url: str) -> Optional[Dict[str, Any]]:
        """
        Video URL ni qayta ishlash: info olish va video tafsilotlarini qaytarish.

        Barcha platformalar uchun yt-dlp ishlatiladi.
        YouTube uchun PROXY_URLS orqali SOCKS5 proxy avtomatik ishlaydi.

        Args:
            url: Video URL manzili

        Returns:
            Dict: 'info', 'platform', 'available_qualities', 'estimated_size'
            yoki None agar xato bo'lsa
        """
        platform = detect_platform(url)
        if not platform:
            logger.warning(f"[DownloadService] Noma'lum platforma: {url}")
            return None

        t0 = time.time()
        info = await extract_video_info(url)
        elapsed = time.time() - t0

        if not info:
            logger.error(f"[DownloadService] Info olish muvaffaqiyatsiz ({platform}): {url}")
            return None

        logger.info(f"[DownloadService] Info olindi ({platform}) — {elapsed:.1f}s")

        # Mavjud sifat variantlarini aniqlash
        formats = info.get("formats", [])
        available_qualities = set()
        for fmt in formats:
            height = fmt.get("height")
            if height:
                available_qualities.add(height)

        # Sifatlarni saralash (yuqoridan pastga)
        quality_list = sorted([q for q in available_qualities if q], reverse=True)
        quality_strings = [f"{q}p" for q in quality_list if q <= 1080]

        # Umumiy sifatlar bilan filterlash
        common_qualities = ["1080p", "720p", "480p", "360p"]
        filtered_qualities = [q for q in common_qualities if q in quality_strings]
        if not filtered_qualities:
            filtered_qualities = quality_strings[:4] if quality_strings else ["720p"]

        # Taxminiy fayl hajmi
        file_size = None
        for fmt in formats:
            if fmt.get("filesize"):
                file_size = fmt["filesize"]
                break
        if file_size is None:
            for fmt in formats:
                if fmt.get("filesize_approx"):
                    file_size = fmt["filesize_approx"]
                    break

        # Video tafsilotlari
        title = info.get("title", "Noma'lum video")
        duration = info.get("duration", 0) or 0
        uploader = info.get("uploader", "") or info.get("channel", "") or ""

        return {
            "info": info,
            "platform": platform,
            "available_qualities": filtered_qualities,
            "estimated_size": format_file_size(file_size) if file_size else "N/A",
            "title": title,
            "duration": format_duration(int(float(duration))) if duration else "N/A",
            "uploader": uploader,
        }

    @staticmethod
    async def download(url: str, quality: str = "720p",
                       audio_only: bool = False, user_id: int = None) -> Optional[Dict[str, Any]]:
        """
        Video/Audio faylni yuklash.

        Barcha platformalar uchun yt-dlp ishlatiladi.
        PROXY_URLS orqali SOCKS5 proxy avtomatik ishlaydi.
        Agar fayl hajmi katta bo'lsa, sifat avtomatik pasaytiriladi.

        Args:
            url: Video URL manzili
            quality: Sifat (masalan: "720p", "1080p")
            audio_only: Faqat audio yuklash
            user_id: Foydalanuvchi ID si (database uchun)

        Returns:
            Dict: 'file_path', 'info', 'platform', 'file_size_mb', 'quality'
            yoki None agar yuklash muvaffaqiyatsiz bo'lsa
        """
        platform = detect_platform(url)
        if not platform:
            logger.warning(f"[DownloadService] Noma'lum platforma: {url}")
            return None

        quality_num = quality.replace("p", "")

        t0 = time.time()

        # Yuklash — barcha platformalar uchun yt-dlp
        if audio_only:
            result = await download_video(url, quality_num, audio_only=True)
        else:
            result = await download_video_auto_quality(url, quality_num)

        elapsed = time.time() - t0

        if result is None:
            logger.error(f"[DownloadService] Yuklash muvaffaqiyatsiz ({platform}) — {elapsed:.1f}s: {url}")
            return None

        file_path, info = result
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        logger.info(
            f"[DownloadService] Yuklandi ({platform}, {quality}, "
            f"{file_size_mb:.1f}MB) — {elapsed:.1f}s"
        )

        # Database ga yozish
        if user_id:
            await DownloadService._record_download(
                user_id, platform, url, quality, file_size_mb
            )

        return {
            "file_path": file_path,
            "info": info,
            "platform": platform,
            "file_size_mb": file_size_mb,
            "quality": quality,
        }

    @staticmethod
    async def _record_download(user_id: int, platform: str, url: str,
                               quality: str, file_size_mb: float) -> None:
        """Yuklash natijasini database ga yozish."""
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
            logger.error(f"[DownloadService] Database ga yozish xatosi: {e}")