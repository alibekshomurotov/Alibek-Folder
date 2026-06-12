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

# Bot detection xatosi sanagichi — agar barcha usullar shu xatoni bersa, keyin o'tkazib yuborish
_bot_detection_count = 0
_bot_detection_threshold = 3  # 3 marta bot detektsiyadan keyin yt-dlp urinishlarini o'tkazib yuborish

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


def _parse_cookies(cookies_path: str) -> Dict[str, List[Dict[str, str]]]:
    """cookies.txt faylini o'qib, domen bo'yicha cookie'larni qaytarish.

    Returns:
        {"instagram.com": [{"name": "sessionid", "value": "...", ...}], ...}
    """
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
    """Instagram cookie'larini tekshirish - story uchun muhim cookie'lar borligini tasdiqlash.

    Returns:
        {"valid": bool, "missing": [...], "found": [...], "total_ig_cookies": int}
    """
    cookies_by_domain = _parse_cookies(cookies_path)

    # Barcha instagram.com subdomainlarini birlashtirish
    ig_cookies = {}
    for domain, cookies in cookies_by_domain.items():
        if "instagram.com" in domain:
            for c in cookies:
                ig_cookies[c["name"]] = c["value"]

    # Story uchun KRITIK cookie'lar
    critical_cookies = ["sessionid", "ds_user_id", "csrftoken"]
    # Foydali qo'shimcha cookie'lar
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
        "ig_cookies": ig_cookies,  # API fallback uchun kerak
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

            # Instagram cookie'larini tekshirish
            ig_validation = _validate_instagram_cookies(path)
            if not ig_validation["valid"]:
                logger.warning(
                    f"[COOKIES] Instagram Story uchun cookie'lar YETARLI EMAS! "
                    f"Yetishmayotgan: {ig_validation['missing']}. "
                    f"Cookie'larni qayta eksport qiling va Instagram'ga kirganingizni tekshiring."
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

    # PO Token holatini tekshirish
    po_token = os.getenv("PO_TOKEN", "")
    if po_token:
        logger.info(f"[PO_TOKEN] Mavjud ({len(po_token)} belgi)")
    else:
        logger.warning("[PO_TOKEN] O'RNATILMAGAN! YouTube bot detektsiyasini chetlab o'tish uchun kerak.")


def _is_bot_detection_error(error: Exception) -> bool:
    """Xato bot detektsiya bilan bog'liq ekanligini tekshirish."""
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
        "socket_timeout": 10,
        "retries": 1,
        "fragment_retries": 1,
        "file_access_retries": 1,
        "throttledratelimit": 0,
        "concurrent_fragment_downloads": 4,
    }

    if use_cookies:
        cookies_path = _find_cookies_file()
        if cookies_path:
            opts["cookiefile"] = cookies_path

    # PO Token qo'shish
    po_token = os.getenv("PO_TOKEN", "")
    if po_token:
        opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = f"web+{po_token}"

    return opts


def _build_download_opts(output_path: str, quality: str = "720",
                         audio_only: bool = False, use_cookies: bool = True,
                         format_override: str = None) -> Dict[str, Any]:
    opts = _build_base_opts(use_cookies)

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
# INSTAGRAM: To'g'ridan-to'g'ri API orqali story yuklash
# ============================================================

async def _download_instagram_story_api(url: str, cookies_path: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Instagram story'ni to'g'ridan-to'g'ri API orqali yuklash.

    yt-dlp ishlamaganda bu fallback sifatida ishlatiladi.
    Instagram stories faqat autentifikatsiya qilingan foydalanuvchilarga ko'rinadi,
    shuning uchun cookie'larda sessionid, ds_user_id, csrftoken bo'lishi shart.

    Returns:
        (file_path, info_dict) yoki None
    """
    import http.cookiejar
    import aiohttp

    # URL dan username va story ID ajratish
    # Format: https://www.instagram.com/stories/USERNAME/STORY_ID/
    # Yoki: https://www.instagram.com/stories/USERNAME/
    story_match = re.search(r'/stories/([^/]+)(?:/(\d+))?/?', url)
    if not story_match:
        logger.error("[IG-API] URL dan username topilmadi")
        return None

    username = story_match.group(1)
    story_id = story_match.group(2)  # Bo'lmasa None

    logger.info(f"[IG-API] Story yuklanmoqda: username={username}, story_id={story_id}")

    # Cookie'larni tekshirish
    ig_validation = _validate_instagram_cookies(cookies_path)
    if not ig_validation["valid"]:
        logger.error(f"[IG-API] Cookie'lar yetarli emas! Yetishmayotgan: {ig_validation['missing']}")
        return None

    ig_cookies = ig_validation["ig_cookies"]
    sessionid = ig_cookies.get("sessionid", "")
    ds_user_id = ig_cookies.get("ds_user_id", "")
    csrftoken = ig_cookies.get("csrftoken", "")

    # Cookie string yaratish
    cookie_str = "; ".join(f"{k}={v}" for k, v in ig_cookies.items())

    headers = {
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

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1-qadam: Username dan user ID olish
        try:
            user_api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            async with session.get(user_api_url) as resp:
                if resp.status != 200:
                    logger.error(f"[IG-API] User ID olish xatosi: status={resp.status}")
                    # 2-usul: sahifa orqali user ID olishga urinib ko'ramiz
                    return await _download_instagram_story_page(url, cookies_path, ig_cookies, headers)

                user_data = await resp.json()
                user_id = user_data.get("data", {}).get("user", {}).get("id")
                if not user_id:
                    logger.error(f"[IG-API] User ID topilmadi: {user_data}")
                    return await _download_instagram_story_page(url, cookies_path, ig_cookies, headers)

        except Exception as e:
            logger.error(f"[IG-API] User ID olish xatosi: {e}")
            return await _download_instagram_story_page(url, cookies_path, ig_cookies, headers)

        logger.info(f"[IG-API] User ID: {user_id}")

        # 2-qadam: User story'larini olish
        try:
            story_api_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/story/"
            async with session.get(story_api_url) as resp:
                if resp.status != 200:
                    logger.error(f"[IG-API] Story API xatosi: status={resp.status}")
                    return await _download_instagram_story_page(url, cookies_path, ig_cookies, headers)

                story_data = await resp.json()
                items = story_data.get("items", [])

                if not items:
                    logger.error("[IG-API] Story topilmadi (bo'sh yoki faol emas)")
                    return None

        except Exception as e:
            logger.error(f"[IG-API] Story API xatosi: {e}")
            return await _download_instagram_story_page(url, cookies_path, ig_cookies, headers)

        # 3-qadam: Kerakli story'ni topish
        target_item = None

        if story_id:
            # Ma'lum bir story ID bo'yicha qidirish
            for item in items:
                item_id = str(item.get("id", ""))
                # Instagram story ID formati: "12345678901234567_12345678901"
                if item_id.startswith(story_id) or story_id in item_id:
                    target_item = item
                    break

        if not target_item and items:
            # Story ID topilmadi yoki berilmadi - oxirgi (eng yangi) story'ni olish
            if story_id:
                logger.warning(f"[IG-API] Story ID {story_id} topilmadi, oxirgi story olinmoqda")
            target_item = items[-1]

        if not target_item:
            logger.error("[IG-API] Hech qanday story topilmadi")
            return None

        # 4-qadam: Story media URL olish va yuklab olish
        return await _download_story_item(session, target_item, username)


async def _download_instagram_story_page(url: str, cookies_path: str,
                                          ig_cookies: Dict[str, str],
                                          headers: Dict[str, str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Instagram story'ni sahifa orqali yuklash - 2-usul fallback.

    API ishlamaganda, story sahifasini ochib, undan media URL'ni ajratib olamiz.
    """
    import aiohttp

    logger.info("[IG-PAGE] Story sahifa orqali yuklash boshlandi")

    # Story URL ni ochish
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.error(f"[IG-PAGE] Sahifa ochish xatosi: status={resp.status}")
                    return None

                html = await resp.text()

                # Sahifadan story ma'lumotlarini ajratib olish
                # Instagram sahifasida "window._sharedData" yoki "requireLazy" ichida JSON bo'ladi
                # Hozirgi versiyada: <script type="application/ld+json"> yoki window.__additionalDataLoaded

                # Usul 1: video_url ni to'g'ridan-to'g'ridan qidirish
                video_urls = re.findall(
                    r'"video_url"\s*:\s*"([^"]+)"',
                    html
                )
                if video_urls:
                    video_url = video_urls[0].replace("\\u0026", "&")
                    logger.info(f"[IG-PAGE] Video URL topildi!")
                    return await _download_media_url(session, video_url, "story_video", "mp4")

                # Usul 2: image URL qidirish
                image_urls = re.findall(
                    r'"display_url"\s*:\s*"([^"]+)"',
                    html
                )
                if image_urls:
                    image_url = image_urls[0].replace("\\u0026", "&")
                    logger.info(f"[IG-PAGE] Image URL topildi!")
                    return await _download_media_url(session, image_url, "story_image", "jpg")

                # Usul 3: og:video meta tag
                og_video = re.findall(
                    r'<meta\s+property="og:video(?::secure_url)?"\s+content="([^"]+)"',
                    html
                )
                if og_video:
                    video_url = og_video[0].replace("&amp;", "&")
                    logger.info(f"[IG-PAGE] OG Video URL topildi!")
                    return await _download_media_url(session, video_url, "story_video", "mp4")

                # Usul 4: CDN URL larni qidirish (story video uchun)
                cdn_urls = re.findall(
                    r'(https?://[^"]*fbcdn\.net[^"]*\.mp4[^"]*)',
                    html
                )
                if cdn_urls:
                    video_url = cdn_urls[0].replace("\\u0026", "&").replace("&amp;", "&")
                    logger.info(f"[IG-PAGE] CDN Video URL topildi!")
                    return await _download_media_url(session, video_url, "story_video", "mp4")

                logger.error("[IG-PAGE] Sahifadan media URL topilmadi")
                return None

        except Exception as e:
            logger.error(f"[IG-PAGE] Sahifa yuklash xatosi: {e}")
            return None


async def _download_story_item(session, item: dict, username: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Instagram story itemdan video/rasm yuklash."""
    item_id = str(item.get("id", "unknown"))
    media_type = item.get("media_type", 0)  # 1=rasm, 2=video

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
        # Video story
        video_versions = item.get("video_versions", [])
        if not video_versions:
            logger.error("[IG-API] Video versions topilmadi")
            return None

        # Eng yuqori sifatli versiyani tanlash
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
                        async for chunk in resp.content.iter_chunked(65536):  # 64KB — tezroq
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
        # Rasm story
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
                        async for chunk in resp.content.iter_chunked(65536):  # 64KB — tezroq
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
                async for chunk in resp.content.iter_chunked(65536):  # 64KB — tezroq
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
    """YouTube cookie'larini avtomatik generatsiya qilish.

    yt-session-generator yordamida yangi cookie'lar yaratish.
    Agar yt-session-generator o'rnatilmagan bo'lsa, None qaytaradi.

    MUHIM: Bu funksiya faqat cookie'lar yo'q yoki eskirgan bo'lsa ishlaydi.
    """
    global _cookies_path

    # yt-session-generator mavjudligini tekshirish
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

                # Cookie'larni faylga yozish
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
# YOUTUBE: Asosiy strategiya - avval API, keyin yt-dlp
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
    1. Cobalt API - tez va ishonchli (o'z serverimiz)
    2. InnerTube API - YouTube'ning ichki API si
    3. Invidious/Piped API - alternative frontendlar
    4. yt-dlp (1-2 urinish) - oxirgi chora, faqat PO Token bilan
    """
    global _bot_detection_count

    from app.utils.youtube_api import _extract_video_id

    video_id = _extract_video_id(url)

    # === 1-BOSQICH: Cobalt API (o'z serverimiz) ===
    cobalt_api_url = os.getenv("COBALT_API_URL", "")

    if cobalt_api_url:
        logger.info(f"[YouTube] Cobalt orqali tekshirilmoqda: {cobalt_api_url}")
        try:
            from app.utils.youtube_api import _try_cobalt
            cobalt_result = await _try_cobalt(url, "720", False)
            if cobalt_result and cobalt_result.get("download_url"):
                logger.info("[YouTube] Cobalt: Video mavjud! Info yaratilmoqda...")
                return {
                    "id": video_id or "unknown",
                    "title": "YouTube Video",
                    "description": "",
                    "duration": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "uploader": "YouTube",
                    "thumbnail": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg" if video_id else "",
                    "webpage_url": url,
                    "extractor": "youtube",
                    "formats": [
                        {"format_id": "720p", "height": 720, "ext": "mp4", "vcodec": "unknown", "acodec": "unknown"},
                        {"format_id": "480p", "height": 480, "ext": "mp4", "vcodec": "unknown", "acodec": "unknown"},
                        {"format_id": "360p", "height": 360, "ext": "mp4", "vcodec": "unknown", "acodec": "unknown"},
                        {"format_id": "mp3", "height": None, "ext": "mp3", "vcodec": "none", "acodec": "unknown"},
                    ],
                    "_cobalt_available": True,
                }
            else:
                logger.info("[YouTube] Cobalt javob bermadi, keyingi usulga o'tilmoqda...")
        except Exception as e:
            logger.warning(f"[YouTube] Cobalt xatosi: {e}")
    else:
        logger.info("[YouTube] COBALT_API_URL o'rnatilmagan, Cobalt o'tkazib yuborildi")

    # === 2-BOSQICH: InnerTube API (YouTube ichki API) ===
    logger.info("[YouTube] InnerTube API orqali tekshirilmoqda...")
    try:
        from app.utils.youtube_api import _try_innertube, convert_api_info_to_ytdlp
        innertube_result = await _try_innertube(video_id, "720", False)
        if innertube_result:
            info = convert_api_info_to_ytdlp(innertube_result)
            if info:
                logger.info("[YouTube] InnerTube orqali MUVOFAQIYATLI!")
                return info
    except Exception as e:
        logger.warning(f"[YouTube] InnerTube xatosi: {e}")

    # === 3-BOSQICH: Invidious/Piped API ===
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

    # === 4-BOSQICH: yt-dlp — faqat PO Token bilan yoki 1-2 marta ===
    # Bot detektsiyasi juda ko'p bo'lsa, yt-dlp urinishlarini o'tkazib yuborish
    if _bot_detection_count >= _bot_detection_threshold:
        logger.warning(f"[YouTube] yt-dlp o'tkazib yuborildi (bot detektsiya: {_bot_detection_count} marta)")
        return None

    po_token = os.getenv("PO_TOKEN", "")
    logger.info(f"[YouTube] yt-dlp sinab ko'rilmoqda (PO Token: {'bor' if po_token else 'yo\'q'})...")
    cookies_path = _find_cookies_file()

    # PO Token bilan 1-urinish, keyin cookiesiz 1-urinish
    player_clients = []
    if po_token:
        # PO Token bor — web klient bilan ishlashi kerak
        player_clients.append(("po_token+web", {}))
    if cookies_path:
        player_clients.append(("cookies+ios", {"youtube": {"player_client": ["ios"]}}))
    player_clients.extend([
        ("ios",     {"youtube": {"player_client": ["ios"]}}),
    ])

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
                "socket_timeout": 8,
            }

            # PO Token qo'shish
            if po_token:
                opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = f"web+{po_token}"

            if extractor_args:
                # Merge extractor_args
                for key, val in extractor_args.items():
                    opts.setdefault("extractor_args", {}).setdefault(key, {}).update(val)

            if use_cookies:
                opts["cookiefile"] = cookies_path

            logger.info(f"[YouTube] yt-dlp info: {label}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    formats = info.get("formats", [])
                    if _has_video_audio(formats):
                        logger.info(f"[YouTube] yt-dlp MUVOFAQIYATLI: {label}")
                        _bot_detection_count = max(0, _bot_detection_count - 1)  # Muvaffaqiyat — hisobni kamaytirish
                        return info

        except Exception as e:
            err_str = str(e)
            if _is_bot_detection_error(e):
                _bot_detection_count += 1
                logger.debug(f"[YouTube] yt-dlp {label}: Bot detektsiya")
            else:
                logger.debug(f"[YouTube] yt-dlp {label}: {err_str[:80]}")
            continue

    logger.error("[YouTube] Barcha usullar muvaffaqiyatsiz")
    return None


def _is_instagram_story(url: str) -> bool:
    """Instagram story URL ekanligini tekshirish."""
    return "/stories/" in url.lower()


def _is_login_required_error(error: Exception) -> bool:
    """Xato login talab qilish bilan bog'liq ekanligini tekshirish."""
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
    """Login talab qilinadigan kontent uchun maxsus xato."""
    def __init__(self, platform: str, content_type: str = "", missing_cookies: list = None):
        self.platform = platform
        self.content_type = content_type
        self.missing_cookies = missing_cookies or []
        super().__init__(f"{platform}: login required for {content_type}")


async def _extract_non_youtube_info(url: str, platform: str) -> Optional[Dict[str, Any]]:
    """YouTube bo'lmagan platformalardan ma'lumot olish."""
    cookies_path = _find_cookies_file()

    # Instagram stories maxsus tekshirish
    is_story = platform == "instagram" and _is_instagram_story(url)
    if is_story and not cookies_path:
        logger.error("[instagram] Story uchun cookie kerak, lekin cookie fayl topilmadi!")
        raise LoginRequiredError("instagram", "story", ["sessionid", "ds_user_id", "csrftoken"])

    # Instagram stories uchun cookie validation
    if is_story and cookies_path:
        ig_validation = _validate_instagram_cookies(cookies_path)
        if not ig_validation["valid"]:
            logger.error(f"[instagram] Story cookie'lari yetarli emas! Yetishmayotgan: {ig_validation['missing']}")
            raise LoginRequiredError("instagram", "story", ig_validation["missing"])

    for use_cookies in [True, False]:
        if use_cookies and not cookies_path:
            continue

        # Story uchun cookiesiz urinish o'tkazib yuboriladi
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

            # Instagram uchun maxsus extractor sozlamalari
            if platform == "instagram" and use_cookies:
                opts["extractor_args"] = {"instagram": {"api": ["graphql"]}}

            proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
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
            logger.warning(f"[{platform}] {label} xato: {str(e)[:100]}")
            # Instagram story + login xatosi → API fallback
            if is_story and _is_login_required_error(e):
                logger.info(f"[{platform}] yt-dlp story uchun ishlamadi, API fallback sinab ko'rilmoqda...")
                # Info olish uchun API dan foydalanish (yuklamasdan)
                # Hozircha LoginRequiredError qaytaramiz, yuklashda API fallback ishlaydi
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
    is_youtube = platform == "youtube"

    if not is_youtube:
        return await _download_non_youtube(url, quality, audio_only)

    return await _download_youtube(url, quality, audio_only)


async def _download_youtube(url: str, quality: str = "720",
                             audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube videosini yuklab olish.

    STRATEGIYA (yangilangan):
    1. Cobalt API (o'z serverimiz) - eng tez va ishonchli
    2. InnerTube API (YouTube ichki API) - to'g'ridan-to'g'ri
    3. Invidious/Piped API - alternative frontendlar
    4. yt-dlp - oxirgi chora, PO Token bilan

    Eslatma: Datacenter IP (Render, Heroku) da YouTube bloklaydi.
    PO_TOKEN o'rnatish — eng samarali yechim!
    Yoki o'z Cobalt serveringizni ishga tushiring va COBALT_API_URL qo'shing.
    """
    global _bot_detection_count

    # === 1-BOSQICH: Cobalt API (o'z serverimiz) ===
    cobalt_api_url = os.getenv("COBALT_API_URL", "")
    if cobalt_api_url:
        logger.info(f"[YouTube] Cobalt orqali yuklanmoqda: {cobalt_api_url}")
        try:
            from app.utils.youtube_api import _try_cobalt, _download_from_url, _make_basic_info, _extract_video_id
            cobalt_result = await _try_cobalt(url, quality, audio_only)
            if cobalt_result and cobalt_result.get("download_url"):
                video_id = _extract_video_id(url)
                result = await _download_from_url(cobalt_result["download_url"], video_id, audio_only)
                if result:
                    info = _make_basic_info(url, video_id, audio_only)
                    logger.info("[YouTube] Cobalt orqali yuklash MUVOFAQIYATLI!")
                    return result, info
                else:
                    logger.warning("[YouTube] Cobalt URL topildi lekin yuklab bo'lmadi")
            else:
                logger.info("[YouTube] Cobalt javob bermadi, keyingi usulga o'tilmoqda...")
        except Exception as e:
            logger.warning(f"[YouTube] Cobalt yuklash xatosi: {e}")
    else:
        logger.info("[YouTube] COBALT_API_URL o'rnatilmagan, Cobalt o'tkazib yuborildi")

    # === 2-BOSQICH: InnerTube API ===
    logger.info("[YouTube] InnerTube API orqali yuklanmoqda...")
    try:
        from app.utils.youtube_api import (
            _try_innertube, _extract_innertube_download,
            _download_from_url, _make_basic_info, _extract_video_id
        )
        video_id = _extract_video_id(url)
        innertube_result = await _try_innertube(video_id, quality, audio_only)
        if innertube_result:
            download_url, file_ext, fmt_info = _extract_innertube_download(
                innertube_result["data"], quality, audio_only
            )
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only, file_ext)
                if result:
                    info = fmt_info or _make_basic_info(url, video_id, audio_only)
                    logger.info("[YouTube] InnerTube orqali yuklash MUVOFAQIYATLI!")
                    return result, info
                else:
                    logger.warning("[YouTube] InnerTube URL topildi lekin yuklab bo'lmadi")
            else:
                logger.warning("[YouTube] InnerTube dan URL ajratib bo'lmadi (cipher kerak)")
    except Exception as e:
        logger.warning(f"[YouTube] InnerTube yuklash xatosi: {e}")

    # === 3-BOSQICH: Invidious/Piped API (youtube_api.py dagi) ===
    logger.info("[YouTube] API orqali yuklanmoqda (Invidious/Piped)...")
    try:
        from app.utils.youtube_api import download_youtube_via_api
        result = await download_youtube_via_api(url, quality, audio_only)
        if result:
            logger.info("[YouTube] API orqali yuklash MUVOFAQIYATLI!")
            return result
    except Exception as e:
        logger.warning(f"[YouTube] API yuklash xatosi: {e}")

    # === 4-BOSQICH: yt-dlp orqali yuklash ===
    # Bot detektsiyasi juda ko'p bo'lsa, yt-dlp urinishlarini o'tkazib yuborish
    if _bot_detection_count >= _bot_detection_threshold:
        logger.warning(
            f"[YouTube] yt-dlp o'tkazib yuborildi (bot detektsiya: {_bot_detection_count} marta). "
            f"PO_TOKEN o'rnatishni yoki residential proxy ishlatishni tavsiya etamiz."
        )
        return None

    po_token = os.getenv("PO_TOKEN", "")
    cookies_path = _find_cookies_file()
    output_path = tempfile.mkdtemp()

    # PO Token bilan urinish — eng ishonchli
    # Keyin faqat 1-2 marta cookiesiz urinish
    player_clients = []

    if po_token:
        # PO Token + web klient — eng ishonchli
        player_clients.append(("po_token+web", {}))
    if cookies_path:
        player_clients.append(("cookies+ios", {"youtube": {"player_client": ["ios"]}}))
    # Faqat 1 marta cookiesiz urinish (ko'p urinish behuda)
    player_clients.append(("ios", {"youtube": {"player_client": ["ios"]}}))

    for i, (label, extractor_args) in enumerate(player_clients):
        use_cookies = "cookies" in label
        try:
            logger.info(f"[YouTube] yt-dlp yuklash {i+1}/{len(player_clients)}: {label}")

            opts = _build_download_opts(output_path, quality, audio_only, use_cookies, "best")
            opts["geo_bypass"] = "US"
            opts["geo_bypass_country"] = "US"
            opts["socket_timeout"] = 8

            # PO Token qo'shish
            if po_token:
                opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = f"web+{po_token}"

            if extractor_args:
                # Merge extractor_args
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

                # Fayl hajmini tekshirish
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if file_size_mb > config.download.max_file_size_mb:
                    logger.warning(f"[YouTube] Fayl juda katta: {file_size_mb:.1f}MB")
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    return None

                logger.info(f"[YouTube] yt-dlp yuklash MUVOFAQIYATLI: {label}")
                _bot_detection_count = max(0, _bot_detection_count - 1)  # Muvaffaqiyat — hisobni kamaytirish
                return file_path, info

        except Exception as e:
            err_str = str(e)
            if "MaxDownloads" in str(type(e).__name__) or "MaxDownloads" in err_str:
                logger.warning("[YouTube] Fayl hajmi cheklovdan oshdi")
                return None
            if _is_bot_detection_error(e):
                _bot_detection_count += 1
                logger.warning(f"[YouTube] yt-dlp {label}: Bot detektsiya (jami: {_bot_detection_count})")
                # Bot detektsiyasi bo'lsa, keyingi klientlarni ham o'tkazib yuborish
                break  # Boshqa klientlar ham ishlamaydi
            logger.warning(f"[YouTube] yt-dlp {label}: {err_str[:80]}")
            continue

    logger.error("[YouTube] Barcha yuklash usullari muvaffaqiyatsiz")
    return None


async def _download_non_youtube(url: str, quality: str, audio_only: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube bo'lmagan platformalardan yuklab olish — TEZ versiya.

    Instagram stories: 1-urinish yt-dlp → keyin to'g'ridan-to'g'ri API
    Boshqa platformalar: 1-urinish cookies → 2-urinish cookiesiz
    """
    platform = detect_platform(url) or "unknown"
    cookies_path = _find_cookies_file()
    is_story = platform == "instagram" and _is_instagram_story(url)

    # === INSTAGRAM STORY: API birinchi (tezroq), keyin yt-dlp fallback ===
    if is_story and cookies_path:
        ig_validation = _validate_instagram_cookies(cookies_path)

        if ig_validation["valid"]:
            # 1-urinish: Instagram API (to'g'ridan-to'g'ri — eng tez)
            logger.info(f"[{platform}] Story: API orqali yuklanmoqda (tez)...")
            try:
                api_result = await _download_instagram_story_api(url, cookies_path)
                if api_result:
                    logger.info(f"[{platform}] Story API MUVOFAQIYATLI!")
                    return api_result
            except Exception as e:
                logger.warning(f"[{platform}] Story API xato: {e}")

            # 2-urinish: yt-dlp (faqat 1 marta, API ishlamasa)
            output_path = tempfile.mkdtemp()
            try:
                opts = _build_download_opts(output_path, quality, audio_only, True, "best")
                opts["extractor_args"] = {"instagram": {"api": ["graphql"]}}
                opts["socket_timeout"] = 8

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
                logger.warning(f"[{platform}] Story yt-dlp xato: {str(e)[:80]}")

            raise LoginRequiredError("instagram", "story", ig_validation.get("missing", []))

        else:
            logger.error(f"[{platform}] Story cookie'lari yetarli emas: {ig_validation['missing']}")
            raise LoginRequiredError("instagram", "story", ig_validation["missing"])

    elif is_story and not cookies_path:
        raise LoginRequiredError("instagram", "story", ["sessionid", "ds_user_id", "csrftoken"])

    # === ODDIY (STORY BO'LMAGAN) YUKLASH — faqat 1-2 urinish, tez ===
    output_path = tempfile.mkdtemp()  # Bitta temp dir — qayta ishlatamiz
    attempts = []
    if cookies_path:
        attempts.append((True, "best"))          # 1: cookies + best
    attempts.append((False, "best"))              # 2: cookiesiz + best

    for i, (use_cookies, fmt_override) in enumerate(attempts, 1):
        try:
            opts = _build_download_opts(output_path, quality, audio_only, use_cookies, fmt_override)
            opts["socket_timeout"] = 8

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
            logger.warning(f"[{platform}] Yuklash xatosi ({label}): {str(e)[:80]}")
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
