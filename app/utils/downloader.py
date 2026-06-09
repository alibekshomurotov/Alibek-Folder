import os
import logging
import shutil
import subprocess
import tempfile
import time
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

import yt_dlp

from app.config import config, SUPPORTED_PLATFORMS

logger = logging.getLogger(__name__)

# Module-level cookies path cache
_cookies_path: Optional[str] = None

try:
    _yt_dlp_version = yt_dlp.version.__version__
except Exception:
    _yt_dlp_version = "unknown"


def _find_cookies_file() -> Optional[str]:
    """Find cookies.txt in multiple possible locations."""
    global _cookies_path

    if _cookies_path is not None:
        if os.path.exists(_cookies_path):
            return _cookies_path
        _cookies_path = None

    possible_paths = [
        config.download.cookies_file,
        os.path.join(os.getcwd(), "cookies.txt"),
        "/etc/secrets/cookies.txt",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "cookies.txt"),
        "cookies.txt",
    ]

    for path in possible_paths:
        if path and os.path.exists(path):
            logger.info(f"[COOKIES] Found cookies file at: {path}")
            _cookies_path = path
            return path

    cookies_content = os.getenv("YOUTUBE_COOKIES", "").strip()
    if cookies_content:
        try:
            env_cookies_path = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
            with open(env_cookies_path, "w") as f:
                f.write(cookies_content)
            logger.info(f"[COOKIES] Created cookies from env var")
            _cookies_path = env_cookies_path
            return env_cookies_path
        except Exception as e:
            logger.error(f"[COOKIES] Failed to create cookies from env var: {e}")

    logger.warning("[COOKIES] No cookies.txt found!")
    return None


def log_cookies_status() -> None:
    """Log cookies status at startup"""
    logger.info(f"[yt-dlp] Version: {_yt_dlp_version}")
    path = _find_cookies_file()
    if path:
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            yt_cookies = [l for l in lines if "youtube.com" in l.lower() and not l.startswith("#")]
            ig_cookies = [l for l in lines if "instagram.com" in l.lower() and not l.startswith("#")]
            logger.info(f"[COOKIES] YouTube: {len(yt_cookies)} | Instagram: {len(ig_cookies)}")

            cookie_text = "".join(lines)
            critical = ["__Secure-1PSID", "__Secure-3PSID", "SID", "HSID", "SSID", "SAPISID"]
            found = [c for c in critical if c in cookie_text]
            missing = [c for c in critical if c not in cookie_text]
            if found:
                logger.info(f"[COOKIES] Critical found: {found}")
            if missing:
                logger.warning(f"[COOKIES] Critical MISSING: {missing}")

            now = time.time()
            expired = sum(1 for l in lines if not l.startswith("#") and l.strip()
                         and len(l.strip().split("\t")) >= 5
                         and int(l.strip().split("\t")[4]) > 0
                         and int(l.strip().split("\t")[4]) < now)
            if expired:
                logger.warning(f"[COOKIES] {expired} cookies EXPIRED!")
        except Exception as e:
            logger.error(f"[COOKIES] Error: {e}")
    else:
        logger.error("[COOKIES] NO COOKIES FILE!")


def detect_platform(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        for platform_key, platform_info in SUPPORTED_PLATFORMS.items():
            for p_domain in platform_info["domains"]:
                if p_domain in domain:
                    return platform_key
        return None
    except Exception:
        return None


def is_video_url(url: str) -> bool:
    return detect_platform(url) is not None


def get_format_selector(quality: str = "720", audio_only: bool = False) -> str:
    if audio_only:
        if config.download.ffmpeg_available:
            return "bestaudio/best"
        else:
            return "bestaudio[ext=m4a]/bestaudio/best"

    height = quality.replace("p", "")

    if config.download.ffmpeg_available:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"bestvideo+bestaudio/"
            f"best[height<={height}]/"
            f"best"
        )
    else:
        return (
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best[ext=mp4]/"
            f"best"
        )


def _build_opts(output_path: str = None, quality: str = "720",
                audio_only: bool = False, use_cookies: bool = True,
                format_override: str = None) -> Dict[str, Any]:
    if output_path is None:
        output_path = tempfile.mkdtemp()

    fmt = format_override or get_format_selector(quality, audio_only)

    opts = {
        "format": fmt,
        "outtmpl": os.path.join(output_path, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "noplaylist": True,
        "max_filesize": config.download.max_file_size_mb * 1024 * 1024,
    }

    if config.download.ffmpeg_available and "+" in fmt:
        opts["merge_output_format"] = "mp4"

    if use_cookies:
        cookies_path = _find_cookies_file()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    if audio_only and config.download.ffmpeg_available:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    if proxy:
        opts["proxy"] = proxy

    return opts


def _list_formats_debug(url: str, cookies_path: Optional[str]) -> None:
    """Debug: list available formats for a YouTube video"""
    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "listformats": True,
        }
        if cookies_path:
            opts["cookiefile"] = cookies_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=False)
    except Exception as e:
        # yt-dlp prints format list to stdout before raising
        logger.info(f"[YouTube] Format list result: {str(e)[:300]}")


async def extract_video_info(url: str) -> Optional[Dict[str, Any]]:
    """Extract video information without downloading.
    
    CRITICAL: For YouTube, do NOT specify format during extraction!
    Format filtering happens during download, not extraction.
    Specifying format during extraction causes "Requested format is not available"
    on older yt-dlp versions.
    """
    platform = detect_platform(url)
    is_youtube = platform == "youtube"
    cookies_path = _find_cookies_file()
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")

    # Base opts - NO format specification for YouTube!
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
    }
    if proxy:
        opts["proxy"] = proxy

    if not is_youtube:
        # Non-YouTube: add cookies and a permissive format
        if cookies_path:
            opts["cookiefile"] = cookies_path
        opts["format"] = "bestvideo+bestaudio/best"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error(f"Error extracting info from {platform}: {e}")
            return None

    # YouTube: try with cookies first, then without
    # NO FORMAT SPECIFICATION during extraction!
    for use_cookies in [True, False]:
        if use_cookies and not cookies_path:
            continue

        attempt_opts = opts.copy()
        if use_cookies:
            attempt_opts["cookiefile"] = cookies_path
            label = "with cookies"
        else:
            label = "without cookies"

        try:
            logger.info(f"[YouTube] Extract info {label} (no format filter)")
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                logger.info(f"[YouTube] Extract SUCCESS {label}")
                return info
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"[YouTube] Extract {label} failed: {error_msg[:150]}")

            # If format error, try listing available formats for debug
            if "format" in error_msg.lower():
                _list_formats_debug(url, cookies_path if use_cookies else None)

            continue

    logger.error("[YouTube] All extraction attempts failed")
    return None


async def download_video(url: str, quality: str = "720",
                         audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download video/audio and return (file_path, info_dict) or None"""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"
    cookies_path = _find_cookies_file()

    if not is_youtube:
        return await _download_non_youtube(url, quality, audio_only)

    # YouTube download: try different approaches
    output_path = tempfile.mkdtemp()

    # The key issue: on older yt-dlp, certain format strings fail.
    # We try the most compatible approaches first.
    attempts = []

    # Attempt 1: With cookies, no format filter (let yt-dlp choose)
    if cookies_path:
        attempts.append(("cookies, auto format", True, None))

    # Attempt 2: With cookies, quality format
    if cookies_path:
        attempts.append(("cookies, quality format", True, get_format_selector(quality, audio_only)))

    # Attempt 3: With cookies, simple best
    if cookies_path:
        attempts.append(("cookies, best", True, "best"))

    # Attempt 4: Without cookies, auto format
    attempts.append(("no cookies, auto format", False, None))

    # Attempt 5: Without cookies, quality format
    attempts.append(("no cookies, quality format", False, get_format_selector(quality, audio_only)))

    for label, use_cookies, fmt_override in attempts:
        try:
            logger.info(f"[YouTube] Download: {label}, quality={quality}")

            # When fmt_override is None, don't set format - let yt-dlp auto-select
            if fmt_override is None:
                opts = {
                    "outtmpl": os.path.join(output_path, "%(id)s.%(ext)s"),
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": False,
                    "socket_timeout": 30,
                    "retries": 3,
                    "fragment_retries": 3,
                    "file_access_retries": 3,
                    "noplaylist": True,
                    "max_filesize": config.download.max_file_size_mb * 1024 * 1024,
                }
                if use_cookies and cookies_path:
                    opts["cookiefile"] = cookies_path
                if audio_only and config.download.ffmpeg_available:
                    opts["postprocessors"] = [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }]
                proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
                if proxy:
                    opts["proxy"] = proxy
            else:
                opts = _build_opts(output_path, quality, audio_only, use_cookies, fmt_override)

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if info is None:
                    output_path = tempfile.mkdtemp()
                    continue

                file_path = ydl.prepare_filename(info)

                if audio_only and config.download.ffmpeg_available:
                    base_path = os.path.splitext(file_path)[0]
                    mp3_path = base_path + ".mp3"
                    if os.path.exists(mp3_path):
                        file_path = mp3_path

                if not os.path.exists(file_path):
                    files = os.listdir(output_path)
                    if files:
                        file_path = os.path.join(output_path, files[0])
                    else:
                        output_path = tempfile.mkdtemp()
                        continue

                logger.info(f"[YouTube] Download SUCCESS: {label}")
                return file_path, info

        except yt_dlp.utils.MaxDownloadsExceeded:
            logger.warning("File size exceeded maximum")
            return None
        except Exception as e:
            logger.warning(f"[YouTube] Download '{label}' failed: {str(e)[:120]}")
            output_path = tempfile.mkdtemp()
            continue

    logger.error("[YouTube] All download attempts failed")
    return None


async def _download_non_youtube(url: str, quality: str, audio_only: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download from non-YouTube platforms"""
    output_path = tempfile.mkdtemp()

    try:
        opts = _build_opts(output_path, quality, audio_only, use_cookies=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None

            file_path = ydl.prepare_filename(info)
            if audio_only and config.download.ffmpeg_available:
                base_path = os.path.splitext(file_path)[0]
                mp3_path = base_path + ".mp3"
                if os.path.exists(mp3_path):
                    file_path = mp3_path

            if not os.path.exists(file_path):
                files = os.listdir(output_path)
                if files:
                    file_path = os.path.join(output_path, files[0])
                else:
                    return None
            return file_path, info

    except Exception as e:
        logger.error(f"Download error: {e}")
        try:
            output_path2 = tempfile.mkdtemp()
            opts2 = _build_opts(output_path2, quality, audio_only, use_cookies=True)
            opts2["format"] = "best"
            with yt_dlp.YoutubeDL(opts2) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None
                file_path = ydl.prepare_filename(info)
                if not os.path.exists(file_path):
                    files = os.listdir(output_path2)
                    if files:
                        file_path = os.path.join(output_path2, files[0])
                    else:
                        return None
                return file_path, info
        except Exception as fallback_err:
            logger.error(f"Fallback also failed: {fallback_err}")
            return None


async def download_video_auto_quality(url: str, start_quality: str = "720",
                                       audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download with automatic quality reduction if file is too large."""
    quality_levels = ["1080", "720", "480", "360"]
    try:
        start_idx = quality_levels.index(start_quality)
    except ValueError:
        start_idx = 1

    for quality in quality_levels[start_idx:]:
        result = await download_video(url, quality, audio_only)
        if result is None:
            continue
        file_path, info = result
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb <= config.download.max_file_size_mb:
            return result
        logger.info(f"File too large ({file_size_mb:.1f}MB) at {quality}p, trying lower")
        try:
            os.remove(file_path)
        except OSError:
            pass
    return None


def cleanup_file(file_path: str) -> None:
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        dir_path = os.path.dirname(file_path)
        if os.path.exists(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
    except OSError as e:
        logger.warning(f"Could not cleanup file {file_path}: {e}")


def format_duration(seconds: int) -> str:
    if not seconds:
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes: float) -> str:
    if not size_bytes:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_view_count(count: int) -> str:
    if not count:
        return "N/A"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)
