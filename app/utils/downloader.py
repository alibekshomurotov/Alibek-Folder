"""Video/Audio Downloader using yt-dlp"""

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

# Module-level cookies path cache (resolved once at startup)
_cookies_path: Optional[str] = None

try:
    _yt_dlp_version = yt_dlp.version.__version__
except Exception:
    _yt_dlp_version = "unknown"


def _find_cookies_file() -> Optional[str]:
    """Find cookies.txt in multiple possible locations.
    Results are cached after first successful find.
    Also supports YOUTUBE_COOKIES env var as fallback.
    """
    global _cookies_path

    # Return cached result if already found
    if _cookies_path is not None:
        if os.path.exists(_cookies_path):
            return _cookies_path
        _cookies_path = None

    # Search in multiple locations
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

    # Try creating cookies from YOUTUBE_COOKIES environment variable
    cookies_content = os.getenv("YOUTUBE_COOKIES", "").strip()
    if cookies_content:
        try:
            env_cookies_path = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
            with open(env_cookies_path, "w") as f:
                f.write(cookies_content)
            logger.info(f"[COOKIES] Created cookies file from YOUTUBE_COOKIES env var at: {env_cookies_path}")
            _cookies_path = env_cookies_path
            return env_cookies_path
        except Exception as e:
            logger.error(f"[COOKIES] Failed to create cookies from env var: {e}")

    logger.warning("[COOKIES] No cookies.txt found! YouTube may not work without cookies.")
    return None


def log_cookies_status() -> None:
    """Log the current cookies status - call this at startup"""
    logger.info(f"[yt-dlp] Version: {_yt_dlp_version}")
    path = _find_cookies_file()
    if path:
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            yt_cookies = [l for l in lines if "youtube.com" in l.lower() and not l.startswith("#")]
            ig_cookies = [l for l in lines if "instagram.com" in l.lower() and not l.startswith("#")]
            logger.info(f"[COOKIES] File: {path} | Total lines: {len(lines)} | YouTube cookies: {len(yt_cookies)} | Instagram cookies: {len(ig_cookies)}")

            cookie_text = "".join(lines)
            critical_cookies = ["__Secure-1PSID", "__Secure-3PSID", "SID", "HSID", "SSID", "SAPISID"]
            found_critical = [c for c in critical_cookies if c in cookie_text]
            missing_critical = [c for c in critical_cookies if c not in cookie_text]

            if found_critical:
                logger.info(f"[COOKIES] YouTube critical cookies found: {found_critical}")
            if missing_critical:
                logger.warning(f"[COOKIES] YouTube critical cookies MISSING: {missing_critical}")

            now = time.time()
            expired_count = 0
            for line in lines:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 5:
                    try:
                        expiry = int(parts[4])
                        if expiry > 0 and expiry < now:
                            expired_count += 1
                    except (ValueError, IndexError):
                        pass
            if expired_count > 0:
                logger.warning(f"[COOKIES] {expired_count} cookies are EXPIRED! Export fresh cookies from browser.")
        except Exception as e:
            logger.error(f"[COOKIES] Error reading cookies file: {e}")
    else:
        logger.error("[COOKIES] NO COOKIES FILE FOUND! YouTube and Instagram will likely fail.")


def detect_platform(url: str) -> Optional[str]:
    """Detect the platform from a URL"""
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
    """Check if URL is a supported video URL"""
    return detect_platform(url) is not None


def get_format_selector(quality: str = "720", audio_only: bool = False) -> str:
    """Get yt-dlp format selector based on quality and FFmpeg availability.
    
    IMPORTANT: YouTube mostly serves adaptive formats (separate video+audio).
    "best" alone fails if no pre-merged format exists.
    We must use "bestvideo+bestaudio/best" as the primary selector.
    """
    if audio_only:
        if config.download.ffmpeg_available:
            return "bestaudio/best"
        else:
            return "bestaudio[ext=m4a]/bestaudio/best"

    height = quality.replace("p", "")

    if config.download.ffmpeg_available:
        # With FFmpeg: merge best video + audio streams
        # bestvideo+bestaudio works even when no pre-merged format exists
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"bestvideo+bestaudio/"
            f"best[height<={height}]/"
            f"best"
        )
    else:
        # Without FFmpeg: pre-merged formats only
        return (
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best[ext=mp4]/"
            f"best"
        )


def _build_ydl_opts(output_path: str = None, quality: str = "720",
                    audio_only: bool = False, use_cookies: bool = True,
                    format_override: str = None) -> Dict[str, Any]:
    """Build yt-dlp options dict"""
    if output_path is None:
        output_path = tempfile.mkdtemp()

    format_selector = format_override or get_format_selector(quality, audio_only)

    opts = {
        "format": format_selector,
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

    # When FFmpeg is available and format uses + (video+audio merge),
    # ensure the final output is mp4
    if config.download.ffmpeg_available and "+" in format_selector:
        opts["merge_output_format"] = "mp4"

    # Cookie support
    if use_cookies:
        cookies_path = _find_cookies_file()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    # Audio-only post-processing
    if audio_only and config.download.ffmpeg_available:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    # Proxy support
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    if proxy:
        opts["proxy"] = proxy

    return opts


# Format strings to try for YouTube (ordered by compatibility)
# The key issue: "best" alone fails when no pre-merged format exists
_YT_FORMAT_STRINGS = [
    "bestvideo+bestaudio/best",     # Merge separate streams, fallback to pre-merged
    "bestvideo+bestaudio",          # Merge separate streams only
    "best",                          # Single pre-merged file (may fail on YouTube)
]


async def extract_video_info(url: str) -> Optional[Dict[str, Any]]:
    """Extract video information without downloading"""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"
    cookies_path = _find_cookies_file()

    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")

    if not is_youtube:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "noplaylist": True,
            "format": "bestvideo+bestaudio/best",
        }
        if cookies_path:
            opts["cookiefile"] = cookies_path
        if proxy:
            opts["proxy"] = proxy
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            logger.error(f"Error extracting info from {platform}: {e}")
            return None

    # YouTube: try multiple format strings with and without cookies
    # The issue: yt-dlp 2025.5.22 has broken extractor_args support
    # Solution: DON'T use extractor_args, just try different format strings
    
    attempts = []

    # Phase 1: With cookies (most likely to work for bot detection bypass)
    if cookies_path:
        for fmt in _YT_FORMAT_STRINGS:
            attempts.append((f"cookies + format={fmt}", {
                "cookiefile": cookies_path,
                "format": fmt,
            }))

    # Phase 2: Without cookies (for public videos)
    for fmt in _YT_FORMAT_STRINGS:
        attempts.append((f"no cookies + format={fmt}", {
            "format": fmt,
        }))

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
    }
    if proxy:
        base_opts["proxy"] = proxy

    for label, extra_opts in attempts:
        opts = base_opts.copy()
        opts.update(extra_opts)
        # Ensure merge_output_format when using +
        if "+" in opts.get("format", "") and config.download.ffmpeg_available:
            opts["merge_output_format"] = "mp4"
        try:
            logger.info(f"[YouTube] Extract: {label}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                logger.info(f"[YouTube] SUCCESS: {label}")
                return info
        except Exception as e:
            logger.warning(f"[YouTube] {label} failed: {str(e)[:120]}")
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

    # YouTube: try multiple strategies
    # Key insight: DON'T use extractor_args (broken in yt-dlp 2025.5.22)
    # Just try different format strings with/without cookies
    
    output_path = tempfile.mkdtemp()
    format_selector = get_format_selector(quality, audio_only)

    strategies = []

    # Strategy 1: Quality-specific format with cookies
    if cookies_path:
        strategies.append(("cookies + quality format", {
            "cookiefile": cookies_path,
            "format": format_selector,
        }))

    # Strategy 2: bestvideo+bestaudio with cookies (no height limit)
    if cookies_path:
        strategies.append(("cookies + bestvideo+bestaudio", {
            "cookiefile": cookies_path,
            "format": "bestvideo+bestaudio/best",
        }))

    # Strategy 3: Without cookies, quality-specific
    strategies.append(("no cookies + quality format", {
        "format": format_selector,
    }))

    # Strategy 4: Without cookies, bestvideo+bestaudio
    strategies.append(("no cookies + bestvideo+bestaudio", {
        "format": "bestvideo+bestaudio/best",
    }))

    # Strategy 5: Absolute fallback
    strategies.append(("fallback: best", {
        "format": "best",
    }))

    for label, extra_opts in strategies:
        try:
            logger.info(f"[YouTube] Download: {label}, quality={quality}")
            opts = _build_ydl_opts(
                output_path=output_path,
                quality=quality,
                audio_only=audio_only,
                use_cookies=("cookies" in label),
                format_override=extra_opts.get("format"),
            )
            if "cookiefile" in extra_opts:
                opts["cookiefile"] = extra_opts["cookiefile"]

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
            error_msg = str(e)
            logger.warning(f"[YouTube] Download '{label}' failed: {error_msg[:120]}")
            output_path = tempfile.mkdtemp()
            continue

    logger.error("[YouTube] All download attempts failed")
    return None


async def _download_non_youtube(url: str, quality: str, audio_only: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download from non-YouTube platforms"""
    output_path = tempfile.mkdtemp()

    try:
        opts = _build_ydl_opts(output_path=output_path, quality=quality, audio_only=audio_only, use_cookies=True)

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
                    logger.error("Downloaded file not found")
                    return None

            return file_path, info

    except yt_dlp.utils.MaxDownloadsExceeded:
        logger.warning("File size exceeded maximum")
        return None
    except Exception as e:
        logger.error(f"Download error: {e}")
        try:
            output_path2 = tempfile.mkdtemp()
            opts2 = _build_ydl_opts(output_path=output_path2, quality=quality, audio_only=audio_only, use_cookies=True)
            opts2["format"] = "bestvideo+bestaudio/best"
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
            logger.error(f"Fallback download also failed: {fallback_err}")
            return None


async def download_video_auto_quality(url: str, start_quality: str = "720",
                                       audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download video with automatic quality reduction if file is too large."""
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

        logger.info(f"File too large ({file_size_mb:.1f}MB) at {quality}p, trying lower quality")
        try:
            os.remove(file_path)
        except OSError:
            pass

    return None


def cleanup_file(file_path: str) -> None:
    """Remove downloaded file and its parent directory if empty"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        dir_path = os.path.dirname(file_path)
        if os.path.exists(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
    except OSError as e:
        logger.warning(f"Could not cleanup file {file_path}: {e}")


def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string"""
    if not seconds:
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes: float) -> str:
    """Format file size in bytes to human readable string"""
    if not size_bytes:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_view_count(count: int) -> str:
    """Format view count to human readable string"""
    if not count:
        return "N/A"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)
