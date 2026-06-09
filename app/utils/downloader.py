

import os
import logging
import shutil
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
    """Get format selector string for yt-dlp.

    For YouTube with FFmpeg: use bestvideo+bestaudio (adaptive streams that need merging)
    For YouTube without FFmpeg: use best (pre-merged formats only)
    For audio only: bestaudio
    """
    if audio_only:
        if config.download.ffmpeg_available:
            return "bestaudio/best"
        else:
            return "bestaudio[ext=m4a]/bestaudio/best"

    height = quality.replace("p", "")

    if config.download.ffmpeg_available:
        # With FFmpeg, we can merge separate video+audio streams
        # This is needed for YouTube which mostly serves adaptive streams
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"bestvideo+bestaudio/"
            f"best[height<={height}]/"
            f"best"
        )
    else:
        # Without FFmpeg, we need pre-merged formats
        return (
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best[ext=mp4]/"
            f"best"
        )


def _build_base_opts(use_cookies: bool = True) -> Dict[str, Any]:
    """Build base yt-dlp options shared between extraction and download."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
    }

    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    if proxy:
        opts["proxy"] = proxy

    if use_cookies:
        cookies_path = _find_cookies_file()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    return opts


def _build_download_opts(output_path: str, quality: str = "720",
                         audio_only: bool = False, use_cookies: bool = True,
                         format_override: str = None) -> Dict[str, Any]:
    """Build yt-dlp options for downloading."""
    opts = _build_base_opts(use_cookies)

    fmt = format_override or get_format_selector(quality, audio_only)
    opts["format"] = fmt
    opts["outtmpl"] = os.path.join(output_path, "%(id)s.%(ext)s")
    opts["extract_flat"] = False
    opts["max_filesize"] = config.download.max_file_size_mb * 1024 * 1024

    if config.download.ffmpeg_available and "+" in fmt:
        opts["merge_output_format"] = "mp4"

    if audio_only and config.download.ffmpeg_available:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    return opts


async def extract_video_info(url: str) -> Optional[Dict[str, Any]]:

    platform = detect_platform(url)
    is_youtube = platform == "youtube"
    cookies_path = _find_cookies_file()

    # Base opts - NO format for YouTube, permissive format for others
    base_opts = _build_base_opts(use_cookies=True)
    base_opts["extract_flat"] = False

    if is_youtube:
        # YouTube: Try with cookies first (needed for bot detection),
        # then without cookies as fallback
        for use_cookies in [True, False]:
            if use_cookies and not cookies_path:
                continue

            attempt_opts = base_opts.copy()
            if use_cookies:
                attempt_opts["cookiefile"] = cookies_path
                label = "with cookies"
            else:
                attempt_opts.pop("cookiefile", None)
                label = "without cookies"

            try:
                logger.info(f"[YouTube] Extract info {label} (no format filter)")
                with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        formats = info.get("formats", [])
                        video_formats = [f for f in formats
                                        if f.get("vcodec") != "none" and f.get("acodec") != "none"]
                        video_only = [f for f in formats
                                     if f.get("vcodec") != "none" and f.get("acodec") == "none"]
                        audio_only = [f for f in formats
                                     if f.get("vcodec") == "none" and f.get("acodec") != "none"]
                        logger.info(
                            f"[YouTube] Extract SUCCESS {label} - "
                            f"Total: {len(formats)}, Video: {len(video_formats)}, "
                            f"VideoOnly: {len(video_only)}, AudioOnly: {len(audio_only)}"
                        )
                        return info
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"[YouTube] Extract {label} failed: {error_msg[:200]}")
                continue

        logger.error("[YouTube] All extraction attempts failed")
        return None

    else:
        # Non-YouTube platforms: add format for compatibility
        base_opts["format"] = "bestvideo+bestaudio/best"
        try:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            logger.error(f"Error extracting info from {platform}: {e}")
            # Try without format as fallback
            try:
                fallback_opts = _build_base_opts(use_cookies=True)
                fallback_opts["extract_flat"] = False
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except Exception as e2:
                logger.error(f"Fallback extract also failed for {platform}: {e2}")
                return None


async def download_video(url: str, quality: str = "720",
                         audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download video/audio and return (file_path, info_dict) or None"""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"

    if not is_youtube:
        return await _download_non_youtube(url, quality, audio_only)

    return await _download_youtube(url, quality, audio_only)


async def _download_youtube(url: str, quality: str = "720",
                             audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download from YouTube with multiple fallback strategies.

    Strategy:
    1. With cookies + quality-specific format (best approach for new yt-dlp)
    2. With cookies + no format (let yt-dlp auto-select)
    3. With cookies + simple "best" format
    4. Without cookies + quality format (may hit bot detection)
    """
    cookies_path = _find_cookies_file()
    output_path = tempfile.mkdtemp()

    fmt_quality = get_format_selector(quality, audio_only)

    attempts = []

    # With cookies - multiple format strategies
    if cookies_path:
        # Attempt 1: Quality-specific format (best for new yt-dlp with proper format parsing)
        attempts.append(("cookies + quality format", True, fmt_quality))
        # Attempt 2: No format filter - let yt-dlp choose
        attempts.append(("cookies + auto select", True, None))
        # Attempt 3: Simple "best" as last resort with cookies
        attempts.append(("cookies + best", True, "best"))

    # Without cookies - may hit bot detection
    attempts.append(("no cookies + quality format", False, fmt_quality))
    attempts.append(("no cookies + best", False, "best"))

    for label, use_cookies, fmt_override in attempts:
        try:
            logger.info(f"[YouTube] Download attempt: {label}")

            if fmt_override is None:
                # No format specified - let yt-dlp auto-select
                opts = _build_base_opts(use_cookies)
                opts["outtmpl"] = os.path.join(output_path, "%(id)s.%(ext)s")
                opts["extract_flat"] = False
                opts["max_filesize"] = config.download.max_file_size_mb * 1024 * 1024
                if audio_only and config.download.ffmpeg_available:
                    opts["postprocessors"] = [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }]
            else:
                opts = _build_download_opts(output_path, quality, audio_only, use_cookies, fmt_override)

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
            logger.warning(f"[YouTube] Download '{label}' failed: {str(e)[:150]}")
            output_path = tempfile.mkdtemp()
            continue

    logger.error("[YouTube] All download attempts failed")
    return None


async def _download_non_youtube(url: str, quality: str, audio_only: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download from non-YouTube platforms (TikTok, Instagram, etc.)

    Uses cookies for platforms that require authentication (Instagram Stories, etc.)
    """
    output_path = tempfile.mkdtemp()

    # Primary attempt: with cookies and quality format
    try:
        opts = _build_download_opts(output_path, quality, audio_only, use_cookies=True)
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
        logger.error(f"Download error (with cookies): {e}")

    # Fallback 1: without cookies, quality format
    try:
        output_path2 = tempfile.mkdtemp()
        opts2 = _build_download_opts(output_path2, quality, audio_only, use_cookies=False)
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
    except Exception as e:
        logger.warning(f"Download error (no cookies): {e}")

    # Fallback 2: with cookies, simple "best"
    try:
        output_path3 = tempfile.mkdtemp()
        opts3 = _build_download_opts(output_path3, quality, audio_only, use_cookies=True, format_override="best")
        with yt_dlp.YoutubeDL(opts3) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None
            file_path = ydl.prepare_filename(info)
            if not os.path.exists(file_path):
                files = os.listdir(output_path3)
                if files:
                    file_path = os.path.join(output_path3, files[0])
                else:
                    return None
            return file_path, info
    except Exception as fallback_err:
        logger.error(f"All non-YouTube download attempts failed: {fallback_err}")
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
