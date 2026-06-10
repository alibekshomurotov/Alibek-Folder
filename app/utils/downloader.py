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
            logger.info(f"[COOKIES] Topildi: {path}")
            _cookies_path = path
            return path

    cookies_content = os.getenv("YOUTUBE_COOKIES", "").strip()
    if cookies_content:
        try:
            env_cookies_path = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
            with open(env_cookies_path, "w") as f:
                f.write(cookies_content)
            logger.info(f"[COOKIES] Env var dan yaratildi")
            _cookies_path = env_cookies_path
            return env_cookies_path
        except Exception as e:
            logger.error(f"[COOKIES] Env var dan yaratish xatosi: {e}")

    logger.warning("[COOKIES] cookies.txt topilmadi!")
    return None


def log_cookies_status() -> None:
    """Ishga tushishda cookie holatini yozish"""
    logger.info(f"[yt-dlp] Versiya: {_yt_dlp_version}")
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
                logger.info(f"[COOKIES] Muhim topildi: {found}")
            if missing:
                logger.warning(f"[COOKIES] Muhim YO'Q: {missing}")

            now = time.time()
            expired = sum(1 for l in lines if not l.startswith("#") and l.strip()
                         and len(l.strip().split("\t")) >= 5
                         and int(l.strip().split("\t")[4]) > 0
                         and int(l.strip().split("\t")[4]) < now)
            if expired:
                logger.warning(f"[COOKIES] {expired} ta cookie MUDDATI O'TGAN!")
        except Exception as e:
            logger.error(f"[COOKIES] Xato: {e}")
    else:
        logger.error("[COOKIES] COOKIE FAYL YO'Q!")


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
    """yt-dlp uchun format tanlash."""
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


def _build_base_opts(use_cookies: bool = True) -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
    }

    if use_cookies:
        cookies_path = _find_cookies_file()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    return opts


def _build_download_opts(output_path: str, quality: str = "720",
                         audio_only: bool = False, use_cookies: bool = True,
                         format_override: str = None) -> Dict[str, Any]:
    opts = _build_base_opts(use_cookies)

    fmt = format_override or get_format_selector(quality, audio_only)
    opts["format"] = fmt
    opts["outtmpl"] = os.path.join(output_path, "%(id)s.%(ext)s")
    opts["extract_flat"] = False
    # DIQQAT: max_filesize OLIB TASHLANDI!
    # yt-dlp ichida MaxDownloadsExceeded atributi yo'q,
    # shu sababli AttributeError chiqardi.
    # Fayl hajmini yuklagandan keyin tekshiramiz.

    if config.download.ffmpeg_available and "+" in fmt:
        opts["merge_output_format"] = "mp4"

    if audio_only and config.download.ffmpeg_available:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    return opts


def _log_formats(info: Dict[str, Any], label: str = "") -> None:
    """Format tafsilotlarini yozish."""
    formats = info.get("formats", [])
    video_formats = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"]
    video_only = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
    audio_only_fmts = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"]

    logger.info(
        f"[FORMATLAR] {label} Jami: {len(formats)}, "
        f"Video: {len(video_formats)}, FaqatVideo: {len(video_only)}, "
        f"FaqatAudio: {len(audio_only_fmts)}"
    )


def _has_video_audio(formats: list) -> bool:
    has_video = any(f.get("vcodec") != "none" for f in formats)
    has_audio = any(f.get("acodec") != "none" for f in formats)
    return has_video or has_audio


# ============================================================
# YOUTUBE: Asosiy strategiya - avval Invidious/Piped API
# ============================================================

async def extract_video_info(url: str) -> Optional[Dict[str, Any]]:
    """Video ma'lumotlarini olish (yuklamasdan)."""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"

    if is_youtube:
        return await _extract_youtube_info(url)

    return await _extract_non_youtube_info(url, platform)


async def _extract_youtube_info(url: str) -> Optional[Dict[str, Any]]:
    """YouTube video ma'lumotlarini olish.

    STRATEGIYA:
    1. Cobalt API - eng tez va ishonchli
    2. Invidious/Piped API - alternative frontendlar
    3. yt-dlp (faqat 2 ta urinish) - oxirgi chora
    """
    # === 1-BOSQICH: Cobalt API (faqat info emas, yuklash uchun) ===
    # Cobalt faqat yuklash URL beradi, info uchun Invidious ishlatamiz

    # === 2-BOSQICH: Invidious/Piped API (info olish uchun) ===
    logger.info("[YouTube] API orqali ma'lumot olinmoqda...")
    try:
        from app.utils.youtube_api import get_youtube_info_via_api, convert_api_info_to_ytdlp
        api_result = await get_youtube_info_via_api(url)
        if api_result:
            info = convert_api_info_to_ytdlp(api_result)
            formats = info.get("formats", [])
            if _has_video_audio(formats):
                logger.info("[YouTube] API orqali MUVOFAQIYATLI!")
                return info
            else:
                logger.warning("[YouTube] API orqali formatlar topilmadi")
    except Exception as e:
        logger.warning(f"[YouTube] API xatosi: {e}")

    # === 3-BOSQICH: yt-dlp (avval proxiesz, keyin proxy bilan) ===
    logger.info("[YouTube] yt-dlp orqali sinab ko'rilmoqda...")
    cookies_path = _find_cookies_file()
    proxy = os.getenv("YOUTUBE_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    proxy_broken = False

    strategies = [
        ("cookies + tv_embedded", True, ["tv_embedded"]),
        ("no-cookies + android", False, ["android"]),
    ]

    for label, use_cookies, player_client in strategies:
        if use_cookies and not cookies_path:
            continue

        # Avval proxiesz, keyin proxy bilan
        for use_proxy in [False, True]:
            if use_proxy and (not proxy or proxy_broken):
                continue

            try:
                opts = {
                    "format": "all",
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": False,
                    "noplaylist": True,
                    "geo_bypass": "US",
                    "geo_bypass_country": "US",
                }

                if use_cookies:
                    opts["cookiefile"] = cookies_path
                if player_client:
                    opts["extractor_args"] = {"youtube": {"player_client": player_client}}
                if use_proxy:
                    opts["proxy"] = proxy

                proxy_label = "proxy" if use_proxy else "proxiesz"
                logger.info(f"[YouTube] yt-dlp: {label} ({proxy_label})")
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        formats = info.get("formats", [])
                        if _has_video_audio(formats):
                            logger.info(f"[YouTube] yt-dlp MUVOFAQIYATLI: {label} ({proxy_label})")
                            return info

            except Exception as e:
                err_str = str(e)
                # Proxy xatosi - keyingi urinishda proxiesz
                if use_proxy and ("ProxyError" in err_str or "Errno 9" in err_str
                                   or "Connection refused" in err_str
                                   or "Errno 111" in err_str):
                    logger.warning(f"[YouTube] Proxy ishlamadi: {err_str[:60]}")
                    proxy_broken = True
                    continue
                logger.debug(f"[YouTube] {label}: {err_str[:80]}")
                continue

    logger.error("[YouTube] Barcha usullar muvaffaqiyatsiz")
    return None


async def _extract_non_youtube_info(url: str, platform: str) -> Optional[Dict[str, Any]]:
    """YouTube bo'lmagan platformalardan ma'lumot olish."""
    cookies_path = _find_cookies_file()

    for use_cookies in [True, False]:
        if use_cookies and not cookies_path:
            continue

        try:
            opts = {
                "format": "all",
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "noplaylist": True,
            }

            if use_cookies:
                opts["cookiefile"] = cookies_path

            proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
            if proxy:
                opts["proxy"] = proxy

            label = "cookies" if use_cookies else "cookiesiz"
            logger.info(f"[{platform}] Ma'lumot olinmoqda: {label}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return info

        except Exception as e:
            logger.warning(f"[{platform}] {label} xato: {str(e)[:100]}")
            continue

    logger.error(f"[{platform}] Barcha urinishlar muvaffaqiyatsiz")
    return None


# ============================================================
# YUKLASH: Video/audio yuklab olish
# ============================================================

async def download_video(url: str, quality: str = "720",
                         audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Video/audio yuklab olish va (file_path, info_dict) qaytarish."""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"

    if not is_youtube:
        return await _download_non_youtube(url, quality, audio_only)

    return await _download_youtube(url, quality, audio_only)


async def _download_youtube(url: str, quality: str = "720",
                             audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube videosini yuklab olish.

    STRATEGIYA:
    1. API orqali yuklash (Cobalt + Invidious + Piped) - faqat 1 marta
    2. yt-dlp - faqat 1 ta urinish (proxy bo'lsa yoki cookies bilan)

    Eslatma: Datacenter IP (Render, Heroku) da YouTube bloklaydi.
    YOUTUBE_PROXY env o'zgaruvchisi bilan residential proxy qo'shing.
    """
    # === 1-BOSQICH: API orqali yuklash (Cobalt + Invidious + Piped) ===
    logger.info("[YouTube] API orqali yuklanmoqda (Cobalt/Invidious/Piped)...")
    try:
        from app.utils.youtube_api import download_youtube_via_api
        result = await download_youtube_via_api(url, quality, audio_only)
        if result:
            logger.info("[YouTube] API orqali yuklash MUVOFAQIYATLI!")
            return result
    except Exception as e:
        logger.warning(f"[YouTube] API yuklash xatosi: {e}")

    # === 2-BOSQICH: yt-dlp orqali yuklash ===
    # MUHIM: Avval PROXIESZ sinaymiz, keyin proxy bilan.
    # Buzilgan proxy barcha so'rovlarni buzadi!
    cookies_path = _find_cookies_file()
    proxy = os.getenv("YOUTUBE_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    proxy_broken = False

    output_path = tempfile.mkdtemp()
    fmt_quality = get_format_selector(quality, audio_only)

    # Bir nechta yt-dlp strategiyasini sinash
    yt_strategies = [
        # 1: best format - eng sodda, ishlash ehtimoli yuqori
        ("best", "best"),
        # 2: format selector bilan
        ("format_selector", fmt_quality),
    ]

    for strat_label, fmt in yt_strategies:
        # Avval proxiesz, keyin proxy bilan
        for use_proxy in [False, True]:
            if use_proxy and (not proxy or proxy_broken):
                continue

            try:
                use_cookies = bool(cookies_path)
                proxy_label = "proxy" if use_proxy else "proxiesz"
                label = f"{strat_label} ({'cookies' if use_cookies else 'no-cookies'}, {proxy_label})"
                logger.info(f"[YouTube] yt-dlp yuklash: {label}")

                opts = _build_download_opts(output_path, quality, audio_only, use_cookies, fmt)

                if use_cookies:
                    opts["extractor_args"] = {"youtube": {"player_client": ["tv_embedded"]}}
                else:
                    opts["extractor_args"] = {"youtube": {"player_client": ["android"]}}

                if use_proxy:
                    opts["proxy"] = proxy
                else:
                    # Proxy ni olib tashlash (agar avval qo'shilgan bo'lsa)
                    opts.pop("proxy", None)

                opts["geo_bypass"] = "US"
                opts["geo_bypass_country"] = "US"

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                    if info is None:
                        logger.warning("[YouTube] yt-dlp info qaytarmadi")
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
                            logger.warning("[YouTube] yt-dlp fayl yaratmadi")
                            continue

                    # Fayl hajmini tekshirish (max_filesize olib tashlangan)
                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    if file_size_mb > config.download.max_file_size_mb:
                        logger.warning(f"[YouTube] Fayl juda katta: {file_size_mb:.1f}MB")
                        try:
                            os.remove(file_path)
                        except OSError:
                            pass
                        return None

                    logger.info(f"[YouTube] yt-dlp yuklash MUVOFAQIYATLI: {label}")
                    return file_path, info

            except AttributeError as e:
                # yt-dlp.utils.MaxDownloadsExceeded yo'q - bu ma'lum xato
                if "MaxDownloads" in str(e):
                    logger.warning("[YouTube] Fayl hajmi cheklovdan oshdi (yt-dlp ichki xatosi)")
                    return None
                logger.warning(f"[YouTube] yt-dlp AttributeError: {str(e)[:100]}")
            except Exception as e:
                err_str = str(e)
                if "MaxDownloads" in str(type(e).__name__) or "MaxDownloads" in err_str:
                    logger.warning("[YouTube] Fayl hajmi cheklovdan oshdi")
                    return None
                # Proxy xatosi - proxyni buzilgan deb belgilash
                if use_proxy and ("ProxyError" in err_str or "Errno 9" in err_str
                                   or "Connection refused" in err_str
                                   or "Errno 111" in err_str):
                    logger.warning(f"[YouTube] Proxy ishlamadi, proxiesz o'tilmoqda")
                    proxy_broken = True
                    continue
                if not use_proxy:
                    # Proxiesz ham ishlamasa - keyingi strategiya
                    logger.debug(f"[YouTube] yt-dlp xato ({strat_label}, proxiesz): {err_str[:80]}")
                    continue
                logger.warning(f"[YouTube] yt-dlp xato ({strat_label}): {err_str[:100]}")

    logger.error("[YouTube] Barcha yuklash usullari muvaffaqiyatsiz")
    return None


async def _download_non_youtube(url: str, quality: str, audio_only: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube bo'lmagan platformalardan yuklab olish."""
    output_path = tempfile.mkdtemp()

    # 1-urinish: cookies bilan
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
        logger.error(f"Yuklash xatosi (cookies bilan): {e}")

    # 2-urinish: format="all/mergeall"
    try:
        output_path2 = tempfile.mkdtemp()
        opts2 = _build_download_opts(output_path2, quality, audio_only, use_cookies=True, format_override="all/mergeall")
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
        logger.warning(f"Yuklash xatosi (all/mergeall): {e}")

    # 3-urinish: cookiesiz
    try:
        output_path3 = tempfile.mkdtemp()
        opts3 = _build_download_opts(output_path3, quality, audio_only, use_cookies=False)
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
    except Exception as e:
        logger.warning(f"Yuklash xatosi (cookiesiz): {e}")

    # 4-urinish: cookies + best
    try:
        output_path4 = tempfile.mkdtemp()
        opts4 = _build_download_opts(output_path4, quality, audio_only, use_cookies=True, format_override="best")
        with yt_dlp.YoutubeDL(opts4) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None
            file_path = ydl.prepare_filename(info)
            if not os.path.exists(file_path):
                files = os.listdir(output_path4)
                if files:
                    file_path = os.path.join(output_path4, files[0])
                else:
                    return None
            return file_path, info
    except Exception as fallback_err:
        logger.error(f"Barcha yuklash urinishlari muvaffaqiyatsiz: {fallback_err}")
        return None


async def download_video_auto_quality(url: str, start_quality: str = "720",
                                       audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Fayl juda katta bo'lsa, sifatni avtomatik pasaytirish."""
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
        logger.info(f"Fayl juda katta ({file_size_mb:.1f}MB) {quality}p, pasaytirilmoqda")
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
        logger.warning(f"Fayl o'chirilmadi {file_path}: {e}")


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
