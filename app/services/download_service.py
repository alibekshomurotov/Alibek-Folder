
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


class DownloadService:
    """Service for handling video download operations"""

    @staticmethod
    async def process_url(url: str) -> Optional[Dict[str, Any]]:
        """
        Process a video URL: extract info and return video details.

        YouTube uchun youtube_api.py ishlatiladi.
        Boshqa platformalar uchun yt-dlp ishlatiladi.

        Returns:
            Dict with 'info', 'platform', 'available_qualities' or None
        """
        platform = detect_platform(url)
        if not platform:
            return None

        info = None

        if platform == "youtube":
            # YouTube uchun youtube_api.py ishlatamiz
            try:
                import youtube_api
                api_result = await youtube_api.get_youtube_info_via_api(url)

                if api_result:
                    source = api_result.get("source", "")

                    if source == "innertube":
                        # InnerTube to'liq info beradi
                        video_details = api_result["data"].get("videoDetails", {})
                        info = {
                            "id": api_result.get("video_id", ""),
                            "title": video_details.get("title", "YouTube Video"),
                            "description": video_details.get("shortDescription", ""),
                            "duration": int(video_details.get("lengthSeconds", 0)),
                            "view_count": int(video_details.get("viewCount", 0)),
                            "uploader": video_details.get("author", ""),
                            "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
                            "webpage_url": url,
                            "extractor": "youtube",
                            "formats": [],
                        }
                    elif source in ("invidious", "piped"):
                        # API natijasini yt-dlp formatiga o'giramiz
                        info = youtube_api.convert_api_info_to_ytdlp(api_result)
                    elif source == "cobalt":
                        # Cobalt faqat URL beradi, basic info
                        info = {
                            "id": api_result.get("video_id", ""),
                            "title": "YouTube Video",
                            "description": "",
                            "duration": 0,
                            "view_count": 0,
                            "uploader": "YouTube",
                            "thumbnail": f"https://img.youtube.com/vi/{api_result.get('video_id', '')}/maxresdefault.jpg",
                            "webpage_url": url,
                            "extractor": "youtube",
                            "formats": [],
                        }

                    if info:
                        logger.info(f"[DownloadService] YouTube info olindi (source={source})")
                        # Barcha sifat variantlarini qo'shamiz
                        if not info.get("formats"):
                            info["formats"] = []
                        # Default quality list
                        return {
                            "info": info,
                            "platform": platform,
                            "available_qualities": ["1080p", "720p", "480p", "360p"],
                            "estimated_size": "N/A",
                        }

            except Exception as e:
                logger.error(f"[DownloadService] youtube_api xatosi: {e}")
                # Fallback to yt-dlp
                logger.info("[DownloadService] yt-dlp fallback...")
                info = await extract_video_info(url)
        else:
            # Boshqa platformalar (TikTok, Instagram, etc.) — yt-dlp
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

        YouTube uchun youtube_api.py ishlatiladi.
        Boshqa platformalar uchun yt-dlp ishlatiladi.

        Returns:
            Dict with 'file_path', 'info', 'platform', 'file_size_mb' or None
        """
        platform = detect_platform(url)
        if not platform:
            return None

        quality_num = quality.replace("p", "")

        if platform == "youtube":
            # YouTube uchun youtube_api.py ishlatamiz
            try:
                import youtube_api
                result = await youtube_api.download_youtube_via_api(
                    url, quality_num, audio_only
                )

                if result:
                    file_path, info = result
                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

                    # Database ga yozish
                    if user_id:
                        await DownloadService._record_download(user_id, platform, url, quality, file_size_mb)

                    return {
                        "file_path": file_path,
                        "info": info,
                        "platform": platform,
                        "file_size_mb": file_size_mb,
                        "quality": quality,
                    }
                else:
                    logger.warning("[DownloadService] youtube_api yuklash muvaffaqiyatsiz")
                    # Fallback to yt-dlp
                    logger.info("[DownloadService] yt-dlp fallback yuklash...")
                    result = await download_video(url, quality_num, audio_only=audio_only)
                    if result:
                        return await DownloadService._process_download_result(result, url, platform, quality, audio_only, user_id)
                    return None

            except Exception as e:
                logger.error(f"[DownloadService] youtube_api download xatosi: {e}")
                # Fallback to yt-dlp
                logger.info("[DownloadService] yt-dlp fallback yuklash...")
                result = await download_video(url, quality_num, audio_only=audio_only)
                if result:
                    return await DownloadService._process_download_result(result, url, platform, quality, audio_only, user_id)
                return None
        else:
            # Boshqa platformalar — yt-dlp
            if audio_only:
                result = await download_video(url, quality_num, audio_only=True)
            else:
                result = await download_video_auto_quality(url, quality_num)

            if result is None:
                return None

            return await DownloadService._process_download_result(result, url, platform, quality, audio_only, user_id)

    @staticmethod
    async def _process_download_result(result: Tuple[str, Dict], url: str, platform: str,
                                        quality: str, audio_only: bool, user_id: int = None) -> Optional[Dict[str, Any]]:
        """yt-dlp natijasini qayta ishlash."""
        file_path, info = result
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if user_id:
            await DownloadService._record_download(user_id, platform, url, quality, file_size_mb)

        return {
            "file_path": file_path,
            "info": info,
            "platform": platform,
            "file_size_mb": file_size_mb,
            "quality": quality,
        }

    @staticmethod
    async def _record_download(user_id: int, platform: str, url: str,
                               quality: str, file_size_mb: float):
        """Download natijasini database ga yozish."""
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