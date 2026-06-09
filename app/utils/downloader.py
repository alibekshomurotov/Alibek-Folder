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
_yt_dlp_version = yt_dlp.version.__version__


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
        # Cached path no longer exists, reset
        _cookies_path = None

    # Search in multiple locations
    possible_paths = [
        config.download.cookies_file,  # Default path from config
        os.path.join(os.getcwd(), "cookies.txt"),  # Current working directory
        "/etc/secrets/cookies.txt",  # Render Secret Files location
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "cookies.txt"),
        "cookies.txt",  # Relative path
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
    logger.warning("[COOKIES] Searched paths: %s", [p for p in possible_paths if p])
    return None


def log_cookies_status() -> None:
    """Log the current cookies status - call this at startup"""
    logger.info(f"[yt-dlp] Version: {_yt_dlp_version}")
    path = _find_cookies_file()
    if path:
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            # Count YouTube-related cookies
            yt_cookies = [l for l in lines if "youtube.com" in l.lower() and not l.startswith("#")]
            ig_cookies = [l for l in lines if "instagram.com" in l.lower() and not l.startswith("#")]
            logger.info(f"[COOKIES] File: {path} | Total lines: {len(lines)} | YouTube cookies: {len(yt_cookies)} | Instagram cookies: {len(ig_cookies)}")

            # Check for critical YouTube cookies
            cookie_text = "".join(lines)
            critical_cookies = ["__Secure-1PSID", "__Secure-3PSID", "SID", "HSID", "SSID", "SAPISID"]
            found_critical = [c for c in critical_cookies if c in cookie_text]
            missing_critical = [c for c in critical_cookies if c not in cookie_text]

            if found_critical:
                logger.info(f"[COOKIES] YouTube critical cookies found: {found_critical}")
            if missing_critical:
                logger.warning(f"[COOKIES] YouTube critical cookies MISSING: {missing_critical}")
                logger.warning("[COOKIES] YouTube 'Sign in' error likely! Export fresh cookies from browser.")

            # Check if cookies might be expired (look at expiry dates)
            import time as _time
            now = _time.time()
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


def _get_youtube_headers() -> Dict[str, str]:
    """Get custom headers to look more like a real browser for YouTube"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com",
    }


def get_format_selector(quality: str = "720", audio_only: bool = False) -> str:
    """Get yt-dlp format selector based on quality and FFmpeg availability"""
    if audio_only:
        if config.download.ffmpeg_available:
            return "bestaudio/best"
        else:
            return "bestaudio[ext=m4a]/bestaudio/best"

    height = quality.replace("p", "")

    if config.download.ffmpeg_available:
        # With FFmpeg: merge best video + audio, FFmpeg converts to mp4
        # NOTE: Do NOT use [ext=mp4] on bestvideo - YouTube often serves
        # video-only streams as webm; FFmpeg will merge & convert to mp4.
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best[ext=mp4]/"
            f"best"
        )
    else:
        # Without FFmpeg: pre-merged formats only with fallback
        return (
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best[ext=mp4]/"
            f"best"
        )


def get_ydl_opts(quality: str = "720", audio_only: bool = False,
                 output_path: str = None, use_cookies: bool = True) -> Dict[str, Any]:
    """Build yt-dlp options dict"""
    if output_path is None:
        output_path = tempfile.mkdtemp()

    format_selector = get_format_selector(quality, audio_only)

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
    # ensure the final output is mp4 regardless of input format
    if config.download.ffmpeg_available and "+" in format_selector:
        opts["merge_output_format"] = "mp4"

    # Cookie support - only if use_cookies is True
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
        opts["outtmpl"] = os.path.join(output_path, "%(id)s.%(ext)s")

    # Proxy support (optional)
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    if proxy:
        opts["proxy"] = proxy

    return opts


# Player client configurations to try for YouTube
# Phase 1: NO cookies (some clients don't need them, and bad cookies can break requests)
# Phase 2: WITH cookies (for bot detection bypass)
_YT_PLAYER_CLIENTS_NO_COOKIES = [
    ["ios"],                       # iOS client - usually works without cookies
    ["android"],                   # Android client
    ["mweb"],                      # Mobile web
    ["tv_embedded"],               # TV embedded
    ["android_vr"],               # Android VR client
    ["mediaconnect"],              # Media Connect client
]

_YT_PLAYER_CLIENTS_WITH_COOKIES = [
    ["web"],                       # Web client - needs cookies
    ["web", "ios"],                # Web + iOS
    ["tv"],                        # Smart TV client
    ["tv_embedded", "ios"],       # TV embedded + iOS
]


async def extract_video_info(url: str) -> Optional[Dict[str, Any]]:
    """Extract video information without downloading"""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"

    # Base options
    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        "format": "best",
    }

    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    if proxy:
        base_opts["proxy"] = proxy

    if not is_youtube:
        # Non-YouTube: simple extraction with cookies
        cookies_path = _find_cookies_file()
        if cookies_path:
            base_opts["cookiefile"] = cookies_path
        try:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            logger.error(f"Error extracting info from {platform}: {e}")
            return None

    # YouTube: Phase 1 - try WITHOUT cookies first
    # Bad/expired cookies can cause "Failed to extract" on ALL clients
    cookies_path = _find_cookies_file()
    attempt = 0

    logger.info(f"[YouTube] Phase 1: Trying without cookies ({len(_YT_PLAYER_CLIENTS_NO_COOKIES)} clients)")
    for player_clients in _YT_PLAYER_CLIENTS_NO_COOKIES:
        attempt += 1
        opts = base_opts.copy()
        opts["http_headers"] = _get_youtube_headers()
        opts["extractor_args"] = {"youtube": {"player_client": player_clients}}
        # NO cookies in this phase

        try:
            logger.info(f"[YouTube] Extract attempt {attempt} (no cookies) with player_client={player_clients}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                logger.info(f"[YouTube] SUCCESS (no cookies) with player_client={player_clients}")
                return info
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"[YouTube] Attempt {attempt} failed: {error_msg[:120]}")
            continue

    # YouTube: Phase 2 - try WITH cookies
    if cookies_path:
        logger.info(f"[YouTube] Phase 2: Trying with cookies ({len(_YT_PLAYER_CLIENTS_WITH_COOKIES)} clients)")
        for player_clients in _YT_PLAYER_CLIENTS_WITH_COOKIES:
            attempt += 1
            opts = base_opts.copy()
            opts["cookiefile"] = cookies_path
            opts["http_headers"] = _get_youtube_headers()
            opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

            try:
                logger.info(f"[YouTube] Extract attempt {attempt} (with cookies) with player_client={player_clients}")
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    logger.info(f"[YouTube] SUCCESS (with cookies) with player_client={player_clients}")
                    return info
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"[YouTube] Attempt {attempt} failed: {error_msg[:120]}")
                continue
    else:
        logger.warning("[YouTube] No cookies file - skipping Phase 2 (cookies-based attempts)")

    # YouTube: Phase 3 - last resort without extractor_args
    attempt += 1
    logger.info("[YouTube] Phase 3: Last resort - default client without extractor_args")
    opts = base_opts.copy()
    if cookies_path:
        opts["cookiefile"] = cookies_path
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            logger.info("[YouTube] SUCCESS with default client")
            return info
    except Exception as e:
        logger.error(f"[YouTube] All {attempt} attempts failed. Last error: {e}")

    return None


async def download_video(url: str, quality: str = "720",
                         audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Download video/audio and return (file_path, info_dict)

    Returns:
        Tuple of (file_path, info_dict) or None on failure
    """
    platform = detect_platform(url)
    is_youtube = platform == "youtube"
    cookies_path = _find_cookies_file()

    if not is_youtube:
        # Non-YouTube platforms: simple download
        return await _download_non_youtube(url, quality, audio_only, cookies_path)

    # YouTube: try multiple player_client configurations
    # Phase 1: without cookies
    attempt = 0
    output_path = tempfile.mkdtemp()

    for player_clients in _YT_PLAYER_CLIENTS_NO_COOKIES:
        attempt += 1
        try:
            logger.info(f"[YouTube] Download attempt {attempt} (no cookies) player_client={player_clients} quality={quality}")
            opts = get_ydl_opts(quality, audio_only, output_path, use_cookies=False)
            opts["http_headers"] = _get_youtube_headers()
            opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

            result = _do_download(ydl_opts=opts, output_path=output_path, audio_only=audio_only)
            if result:
                logger.info(f"[YouTube] Download SUCCESS (no cookies) with player_client={player_clients}")
                return result
        except yt_dlp.utils.MaxDownloadsExceeded:
            logger.warning("File size exceeded maximum")
            return None
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"[YouTube] Download attempt {attempt} failed: {error_msg[:120]}")
            output_path = tempfile.mkdtemp()  # Fresh dir for next attempt
            continue

    # Phase 2: with cookies
    if cookies_path:
        for player_clients in _YT_PLAYER_CLIENTS_WITH_COOKIES:
            attempt += 1
            try:
                logger.info(f"[YouTube] Download attempt {attempt} (with cookies) player_client={player_clients} quality={quality}")
                opts = get_ydl_opts(quality, audio_only, output_path, use_cookies=True)
                opts["http_headers"] = _get_youtube_headers()
                opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

                result = _do_download(ydl_opts=opts, output_path=output_path, audio_only=audio_only)
                if result:
                    logger.info(f"[YouTube] Download SUCCESS (with cookies) with player_client={player_clients}")
                    return result
            except yt_dlp.utils.MaxDownloadsExceeded:
                logger.warning("File size exceeded maximum")
                return None
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"[YouTube] Download attempt {attempt} failed: {error_msg[:120]}")
                output_path = tempfile.mkdtemp()
                continue

    # Phase 3: default client
    attempt += 1
    logger.info("[YouTube] Phase 3: Last resort - default download")
    try:
        opts = get_ydl_opts(quality, audio_only, output_path, use_cookies=bool(cookies_path))
        result = _do_download(ydl_opts=opts, output_path=output_path, audio_only=audio_only)
        if result:
            return result
    except Exception as e:
        logger.error(f"[YouTube] All {attempt} download attempts failed. Last error: {e}")

    return None


def _do_download(ydl_opts: Dict[str, Any], output_path: str,
                 audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Execute yt-dlp download and return (file_path, info) or None"""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(download=True)

        if info is None:
            return None

        file_path = ydl.prepare_filename(info)

        # For audio with FFmpeg post-processing, extension changes to mp3
        if audio_only and config.download.ffmpeg_available:
            base_path = os.path.splitext(file_path)[0]
            mp3_path = base_path + ".mp3"
            if os.path.exists(mp3_path):
                file_path = mp3_path

        if not os.path.exists(file_path):
            # Try to find any file in the output directory
            files = os.listdir(output_path)
            if files:
                file_path = os.path.join(output_path, files[0])
            else:
                logger.error("Downloaded file not found")
                return None

        return file_path, info


async def _download_non_youtube(url: str, quality: str, audio_only: bool,
                                 cookies_path: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Download from non-YouTube platforms"""
    output_path = tempfile.mkdtemp()

    try:
        opts = get_ydl_opts(quality, audio_only, output_path, use_cookies=True)

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
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        # Simple fallback
        try:
            output_path2 = tempfile.mkdtemp()
            opts2 = get_ydl_opts(quality, audio_only, output_path2, use_cookies=True)
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
            logger.error(f"Fallback download also failed: {fallback_err}")
            return None
    except Exception as e:
        logger.error(f"Unexpected download error: {e}")
        return None


async def download_video_auto_quality(url: str, start_quality: str = "720",
                                       audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Download video with automatic quality reduction if file is too large.
    Tries: start_quality -> lower quality -> even lower -> as document

    Returns:
        Tuple of (file_path, info_dict) or None on failure
    """
    quality_levels = ["1080", "720", "480", "360"]

    # Find starting quality index
    try:
        start_idx = quality_levels.index(start_quality)
    except ValueError:
        start_idx = 1  # Default to 720p

    # Try each quality level from start to lowest
    for quality in quality_levels[start_idx:]:
        result = await download_video(url, quality, audio_only)
        if result is None:
            continue

        file_path, info = result
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if file_size_mb <= config.download.max_file_size_mb:
            return result

        # File too large, try lower quality
        logger.info(f"File too large ({file_size_mb:.1f}MB) at {quality}p, trying lower quality")
        # Clean up
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
        # Try to remove the temp directory
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