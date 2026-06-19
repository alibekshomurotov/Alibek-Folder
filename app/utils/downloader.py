import os
import logging
import re
import shutil
import subprocess
import tempfile
import time
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse

import yt_dlp

from app.config import config, SUPPORTED_PLATFORMS

logger = logging.getLogger(__name__)

# Module-level cookies path cache
_cookies_path: Optional[str] = None

# Bot detection xatosi sanagichi
_bot_detection_count = 0
_bot_detection_threshold = 3

try:
    _yt_dlp_version = yt_dlp.version.__version__
except Exception:
    _yt_dlp_version = "unknown"


# ============================================================
# PROXY: youtube_api.siz to'g'ridan-to'g'ri env var dan
# ============================================================

def _get_youtube_proxy() -> str:
    """YouTube uchun proxy olish (faqat env var, youtube_api emas)."""
    return (
        os.getenv("YOUTUBE_PROXY", "")
        or os.getenv("HTTP_PROXY", "")
        or os.getenv("HTTPS_PROXY", "")
    )


def _get_platform_proxy(platform: str) -> str:
    """Platformaga mos proxy olish.

    YouTube   → YOUTUBE_PROXY (SOCKS5, datacenter IP blokirovkasi uchun)
    Instagram → INSTAGRAM_PROXY (ixtiyoriy)
    Boshqa    → proxy yo'q
    """
    if platform == "youtube":
        return _get_youtube_proxy()
    if platform == "instagram":
        return os.getenv("INSTAGRAM_PROXY", "")
    return ""


# ============================================================
# COOKIES: topish, parse qilish, tekshirish
# ============================================================

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


def _parse_cookies(cookies_path: str) -> Dict[str, List[Dict[str, str]]]:
    """cookies.txt faylini o'qib, domen bo'yicha cookie'larni qaytarish."""
    result = {}
    try:
        with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in lines:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) < 7:
                continue
            domain = parts[0].lower()
            name = parts[5].strip()
            value = parts[6].strip()
            if not name or not value:
                continue
            if domain not in result:
                result[domain] = []
            result[domain].append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": parts[2] if len(parts) > 2 else "/",
            })
    except Exception as e:
        logger.error(f"[COOKIES] Parse xatosi: {e}")
    return result


def _validate_instagram_cookies(cookies_path: str) -> Dict[str, Any]:
    """Instagram cookie'larini tekshirish - story uchun muhim."""
    cookies_by_domain = _parse_cookies(cookies_path)

    ig_cookies = {}
    for domain, cookies in cookies_by_domain.items():
        if "instagram.com" in domain:
            for c in cookies:
                ig_cookies[c["name"]] = c["value"]

    critical_cookies = ["sessionid", "ds_user_id", "csrftoken"]
    useful_cookies = ["ig_did", "mid", "ig_nrcb", "rur", "shbid"]

    found_critical = [c for c in critical_cookies if c in ig_cookies]
    missing_critical = [c for c in critical_cookies if c not in ig_cookies]
    found_useful = [c for c in useful_cookies if c in ig_cookies]

    is_valid = len(missing_critical) == 0

    result = {
        "valid": is_valid,
        "missing": missing_critical,
        "found_critical": found_critical,
        "found_useful": found_useful,
        "total_ig_cookies": len(ig_cookies),
        "ig_cookies": ig_cookies,
    }

    if is_valid:
        logger.info(f"[IG-COOKIES] Cookie'lar YAXSHI! Kritik: {found_critical}, Qo'shimcha: {found_useful}, Jami: {len(ig_cookies)}")
    else:
        logger.error(f"[IG-COOKIES] YETISHMAYAPTI: {missing_critical}! Bor: {found_critical}, Jami: {len(ig_cookies)}")

    return result


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

            ig_validation = _validate_instagram_cookies(path)
            if not ig_validation["valid"]:
                logger.warning(
                    f"[COOKIES] Instagram Story uchun cookie'lar YETARLI EMAS! "
                    f"Yetishmayotgan: {ig_validation['missing']}."
                )

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

    po_token = os.getenv("PO_TOKEN", "")
    if po_token:
        logger.info(f"[PO_TOKEN] Mavjud ({len(po_token)} belgi)")
    else:
        logger.warning("[PO_TOKEN] O'RNATILMAGAN! YouTube bot detektsiyasini chetlab o'tish uchun kerak.")

    visitor_data = os.getenv("VISITOR_DATA", "")
    if visitor_data:
        logger.info(f"[VISITOR_DATA] Mavjud ({len(visitor_data)} belgi)")
    else:
        logger.info("[VISITOR_DATA] O'rnatilmagan (ixtiyoriy)")


# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================

def _is_bot_detection_error(error: Exception) -> bool:
    err_str = str(error).lower()
    bot_keywords = [
        "sign in to confirm",
        "not a bot",
        "bot detection",
        "captcha",
        "age verification",
    ]
    return any(kw in err_str for kw in bot_keywords)


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


def _is_youtube(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        return any(d in domain for d in ["youtube.com", "youtu.be"])
    except Exception:
        return False


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


def _log_formats(info: Dict[str, Any], label: str = "") -> None:
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
# YT-DLP OPTS QURISH (youtube_api.siz)
# ============================================================

def _build_base_opts(use_cookies: bool = True, platform: str = "youtube") -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # === Tuzatildi: 8 → 120 (YouTube API timeout oldini olish) ===
        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "throttledratelimit": 100000,
        "concurrent_fragment_downloads": 8,
        "buffersize": 16384,
        "http_chunk_size": 10485760,
    }

    if use_cookies:
        cookies_path = _find_cookies_file()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    # YouTube: PO_TOKEN qo'shish
    if platform == "youtube":
        po_token = os.getenv("PO_TOKEN", "")
        if po_token:
            opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = f"web+{po_token}"

    # Proxy — platformaga mos, to'g'ridan-to'g'ri env var dan
    # MUHIM: yt-dlp HTTP_PROXY/HTTPS_PROXY env ni avtomatik o'qiydi!
    # Proxy ishlatmaslik uchun opts["proxy"] = "" qilish kerak (None emas!)
    proxy = _get_platform_proxy(platform)
    if proxy:
        opts["proxy"] = proxy
        logger.debug(f"[yt-dlp] {platform} proxy: {proxy.split('@')[-1]}")
    else:
        opts["proxy"] = ""  # env dan proxy olmaslik uchun

    return opts


def _build_download_opts(output_path: str, quality: str = "720",
                         audio_only: bool = False, use_cookies: bool = True,
                         format_override: str = None, platform: str = "youtube") -> Dict[str, Any]:
    opts = _build_base_opts(use_cookies, platform)

    fmt = format_override or get_format_selector(quality, audio_only)
    opts["format"] = fmt
    opts["outtmpl"] = os.path.join(output_path, "%(id)s.%(ext)s")
    opts["extract_flat"] = False

    if config.download.ffmpeg_available and "+" in fmt:
        opts["merge_output_format"] = "mp4"

    if audio_only and config.download.ffmpeg_available:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    return opts


# ============================================================
# INSTAGRAM: To'g'ridan-to'g'ri API orqali story yuklash
# ============================================================

async def _download_instagram_story_api(url: str, cookies_path: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Instagram story'ni to'g'ridan-to'g'ri API orqali yuklash.

    Strategiya (yangilangan — 429 rate limit bilan kurashish):
    1. Web API (www.instagram.com/api/v1/) — birinchi urinish
    2. Mobile API (i.instagram.com/api/v1/) — 429 bo'lsa fallback
    3. Sahifa orqali user_id olish — ikkala API ham ishlamasa
    """
    import http.cookiejar
    import aiohttp
    import asyncio

    story_match = re.search(r'/stories/([^/]+)(?:/(\d+))?/?', url)
    if not story_match:
        logger.error("[IG-API] URL dan username topilmadi")
        return None

    username = story_match.group(1)
    story_id = story_match.group(2)

    logger.info(f"[IG-API] Story yuklanmoqda: username={username}, story_id={story_id}")

    ig_validation = _validate_instagram_cookies(cookies_path)
    if not ig_validation["valid"]:
        logger.error(f"[IG-API] Cookie'lar yetarli emas! Yetishmayotgan: {ig_validation['missing']}")
        return None

    ig_cookies = ig_validation["ig_cookies"]
    sessionid = ig_cookies.get("sessionid", "")
    ds_user_id = ig_cookies.get("ds_user_id", "")
    csrftoken = ig_cookies.get("csrftoken", "")

    cookie_str = "; ".join(f"{k}={v}" for k, v in ig_cookies.items())

    # === 1-USUL: Web API (www.instagram.com) ===
    web_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "X-IG-App-ID": "936619743392459",
        "X-CSRFToken": csrftoken,
        "X-Instagram-AJAX": "1",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{username}/",
        "Cookie": cookie_str,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    # === 2-USUL: Mobile API (i.instagram.com) ===
    mobile_headers = {
        "User-Agent": "Instagram 312.1.0.34.111 (iPhone; iOS 16_6; en_US; iPhone14,2; scale=3.00; 1080x2340; 530840967)",
        "X-IG-App-ID": "567067343352427",
        "X-IG-Device-ID": ig_cookies.get("ig_did", "00000000-0000-0000-0000-000000000000"),
        "X-IG-Android-ID": "android-" + ds_user_id[-16:] if ds_user_id else "",
        "X-CSRFToken": csrftoken,
        "Cookie": cookie_str,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-Connection-Type": "WIFI",
        "X-IG-Capabilities": "3brTvw==",
        "X-IG-App-Startup-Country": "US",
    }

    user_id = None
    story_items = None

    # === USER ID OLISH — 3 xil usul ===
    async with aiohttp.ClientSession(headers=web_headers) as web_session:
        try:
            user_api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            async with web_session.get(user_api_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 429:
                    logger.warning("[IG-API] Web API 429 rate limit — mobile API sinab ko'rilmoqda...")
                elif resp.status == 200:
                    user_data = await resp.json()
                    user_id = user_data.get("data", {}).get("user", {}).get("id")
                    if user_id:
                        logger.info(f"[IG-API] Web API: User ID={user_id}")
                else:
                    logger.warning(f"[IG-API] Web API user ID xatosi: status={resp.status}")
        except Exception as e:
            logger.warning(f"[IG-API] Web API user ID xatosi: {e}")

    if not user_id:
        async with aiohttp.ClientSession(headers=mobile_headers) as mobile_session:
            try:
                await asyncio.sleep(0.5)
                mobile_user_url = f"https://i.instagram.com/api/v1/users/{username}/usernameinfo/"
                async with mobile_session.get(mobile_user_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        user_data = await resp.json()
                        user_id = user_data.get("user", {}).get("pk") or user_data.get("user", {}).get("id")
                        if user_id:
                            logger.info(f"[IG-API] Mobile API: User ID={user_id}")
                    elif resp.status == 429:
                        logger.warning("[IG-API] Mobile API ham 429 — kutib qayta urinamiz...")
                        await asyncio.sleep(2)
                        async with mobile_session.get(mobile_user_url, timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                            if resp2.status == 200:
                                user_data = await resp2.json()
                                user_id = user_data.get("user", {}).get("pk") or user_data.get("user", {}).get("id")
                                if user_id:
                                    logger.info(f"[IG-API] Mobile API retry: User ID={user_id}")
                    else:
                        logger.warning(f"[IG-API] Mobile API user ID xatosi: status={resp.status}")
            except Exception as e:
                logger.warning(f"[IG-API] Mobile API user ID xatosi: {e}")

    if not user_id:
        logger.info("[IG-API] API lar ishlamadi — sahifa orqali user_id olinmoqda...")
        user_id = await _get_instagram_user_id_from_page(username, web_headers)
        if not user_id:
            logger.error("[IG-API] Barcha usullar bilan user_id olib bo'lmadi")
            return None

    logger.info(f"[IG-API] User ID: {user_id}")

    # === STORY LARNI OLISH — 2 xil API ===
    async with aiohttp.ClientSession(headers=web_headers) as web_session:
        try:
            story_api_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/story/"
            async with web_session.get(story_api_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    story_data = await resp.json()
                    story_items = story_data.get("items", [])
                    if story_items:
                        logger.info(f"[IG-API] Web API: {len(story_items)} ta story topildi")
                elif resp.status == 429:
                    logger.warning("[IG-API] Story Web API 429 — mobile API sinab ko'rilmoqda...")
        except Exception as e:
            logger.warning(f"[IG-API] Story Web API xatosi: {e}")

    if not story_items:
        async with aiohttp.ClientSession(headers=mobile_headers) as mobile_session:
            try:
                await asyncio.sleep(0.5)
                mobile_story_url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/story/"
                async with mobile_session.get(mobile_story_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        story_data = await resp.json()
                        story_items = story_data.get("items", [])
                        if story_items:
                            logger.info(f"[IG-API] Mobile API: {len(story_items)} ta story topildi")
                    elif resp.status == 429:
                        await asyncio.sleep(3)
                        async with mobile_session.get(mobile_story_url, timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                            if resp2.status == 200:
                                story_data = await resp2.json()
                                story_items = story_data.get("items", [])
                                if story_items:
                                    logger.info(f"[IG-API] Mobile API retry: {len(story_items)} ta story topildi")
            except Exception as e:
                logger.warning(f"[IG-API] Story Mobile API xatosi: {e}")

    if not story_items:
        logger.error("[IG-API] Story topilmadi (bo'sh, faol emas, yoki barcha API 429)")
        return None

    target_item = None
    if story_id:
        for item in story_items:
            item_id = str(item.get("id", ""))
            if item_id.startswith(story_id) or story_id in item_id:
                target_item = item
                break

    if not target_item and story_items:
        if story_id:
            logger.warning(f"[IG-API] Story ID {story_id} topilmadi, oxirgi story olinmoqda")
        target_item = story_items[-1]

    if not target_item:
        logger.error("[IG-API] Hech qanday story topilmadi")
        return None

    return await _download_story_item(target_item, username)


async def _get_instagram_user_id_from_page(username: str, headers: Dict[str, str]) -> Optional[str]:
    """Instagram sahifasidan user_id ajratib olish."""
    import aiohttp

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                f"https://www.instagram.com/{username}/",
                timeout=aiohttp.ClientTimeout(total=8),
                allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[IG-PAGE] Sahifa ochish xatosi: status={resp.status}")
                    return None

                html = await resp.text()

                user_id_match = re.search(r'"user_id"\s*:\s*"(\d+)"', html)
                if user_id_match:
                    uid = user_id_match.group(1)
                    logger.info(f"[IG-PAGE] user_id topildi: {uid}")
                    return uid

                id_match = re.search(r'"id"\s*:\s*"(\d{10,})"', html)
                if id_match:
                    uid = id_match.group(1)
                    logger.info(f"[IG-PAGE] id topildi: {uid}")
                    return uid

                profile_match = re.search(r'profilePage_(\d+)', html)
                if profile_match:
                    uid = profile_match.group(1)
                    logger.info(f"[IG-PAGE] profilePage id topildi: {uid}")
                    return uid

                owner_match = re.search(r'"owner"\s*:\s*\{[^}]*"id"\s*:\s*"(\d+)"', html)
                if owner_match:
                    uid = owner_match.group(1)
                    logger.info(f"[IG-PAGE] owner.id topildi: {uid}")
                    return uid

                logger.warning("[IG-PAGE] Sahifadan user_id topilmadi")
                return None

    except Exception as e:
        logger.error(f"[IG-PAGE] Sahifa yuklash xatosi: {e}")
        return None


async def _download_instagram_story_page(url: str, cookies_path: str,
                                          ig_cookies: Dict[str, str],
                                          headers: Dict[str, str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Instagram story'ni sahifa orqali yuklash - 2-usul fallback."""
    import aiohttp

    logger.info("[IG-PAGE] Story sahifa orqali yuklash boshlandi")

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.error(f"[IG-PAGE] Sahifa ochish xatosi: status={resp.status}")
                    return None

                html = await resp.text()

                video_urls = re.findall(r'"video_url"\s*:\s*"([^"]+)"', html)
                if video_urls:
                    video_url = video_urls[0].replace("\\u0026", "&")
                    logger.info(f"[IG-PAGE] Video URL topildi!")
                    return await _download_media_url(session, video_url, "story_video", "mp4")

                image_urls = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html)
                if image_urls:
                    image_url = image_urls[0].replace("\\u0026", "&")
                    logger.info(f"[IG-PAGE] Image URL topildi!")
                    return await _download_media_url(session, image_url, "story_image", "jpg")

                og_video = re.findall(r'<meta\s+property="og:video(?::secure_url)?"\s+content="([^"]+)"', html)
                if og_video:
                    video_url = og_video[0].replace("&amp;", "&")
                    logger.info(f"[IG-PAGE] OG Video URL topildi!")
                    return await _download_media_url(session, video_url, "story_video", "mp4")

                cdn_urls = re.findall(r'(https?://[^"]*fbcdn\.net[^"]*\.mp4[^"]*)', html)
                if cdn_urls:
                    video_url = cdn_urls[0].replace("\\u0026", "&").replace("&amp;", "&")
                    logger.info(f"[IG-PAGE] CDN Video URL topildi!")
                    return await _download_media_url(session, video_url, "story_video", "mp4")

                logger.error("[IG-PAGE] Sahifadan media URL topilmadi")
                return None

        except Exception as e:
            logger.error(f"[IG-PAGE] Sahifa yuklash xatosi: {e}")
            return None


async def _download_story_item(item: dict, username: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Instagram story itemdan video/rasm yuklash."""
    item_id = str(item.get("id", "unknown"))
    media_type = item.get("media_type", 0)

    info = {
        "id": item_id,
        "title": f"Instagram Story - @{username}",
        "uploader": username,
        "uploader_id": item.get("user", {}).get("pk", ""),
        "extractor": "instagram",
        "extractor_key": "Instagram",
        "webpage_url": f"https://www.instagram.com/stories/{username}/{item_id.split('_')[0]}/",
        "duration": item.get("video_duration", 0) if media_type == 2 else 0,
    }

    if media_type == 2:
        video_versions = item.get("video_versions", [])
        if not video_versions:
            logger.error("[IG-API] Video versions topilmadi")
            return None

        best_video = max(video_versions, key=lambda v: v.get("width", 0) * v.get("height", 0))
        video_url = best_video.get("url", "")
        if not video_url:
            logger.error("[IG-API] Video URL bo'sh")
            return None

        info["width"] = best_video.get("width", 0)
        info["height"] = best_video.get("height", 0)
        info["ext"] = "mp4"

        output_path = tempfile.mkdtemp()
        file_path = os.path.join(output_path, f"{item_id}.mp4")

        logger.info(f"[IG-API] Video yuklanmoqda: {best_video.get('width')}x{best_video.get('height')}")

        try:
            import aiohttp
            async with aiohttp.ClientSession() as dl_session:
                async with dl_session.get(video_url) as resp:
                    if resp.status != 200:
                        logger.error(f"[IG-API] Video yuklash xatosi: status={resp.status}")
                        return None
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)

            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error("[IG-API] Yuklangan fayl bo'sh")
                return None

            logger.info(f"[IG-API] Video yuklandi: {file_size / 1024:.1f} KB")
            return file_path, info

        except Exception as e:
            logger.error(f"[IG-API] Video yuklash xatosi: {e}")
            return None

    elif media_type == 1:
        image_versions = item.get("image_versions2", {}).get("candidates", [])
        if not image_versions:
            logger.error("[IG-API] Image versions topilmadi")
            return None

        best_image = max(image_versions, key=lambda v: v.get("width", 0) * v.get("height", 0))
        image_url = best_image.get("url", "")
        if not image_url:
            logger.error("[IG-API] Image URL bo'sh")
            return None

        info["width"] = best_image.get("width", 0)
        info["height"] = best_image.get("height", 0)
        info["ext"] = "jpg"

        output_path = tempfile.mkdtemp()
        file_path = os.path.join(output_path, f"{item_id}.jpg")

        logger.info(f"[IG-API] Rasm yuklanmoqda: {best_image.get('width')}x{best_image.get('height')}")

        try:
            import aiohttp
            async with aiohttp.ClientSession() as dl_session:
                async with dl_session.get(image_url) as resp:
                    if resp.status != 200:
                        logger.error(f"[IG-API] Rasm yuklash xatosi: status={resp.status}")
                        return None
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)

            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error("[IG-API] Yuklangan fayl bo'sh")
                return None

            logger.info(f"[IG-API] Rasm yuklandi: {file_size / 1024:.1f} KB")
            return file_path, info

        except Exception as e:
            logger.error(f"[IG-API] Rasm yuklash xatosi: {e}")
            return None

    else:
        logger.error(f"[IG-API] Noma'lum media_type: {media_type}")
        return None


async def _download_media_url(session, media_url: str, prefix: str, ext: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """To'g'ridan-to'g'ri media URL dan yuklash."""
    import time as _time

    output_path = tempfile.mkdtemp()
    file_name = f"{prefix}_{int(_time.time())}.{ext}"
    file_path = os.path.join(output_path, file_name)

    try:
        async with session.get(media_url) as resp:
            if resp.status != 200:
                logger.error(f"[IG-DL] Media yuklash xatosi: status={resp.status}")
                return None
            with open(file_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            logger.error("[IG-DL] Yuklangan fayl bo'sh")
            return None

        info = {
            "id": f"{prefix}_{int(_time.time())}",
            "title": f"Instagram Story",
            "extractor": "instagram",
            "ext": ext,
        }

        logger.info(f"[IG-DL] Media yuklandi: {file_size / 1024:.1f} KB")
        return file_path, info

    except Exception as e:
        logger.error(f"[IG-DL] Media yuklash xatosi: {e}")
        return None


# ============================================================
# COOKIE AUTO-GENERATION via yt-session-generator
# ============================================================

async def _auto_generate_cookies() -> Optional[str]:
    """YouTube cookie'larini avtomatik generatsiya qilish."""
    global _cookies_path

    generator_url = os.getenv("YT_SESSION_GENERATOR_URL", "")
    if not generator_url:
        logger.debug("[Cookie-Gen] YT_SESSION_GENERATOR_URL o'rnatilmagan")
        return None

    try:
        import aiohttp
        logger.info(f"[Cookie-Gen] yt-session-generator ga so'rov yuborilmoqda: {generator_url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{generator_url.rstrip('/')}/generate",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[Cookie-Gen] Generator javob bermadi: HTTP {resp.status}")
                    return None

                data = await resp.json()
                cookies_text = data.get("cookies", "")
                if not cookies_text:
                    logger.warning("[Cookie-Gen] Generator cookie qaytarmadi")
                    return None

                cookies_path = os.path.join(tempfile.gettempdir(), "yt_generated_cookies.txt")
                with open(cookies_path, "w") as f:
                    f.write(cookies_text)

                _cookies_path = cookies_path
                logger.info(f"[Cookie-Gen] Yangi cookie'lar generatsiya qilindi: {cookies_path}")
                return cookies_path

    except Exception as e:
        logger.warning(f"[Cookie-Gen] Xato: {e}")
        return None


# ============================================================
# YOUTUBE: Faqat yt-dlp + PO_TOKEN (API usullarisiz — tezkor)
# ============================================================

async def _extract_youtube_info(url: str) -> Optional[Dict[str, Any]]:
    """YouTube video ma'lumotlarini olish.

    STRATEGIYA (soddalashtirilgan — tezkor):
    1. yt-dlp + PO_TOKEN + SOCKS5 proxy (5-10 soniya)
    2. yt-dlp + cookies + ios (fallback)
    3. yt-dlp + ios (fallback)

    Cobalt/InnerTube/Invidious/Piped — OLIB TASHLANDI (hammasi ishlamaydi).
    """
    global _bot_detection_count

    po_token = os.getenv("PO_TOKEN", "")
    cookies_path = _find_cookies_file()

    # Urinishlar ro'yxati
    player_clients = []
    if po_token:
        player_clients.append(("po_token+web", {}))
    if cookies_path:
        player_clients.append(("cookies+ios", {"youtube": {"player_client": ["ios"]}}))
    player_clients.append(("ios", {"youtube": {"player_client": ["ios"]}}))

    for label, extractor_args in player_clients:
        use_cookies = "cookies" in label
        try:
            opts = {
                "format": "all",
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "noplaylist": True,
                "geo_bypass": "US",
                "geo_bypass_country": "US",
                # === Tuzatildi: 8 → 120 ===
                "socket_timeout": 120,
            }

            # PO Token
            if po_token:
                opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = f"web+{po_token}"

            # Extractor args
            if extractor_args:
                for key, val in extractor_args.items():
                    opts.setdefault("extractor_args", {}).setdefault(key, {}).update(val)

            if use_cookies:
                opts["cookiefile"] = cookies_path

            # Proxy — to'g'ridan-to'g'ri env var
            proxy = _get_youtube_proxy()
            if proxy:
                opts["proxy"] = proxy
            else:
                opts["proxy"] = ""

            proxy_status = "bor" if proxy else "yo'q"
            logger.info(f"[YouTube] yt-dlp info: {label} (proxy: {proxy_status})")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    formats = info.get("formats", [])
                    if _has_video_audio(formats):
                        logger.info(f"[YouTube] yt-dlp MUVOFAQIYATLI: {label}")
                        _bot_detection_count = max(0, _bot_detection_count - 1)
                        return info

        except Exception as e:
            if _is_bot_detection_error(e):
                _bot_detection_count += 1
                logger.debug(f"[YouTube] yt-dlp {label}: Bot detektsiya")
            else:
                logger.debug(f"[YouTube] yt-dlp {label}: {str(e)[:80]}")
            continue

    logger.error("[YouTube] Barcha usullar muvaffaqiyatsiz")
    return None


async def extract_video_info(url: str) -> Optional[Dict[str, Any]]:
    """Video ma'lumotlarini olish (yuklamasdan)."""
    platform = detect_platform(url)
    is_youtube = platform == "youtube"

    if is_youtube:
        return await _extract_youtube_info(url)

    return await _extract_non_youtube_info(url, platform)


def _is_instagram_story(url: str) -> bool:
    return "/stories/" in url.lower()


def _is_login_required_error(error: Exception) -> bool:
    err_str = str(error).lower()
    login_keywords = [
        "log in to access",
        "login required",
        "need to log in",
        "private video",
        "sign in",
        "not available",
        "you need to log in",
    ]
    return any(kw in err_str for kw in login_keywords)


class LoginRequiredError(Exception):
    def __init__(self, platform: str, content_type: str = "", missing_cookies: list = None):
        self.platform = platform
        self.content_type = content_type
        self.missing_cookies = missing_cookies or []
        super().__init__(f"{platform}: login required for {content_type}")


async def _extract_non_youtube_info(url: str, platform: str) -> Optional[Dict[str, Any]]:
    """YouTube bo'lmagan platformalardan ma'lumot olish."""
    cookies_path = _find_cookies_file()

    is_story = platform == "instagram" and _is_instagram_story(url)
    if is_story and not cookies_path:
        logger.error("[instagram] Story uchun cookie kerak, lekin cookie fayl topilmadi!")
        raise LoginRequiredError("instagram", "story", ["sessionid", "ds_user_id", "csrftoken"])

    if is_story and cookies_path:
        ig_validation = _validate_instagram_cookies(cookies_path)
        if not ig_validation["valid"]:
            logger.error(f"[instagram] Story cookie'lari yetarli emas! Yetishmayotgan: {ig_validation['missing']}")
            raise LoginRequiredError("instagram", "story", ig_validation["missing"])

    for use_cookies in [True, False]:
        if use_cookies and not cookies_path:
            continue
        if is_story and not use_cookies:
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

            if platform == "instagram" and use_cookies:
                opts["extractor_args"] = {"instagram": {"api": ["graphql"]}}

            # Proxy — to'g'ridan-to'g'ri env var
            proxy = _get_platform_proxy(platform)
            if proxy:
                opts["proxy"] = proxy

            label = "cookies" if use_cookies else "cookiesiz"
            if platform == "instagram" and use_cookies:
                label = "cookies+graphql"
            logger.info(f"[{platform}] Ma'lumot olinmoqda: {label}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return info

        except Exception as e:
            err_str = str(e)
            logger.warning(f"[{platform}] {label} xato: {err_str[:100]}")
            if is_story and _is_login_required_error(e):
                logger.info(f"[{platform}] yt-dlp story uchun ishlamadi, API fallback...")
                raise LoginRequiredError("instagram", "story", [])
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

    if not platform:
        return None

    if platform == "youtube":
        return await _download_youtube(url, quality, audio_only)

    return await _download_non_youtube(url, quality, audio_only)


async def _download_youtube(url: str, quality: str = "720",
                             audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube videosini yuklab olish.

    STRATEGIYA (soddalashtirilgan — tezkor):
    1. yt-dlp + PO_TOKEN + web + SOCKS5 proxy (5-10 soniya)
    2. yt-dlp + cookies + ios (fallback)
    3. yt-dlp + ios (fallback)

    Cobalt/InnerTube/Invidious/Piped API — OLIB TASHLANDI (hammasi ishlamaydi, 60s behuda).
    """
    global _bot_detection_count

    if _bot_detection_count >= _bot_detection_threshold:
        logger.warning(
            f"[YouTube] yt-dlp o'tkazib yuborildi (bot detektsiya: {_bot_detection_count} marta). "
            f"PO_TOKEN o'rnatishni tavsiya etamiz."
        )
        return None

    po_token = os.getenv("PO_TOKEN", "")
    cookies_path = _find_cookies_file()
    output_path = tempfile.mkdtemp()

    # Urinishlar: PO_TOKEN+web → cookies+ios → ios
    player_clients = []
    if po_token:
        player_clients.append(("po_token+web", {}))
    if cookies_path:
        player_clients.append(("cookies+ios", {"youtube": {"player_client": ["ios"]}}))
    player_clients.append(("ios", {"youtube": {"player_client": ["ios"]}}))

    for i, (label, extractor_args) in enumerate(player_clients):
        use_cookies = "cookies" in label
        try:
            proxy = _get_youtube_proxy()
            proxy_status = "bor" if proxy else "yo'q"
            logger.info(f"[YouTube] yt-dlp yuklash {i+1}/{len(player_clients)}: {label} (proxy: {proxy_status})")

            opts = _build_download_opts(output_path, quality, audio_only, use_cookies, "best", "youtube")
            opts["geo_bypass"] = "US"
            opts["geo_bypass_country"] = "US"
            # === Tuzatildi: 8 → 120 ===
            opts["socket_timeout"] = 120

            # PO Token qo'shish
            if po_token:
                opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = f"web+{po_token}"

            if extractor_args:
                for key, val in extractor_args.items():
                    opts.setdefault("extractor_args", {}).setdefault(key, {}).update(val)

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if info is None:
                    logger.warning(f"[YouTube] yt-dlp {label}: info qaytarmadi")
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
                        logger.warning(f"[YouTube] yt-dlp {label}: fayl yaratmadi")
                        continue

                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if file_size_mb > config.download.max_file_size_mb:
                    logger.warning(f"[YouTube] Fayl juda katta: {file_size_mb:.1f}MB")
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    return None

                logger.info(f"[YouTube] yt-dlp yuklash MUVOFAQIYATLI: {label}")
                _bot_detection_count = max(0, _bot_detection_count - 1)
                return file_path, info

        except Exception as e:
            err_str = str(e)
            if "407" in err_str or "Proxy Authentication Required" in err_str:
                logger.warning(f"[YouTube] yt-dlp {label}: 407 Proxy Auth Required — proxy BUZILGAN!")
                break
            if "MaxDownloads" in str(type(e).__name__) or "MaxDownloads" in err_str:
                logger.warning("[YouTube] Fayl hajmi cheklovdan oshdi")
                return None
            if _is_bot_detection_error(e):
                _bot_detection_count += 1
                logger.warning(f"[YouTube] yt-dlp {label}: Bot detektsiya (jami: {_bot_detection_count})")
                break
            logger.warning(f"[YouTube] yt-dlp {label}: {err_str[:80]}")
            continue

    logger.error("[YouTube] Barcha yuklash usullari muvaffaqiyatsiz")
    return None


async def _download_non_youtube(url: str, quality: str, audio_only: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube bo'lmagan platformalardan yuklab olish."""
    platform = detect_platform(url) or "unknown"
    cookies_path = _find_cookies_file()
    is_story = platform == "instagram" and _is_instagram_story(url)

    # === INSTAGRAM STORY: API birinchi (tezroq), keyin yt-dlp fallback ===
    if is_story and cookies_path:
        ig_validation = _validate_instagram_cookies(cookies_path)

        if ig_validation["valid"]:
            logger.info(f"[{platform}] Story: API orqali yuklanmoqda (tez)...")
            try:
                api_result = await _download_instagram_story_api(url, cookies_path)
                if api_result:
                    logger.info(f"[{platform}] Story API MUVOFAQIYATLI!")
                    return api_result
            except Exception as e:
                logger.warning(f"[{platform}] Story API xato: {e}")

            # yt-dlp fallback
            output_path = tempfile.mkdtemp()
            try:
                opts = _build_download_opts(output_path, quality, audio_only, True, "best", "instagram")
                opts["extractor_args"] = {"instagram": {"api": ["graphql"]}}
                # === Tuzatildi: 8 → 120 ===
                opts["socket_timeout"] = 120

                logger.info(f"[{platform}] Story: yt-dlp fallback...")

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info:
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
                                file_path = None
                        if file_path:
                            logger.info(f"[{platform}] Story yt-dlp MUVOFAQIYATLI!")
                            return file_path, info

            except Exception as e:
                err_str = str(e)
                logger.warning(f"[{platform}] Story yt-dlp xato: {err_str[:80]}")

            raise LoginRequiredError("instagram", "story", ig_validation.get("missing", []))

        else:
            logger.error(f"[{platform}] Story cookie'lari yetarli emas: {ig_validation['missing']}")
            raise LoginRequiredError("instagram", "story", ig_validation["missing"])

    elif is_story and not cookies_path:
        raise LoginRequiredError("instagram", "story", ["sessionid", "ds_user_id", "csrftoken"])

    # === ODDIY (STORY BO'LMAGAN) YUKLASH ===
    output_path = tempfile.mkdtemp()
    attempts = []
    if cookies_path:
        attempts.append((True, "best"))
    attempts.append((False, "best"))

    for i, (use_cookies, fmt_override) in enumerate(attempts, 1):
        try:
            opts = _build_download_opts(output_path, quality, audio_only, use_cookies, fmt_override, platform)
            # === Tuzatildi: 8 → 120 ===
            opts["socket_timeout"] = 120

            label = "cookies" if use_cookies else "cookiesiz"
            logger.info(f"[{platform}] Yuklash {i}/{len(attempts)}: {label}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
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
                        continue

                logger.info(f"[{platform}] Yuklash muvaffaqiyatli: {label}")
                return file_path, info

        except Exception as e:
            err_str = str(e)
            logger.warning(f"[{platform}] Yuklash xatosi ({label}): {err_str[:80]}")
            if "407" in err_str or "Proxy Authentication Required" in err_str:
                logger.warning(f"[{platform}] 407 Proxy Auth Required — proxy buzilgan!")
                break
            continue

    logger.error(f"[{platform}] Barcha yuklash urinishlari muvaffaqiyatsiz")
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