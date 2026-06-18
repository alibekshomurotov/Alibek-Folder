import asyncio
import json
import logging
import os
import tempfile
import time
import random
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse, parse_qs, quote_plus

import aiohttp

logger = logging.getLogger(__name__)

# Cobalt API - env dan har safar o'qiladi (_try_cobalt ichida)
COBALT_API_URL = os.getenv("COBALT_API_URL", "")
COBALT_API_KEY = os.getenv("COBALT_API_KEY", "")

# Proxy sozlamalari - YOUTUBE_PROXY faqat SOCKS5 proxy qabul qiladi
# HTTP proxy HTTPS tunnel qila olmaydi (400 Bad Request)
YOUTUBE_PROXY = os.getenv("YOUTUBE_PROXY", "")
# Faqat SOCKS5 proxy ishlatamiz
_USE_SOCKS5_PROXY = YOUTUBE_PROXY.lower().startswith("socks5") if YOUTUBE_PROXY else False

# Instagram uchun alohida proxy
INSTAGRAM_PROXY = os.getenv("INSTAGRAM_PROXY", "")

# YouTube proxy holati
_proxy_broken = False
_proxy_broken_since = 0
_PROXY_RETRY_AFTER = 300  # 5 daqiqadan keyin qayta urinib ko'rish

# Instagram proxy holati
_ig_proxy_broken = False
_ig_proxy_broken_since = 0
_IG_PROXY_RETRY_AFTER = 300

# Cobalt holati
_cobalt_broken_until = 0

# PO Token
PO_TOKEN = os.getenv("PO_TOKEN", "")

# Invidious instances - 2026 iyun yangilangan
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://iv.datura.network",
    "https://invidious.jing.rocks",
    "https://invidious.protokolla.fi",
    "https://yt.cdaut.de",
    "https://invidious.perennialte.ch",
    "https://inv.tux.pizza",
    "https://vid.puffyan.us",
    "https://invidious.fdn.fr",
    "https://iv.ggtyler.dev",
    "https://inv.oikei.net",
    "https://yewtu.be",
    "https://invidious.privacy.de",
    "https://invidious.lunar.icu",
    "https://invidious.private.coffee",
    "https://iv.melmac.space",
    "https://inv.citw.lgbt",
]

# Piped instances - 2026 iyun yangilangan
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.r4fo.com",
    "https://api.piped.yt",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.darkness.services",
    "https://pipedapi.drgns.space",
    "https://api.piped.projectsegfau.lt",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_FAST_INVIDIOUS = INVIDIOUS_INSTANCES[:5]
_FAST_PIPED = PIPED_INSTANCES[:3]

# Timeout sozlamalari (soniya)
_INVIDIOUS_TIMEOUT = 6
_PIPED_TIMEOUT = 6
_INVIDIOUS_DOWNLOAD_TIMEOUT = 10  # Invidious proxy download uchun

# InnerTube API klientlari - 2026 iyun yangilangan versiyalar
INNERTUBE_CLIENTS = {
    "android": {
        "clientName": "ANDROID",
        "clientVersion": "20.29.37",
        "androidSdkVersion": 35,
        "hl": "en",
        "gl": "US",
        "apiKey": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
    },
    "ios": {
        "clientName": "IOS",
        "clientVersion": "20.29.3",
        "deviceModel": "iPhone16,2",
        "hl": "en",
        "gl": "US",
        "apiKey": "AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc",
    },
    "tv": {
        "clientName": "TVHTML5_CAST",
        "clientVersion": "7.20260610.00.00",
        "hl": "en",
        "gl": "US",
        "apiKey": "AIzaSyD8nUgaBM3G_3smGBk9AV5FCWx8pFWsBPQ",
    },
    "web_embed": {
        "clientName": "WEB_EMBEDDED_PLAYER",
        "clientVersion": "2.20260610.00.00",
        "hl": "en",
        "gl": "US",
        "thirdPartyEmbedUrl": "https://www.google.com",
        "apiKey": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
    },
    "mweb": {
        "clientName": "MWEB",
        "clientVersion": "2.20260610.01.00",
        "hl": "en",
        "gl": "US",
        "apiKey": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
    },
    "web": {
        "clientName": "WEB",
        "clientVersion": "2.20260610.00.00",
        "hl": "en",
        "gl": "US",
        "apiKey": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
    },
}


def _is_proxy_available() -> bool:
    """SOCKS5 proxy ishlayaptimi tekshirish."""
    global _proxy_broken, _proxy_broken_since
    if not _USE_SOCKS5_PROXY:
        return False
    if _proxy_broken:
        if _proxy_broken_since and (time.time() - _proxy_broken_since > _PROXY_RETRY_AFTER):
            logger.info("[Proxy] Qayta urinib ko'rilmoqda (5 daqiqa o'tdi)...")
            _proxy_broken = False
            _proxy_broken_since = 0
            return True
        return False
    return True


def _get_proxy_connector() -> Optional[aiohttp.TCPConnector]:
    """SOCKS5 proxy uchun aiohttp connector yaratish."""
    if not _is_proxy_available():
        return None

    try:
        from aiohttp_socks import ProxyConnector
        return ProxyConnector.from_url(YOUTUBE_PROXY)
    except ImportError:
        logger.warning("[Proxy] aiohttp-socks o'rnatilmagan!")
        return None
    except Exception as e:
        logger.warning(f"[Proxy] SOCKS5 connector xatosi: {e}")
        return None


def _get_proxy_url() -> Optional[str]:
    """aiohttp uchun proxy URL qaytarish (SOCKS5 connector ishlatilsa None)."""
    return None  # SOCKS5 doimo connector orqali


def _mark_proxy_broken(reason: str = ""):
    """YouTube proxy buzilgan deb belgilash."""
    global _proxy_broken, _proxy_broken_since
    if not _proxy_broken:
        _proxy_broken = True
        _proxy_broken_since = time.time()
        reason_str = f" ({reason})" if reason else ""
        logger.warning(f"[Proxy] YouTube proxy ishlamadi{reason_str}, proxysz ishlatamiz! Qayta urinish: 5 daqiqadan keyin")


def _is_instagram_proxy_available() -> bool:
    """Instagram proxy ishlayaptimi tekshirish."""
    global _ig_proxy_broken, _ig_proxy_broken_since
    if not INSTAGRAM_PROXY:
        return False
    if _ig_proxy_broken:
        if _ig_proxy_broken_since and (time.time() - _ig_proxy_broken_since > _IG_PROXY_RETRY_AFTER):
            logger.info("[IG-Proxy] Qayta urinib ko'rilmoqda (5 daqiqa o'tdi)...")
            _ig_proxy_broken = False
            _ig_proxy_broken_since = 0
            return True
        return False
    return True


def _mark_instagram_proxy_broken(reason: str = ""):
    """Instagram proxy buzilgan deb belgilash."""
    global _ig_proxy_broken, _ig_proxy_broken_since
    if not _ig_proxy_broken:
        _ig_proxy_broken = True
        _ig_proxy_broken_since = time.time()
        reason_str = f" ({reason})" if reason else ""
        logger.warning(f"[IG-Proxy] Instagram proxy ishlamadi{reason_str}, proxysz ishlatamiz!")


def get_proxy_for_platform(platform: str) -> Optional[str]:
    """Platformaga mos proxy URL qaytarish."""
    if platform == "youtube":
        if _is_proxy_available():
            return YOUTUBE_PROXY
        return None
    elif platform == "instagram":
        if _is_instagram_proxy_available():
            return INSTAGRAM_PROXY
        return None
    else:
        return None


def _extract_video_id(url: str) -> Optional[str]:
    """YouTube video ID sini URL dan ajratib olish."""
    parsed = urlparse(url)

    if "youtube.com" in parsed.netloc:
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            return qs.get("v", [None])[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/")[1].split("?")[0]
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/embed/")[1].split("?")[0]
        if parsed.path.startswith("/live/"):
            return parsed.path.split("/live/")[1].split("?")[0]

    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/").split("?")[0]

    return None


# ============================================================
# COBALT API
# ============================================================

async def _wake_hf_space(api_url: str) -> bool:
    """Hugging Face Space ni uyg'otish."""
    if ".hf.space" not in api_url:
        return True

    try:
        logger.info("[Cobalt] HF Space uyg'otilmoqda...")
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(
                api_url.rstrip("/"),
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=True,
            ) as resp:
                status = resp.status
                body = await resp.text()
                logger.info(f"[Cobalt] HF Space javob: HTTP {status}")

                if body.strip().startswith("<"):
                    logger.info("[Cobalt] HF Space uyg'onyapti, 5 sekund kutilmoqda...")
                    await asyncio.sleep(5)

                    async with session.get(
                        api_url.rstrip("/"),
                        timeout=aiohttp.ClientTimeout(total=5),
                        allow_redirects=True,
                    ) as resp2:
                        body2 = await resp2.text()
                        if body2.strip().startswith("<"):
                            logger.warning("[Cobalt] HF Space hali ham HTML qaytarayapti")
                            return False
                        logger.info("[Cobalt] HF Space API tayyor!")
                        return True

                if '"version"' in body or '"cobalt"' in body:
                    logger.info("[Cobalt] HF Space tayyor!")
                    return True

                return True

    except asyncio.TimeoutError:
        logger.warning("[Cobalt] HF Space timeout")
        return False
    except Exception as e:
        logger.warning(f"[Cobalt] HF Space uyg'otish xatosi: {e}")
        return False


async def _try_cobalt(url: str, quality: str = "720", audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """Cobalt API orqali video yuklab olish."""
    api_url = os.getenv("COBALT_API_URL", "")
    api_key = os.getenv("COBALT_API_KEY", "")

    if not api_url:
        logger.debug("[Cobalt] COBALT_API_URL o'rnatilmagan")
        return None

    global _cobalt_broken_until
    if time.time() < _cobalt_broken_until:
        logger.debug("[Cobalt] Yaqinda ishlamadi, o'tkazib yuborilmoqda...")
        return None

    if not api_key and ("cobalt.tools" in api_url or "api.cobalt.tools" == api_url.rstrip("/")):
        logger.warning("[Cobalt] Rasmiy API uchun COBALT_API_KEY talab qilinadi!")
        return None

    logger.info(f"[Cobalt] {api_url} ga so'rov yuborilmoqda...")

    if ".hf.space" in api_url:
        space_ready = await _wake_hf_space(api_url)
        if not space_ready:
            logger.error("[Cobalt] HF Space ishlamayapti!")
            _cobalt_broken_until = time.time() + 300
            return None

    endpoint = api_url.rstrip('/')

    # Cobalt SOCKS5 proxy orqali emas, to'g'ridan-to'g'ri ishlaydi
    # (Cobalt o'zi YouTube ga murojaat qiladi, bizning IPimiz emas)
    cobalt_quality = quality.replace("p", "")
    if audio_only:
        cobalt_quality = "0"

    payload = {
        "url": url,
        "videoQuality": cobalt_quality,
        "filenameStyle": "basic",
        "downloadMode": "audio" if audio_only else "auto",
    }

    headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.text()
                logger.info(f"[Cobalt] POST {endpoint} -> HTTP {resp.status} - {body[:300]}")

                if body.strip().startswith("<"):
                    logger.error("[Cobalt] HTML javob - API noto'g'ri sozlangan!")
                    return None

                if resp.status == 401:
                    logger.error("[Cobalt] 401 - API_KEY kerak!")
                    return None

                try:
                    data = json.loads(body)
                except Exception:
                    if resp.status != 200:
                        logger.warning(f"[Cobalt] HTTP {resp.status} (JSON emas)")
                    else:
                        logger.warning("[Cobalt] JSON parse xatosi")
                    return None

                status = data.get("status", "")

                if status == "error":
                    error_text = data.get("text", "")
                    error_obj = data.get("error", {})

                    if error_text:
                        logger.warning(f"[Cobalt] Xato: {error_text[:200]}")
                    elif isinstance(error_obj, dict):
                        error_code = error_obj.get("code", "noma'lum")
                        error_context = error_obj.get("context", {})
                        ctx_str = f" ({error_context})" if error_context else ""
                        logger.warning(f"[Cobalt] Xato kodi: {error_code}{ctx_str}")
                        if "fetch.fail" in str(error_code) and error_context.get("service") == "youtube":
                            logger.error("[Cobalt] YouTube yuklab bo'lmadi")
                            _cobalt_broken_until = time.time() + 300
                    else:
                        logger.warning(f"[Cobalt] Xato: {str(error_obj)[:200]}")
                    return None

                download_url = None

                raw_url = data.get("url")
                if raw_url:
                    if raw_url.startswith("/tunnel"):
                        download_url = f"{api_url.rstrip('/')}{raw_url}"
                    else:
                        download_url = raw_url

                if not download_url:
                    tunnel_obj = data.get("tunnel")
                    if isinstance(tunnel_obj, dict):
                        download_url = tunnel_obj.get("url") or tunnel_obj.get("proxy")
                        if download_url and download_url.startswith("/"):
                            download_url = f"{api_url.rstrip('/')}{download_url}"

                if not download_url:
                    picker = data.get("picker", [])
                    if picker and isinstance(picker, list):
                        first = picker[0]
                        if isinstance(first, dict):
                            download_url = first.get("url")
                        elif isinstance(first, str):
                            download_url = first

                if download_url:
                    logger.info(f"[Cobalt] Yuklash URL topildi! (status={status})")
                    return {
                        "source": "cobalt",
                        "download_url": download_url,
                        "audio_only": audio_only,
                        "filename": data.get("filename", ""),
                    }
                else:
                    logger.warning(f"[Cobalt] URL topilmadi: {str(data)[:200]}")

    except asyncio.TimeoutError:
        logger.warning("[Cobalt] Timeout (15s)")
    except Exception as e:
        logger.warning(f"[Cobalt] Xato: {str(e)[:100]}")

    return None


# ============================================================
# INNERTUBE API - YouTube'ning ichki API si
# ============================================================

async def _try_innertube(video_id: str, quality: str = "720",
                         audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """YouTube InnerTube API orqali video ma'lumot olish."""
    logger.info(f"[InnerTube] Video ID: {video_id}")

    # Yangi tartib: android, ios, tv, web_embed, mweb, web
    # android/ios/tv PO Token talab qilmaydi
    client_order = ["android", "ios", "tv", "web_embed", "mweb", "web"]

    po_token = os.getenv("PO_TOKEN", "")
    has_bot_detection = False

    for client_key in client_order:
        if has_bot_detection and not po_token and client_key in ("web", "mweb", "web_embed"):
            logger.info(f"[InnerTube] {client_key}: O'tkazib yuborildi (bot detektsiya + PO_TOKEN yo'q)")
            continue

        client_ctx = INNERTUBE_CLIENTS.get(client_key)
        if not client_ctx:
            logger.debug(f"[InnerTube] {client_key}: Klient konfiguratsiyasi topilmadi")
            continue

        try:
            result = await _innertube_player_request(video_id, client_key, client_ctx, po_token)
            if result is None:
                logger.warning(f"[InnerTube] {client_key}: So'rov muvaffaqiyatsiz")
                continue

            playability = result.get("playabilityStatus", {})
            status = playability.get("status", "")

            if status == "OK":
                streaming_data = result.get("streamingData", {})
                formats = streaming_data.get("formats", [])
                adaptive_formats = streaming_data.get("adaptiveFormats", [])

                if formats or adaptive_formats:
                    video_details = result.get("videoDetails", {})
                    logger.info(
                        f"[InnerTube] {client_key}: MUVOFAQIYATLI - "
                        f"Formats: {len(formats)}, Adaptive: {len(adaptive_formats)}"
                    )
                    return {
                        "source": "innertube",
                        "client": client_key,
                        "data": result,
                        "video_id": video_id,
                        "quality": quality,
                        "audio_only": audio_only,
                    }
                else:
                    logger.warning(f"[InnerTube] {client_key}: OK lekin formatlar topilmadi")
                    continue

            elif status == "LOGIN_REQUIRED":
                reason = playability.get("reason", "")
                messages = playability.get("messages", [])
                logger.warning(
                    f"[InnerTube] {client_key}: LOGIN_REQUIRED - "
                    f"{reason} {' '.join(messages)[:100]}"
                )
                if "bot" in reason.lower() or "sign in" in reason.lower():
                    has_bot_detection = True
                if not po_token:
                    logger.info(f"[InnerTube] PO_TOKEN yo'q, keyingi klientga o'tilmoqda...")
                continue

            elif status == "UNPLAYABLE":
                reason = playability.get("reason", "")
                logger.warning(f"[InnerTube] {client_key}: UNPLAYABLE - {reason}")
                continue

            elif status == "ERROR":
                reason = playability.get("reason", "")
                if "no longer supported" in reason.lower():
                    logger.warning(f"[InnerTube] {client_key}: KLIENT ESKIRGAN - {reason}")
                else:
                    logger.warning(f"[InnerTube] {client_key}: ERROR - {reason}")
                continue

            else:
                reason = playability.get("reason", "Noma'lum")
                logger.warning(f"[InnerTube] {client_key}: {status} - {reason}")
                continue

        except Exception as e:
            logger.warning(f"[InnerTube] {client_key}: Istisno - {str(e)[:80]}")
            continue

    if not po_token:
        logger.warning("[InnerTube] Barcha klientlar muvaffaqiyatsiz. PO_TOKEN o'rnatishni tavsiya etamiz!")
    else:
        logger.warning("[InnerTube] Barcha klientlar muvaffaqiyatsiz")
    return None


async def _innertube_player_request(video_id: str, client_key: str,
                                     client_ctx: Dict, po_token: str = "") -> Optional[Dict]:
    """YouTube InnerTube player API ga so'rov yuborish.

    YANGILANGAN: _get_proxy_url() hamisha None qaytaradi,
    SOCKS5 faqat connector orqali ishlaydi.
    """
    import hashlib

    api_key = client_ctx.get("apiKey", "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8")

    api_url = "https://www.youtube.com/youtubei/v1/player"
    params = {
        "key": api_key,
        "prettyPrint": "false",
    }

    client_info = {k: v for k, v in client_ctx.items() if k != "apiKey"}

    # PO Token qo'shish (web, mweb, web_embed klientlari uchun)
    if po_token and client_key in ("web", "mweb", "web_embed"):
        client_info["poToken"] = po_token

    # VISITOR_DATA qo'shish
    visitor_data = os.getenv("VISITOR_DATA", "")
    if visitor_data and client_key in ("web", "mweb", "web_embed"):
        client_info["visitorData"] = visitor_data

    # Cookies fayldan SAPISIDHASH olish
    sapisid = ""
    cookies_path = os.getenv("COOKIES_FILE", "cookies.txt")
    if not os.path.exists(cookies_path):
        cookies_path = os.path.join(os.getcwd(), "cookies.txt")
    if os.path.exists(cookies_path):
        try:
            with open(cookies_path, "r") as f:
                for line in f:
                    if "SAPISID" in line and "youtube.com" in line and not line.startswith("#"):
                        parts = line.strip().split("\t")
                        if len(parts) >= 7:
                            sapisid = parts[6].strip()
                            break
        except Exception:
            pass

    payload = {
        "context": {
            "client": client_info,
        },
        "videoId": video_id,
        "contentCheckOk": True,
        "racyCheckOk": True,
    }

    # User-Agent
    if client_key == "android":
        user_agent = "com.google.android.youtube/20.29.37 (Linux; U; Android 15)"
    elif client_key == "ios":
        user_agent = "com.google.ios.youtube/20.29.3 (iPhone; U; CPU iOS 19_0 like Mac OS X)"
    elif client_key == "tv":
        user_agent = "Mozilla/5.0 (Chromecast; Linux) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 CrKey/1.56.500000"
    else:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "Origin": "https://www.youtube.com",
        "Referer": f"https://www.youtube.com/watch?v={video_id}",
    }

    if client_key == "android":
        headers["X-YouTube-Client-Name"] = "3"
        headers["X-YouTube-Client-Version"] = "20.29.37"
    elif client_key == "ios":
        headers["X-YouTube-Client-Name"] = "5"
        headers["X-YouTube-Client-Version"] = "20.29.3"
    elif client_key == "tv":
        headers["X-YouTube-Client-Name"] = "7"
        headers["X-YouTube-Client-Version"] = "7.20260610.00.00"

    # Cookie header qo'shish
    cookie_parts = []
    if sapisid:
        cookie_parts.append(f"SAPISID={sapisid}")
        timestamp = str(int(time.time()))
        hash_input = f"{timestamp} {sapisid} https://www.youtube.com"
        sapisidhash = hashlib.sha1(hash_input.encode()).hexdigest()
        headers["Authorization"] = f"SAPISIDHASH {timestamp}_{sapisidhash}"

    if cookie_parts:
        headers["Cookie"] = "; ".join(cookie_parts)

    # Proxy: SOCKS5 connector yoki proxysz
    use_proxy = _is_proxy_available()
    connector = _get_proxy_connector() if use_proxy else None
    proxy = None  # SOCKS5 connector orqali ishlaydi

    if use_proxy:
        logger.info(f"[InnerTube] {client_key}: SOCKS5 proxy ishlatilmoqda")
    else:
        logger.info(f"[InnerTube] {client_key}: Proxysz")

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                api_url,
                params=params,
                json=payload,
                headers=headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    if resp.status == 403:
                        # 403 — YouTube bot deb o'ylayapti, boshqa klientga o'tish
                        logger.warning(f"[InnerTube] {client_key}: HTTP 403 (bot detection)")
                    else:
                        logger.warning(f"[InnerTube] {client_key}: HTTP {resp.status}")
                    return None

                data = await resp.json()
                return data

    except aiohttp.ClientConnectorError:
        if use_proxy:
            _mark_proxy_broken("InnerTube connection error")
        logger.warning(f"[InnerTube] {client_key}: Ulanish xatosi")
        return None
    except asyncio.TimeoutError:
        logger.warning(f"[InnerTube] {client_key}: Timeout (10s)")
        return None
    except Exception as e:
        err_str = str(e)
        if "407" in err_str or "Proxy Authentication" in err_str or "ProxyError" in err_str:
            if use_proxy:
                _mark_proxy_broken(f"407 Proxy Auth (InnerTube {client_key})")
                return None
        logger.warning(f"[InnerTube] {client_key} xato: {err_str[:80]}")
        return None


def _extract_innertube_download(data: Dict, quality: str = "720",
                                 audio_only: bool = False) -> Tuple[Optional[str], str, Optional[Dict]]:
    """InnerTube natijasidan eng yaxshi yuklab olish URL ini topish."""
    streaming_data = data.get("streamingData", {})
    video_details = data.get("videoDetails", {})

    formats = streaming_data.get("formats", [])
    adaptive_formats = streaming_data.get("adaptiveFormats", [])

    if audio_only:
        audio_fmts = [f for f in adaptive_formats if f.get("mimeType", "").startswith("audio/")]
        if not audio_fmts:
            audio_fmts = formats
        if not audio_fmts:
            return None, "", None

        audio_fmts.sort(key=lambda f: int(f.get("bitrate", 0)), reverse=True)
        best = audio_fmts[0]

        url = best.get("url")
        if not url:
            cipher = best.get("cipher") or best.get("signatureCipher", "")
            if cipher:
                url = _decrypt_cipher(cipher)

        if url:
            info = _innertube_to_info(video_details, best, audio_only=True)
            return url, "mp3", info

        return None, "", None

    # Video yuklash
    target_height = int(quality.replace("p", ""))

    # Combined formatlar
    combined = [f for f in formats if f.get("url") or f.get("cipher") or f.get("signatureCipher")]
    if combined:
        best_combined = _find_closest_format(combined, target_height)
        if best_combined:
            url = best_combined.get("url")
            if not url:
                cipher = best_combined.get("cipher") or best_combined.get("signatureCipher", "")
                if cipher:
                    url = _decrypt_cipher(cipher)
            if url:
                info = _innertube_to_info(video_details, best_combined, audio_only=False)
                return url, "mp4", info

    # Adaptive formatlar
    video_fmts = [f for f in adaptive_formats if f.get("mimeType", "").startswith("video/")]
    if video_fmts:
        best_video = _find_closest_format(video_fmts, target_height)
        if best_video:
            url = best_video.get("url")
            if not url:
                cipher = best_video.get("cipher") or best_video.get("signatureCipher", "")
                if cipher:
                    url = _decrypt_cipher(cipher)
            if url:
                info = _innertube_to_info(video_details, best_video, audio_only=False)
                return url, "mp4", info

    return None, "", None


def _find_closest_format(formats: List[Dict], target_height: int) -> Optional[Dict]:
    """Target height ga eng yaqin formatni topish."""
    best = None
    best_diff = 99999

    for fmt in formats:
        height = fmt.get("height")
        if not height:
            quality = fmt.get("qualityLabel", "")
            if "p" in quality:
                try:
                    height = int(quality.replace("p", ""))
                except ValueError:
                    continue
            else:
                continue

        diff = abs(height - target_height)
        if diff < best_diff:
            best_diff = diff
            best = fmt
        elif diff == best_diff and best:
            if int(fmt.get("bitrate", 0)) > int(best.get("bitrate", 0)):
                best = fmt

    return best


def _decrypt_cipher(cipher_text: str) -> Optional[str]:
    """YouTube cipher/signatureCipher dan URL ajratib olish."""
    from urllib.parse import parse_qs as _parse_qs

    try:
        params = _parse_qs(cipher_text)
        url = params.get("url", [None])[0]
        if url:
            s = params.get("s", [None])[0]
            sp = params.get("sp", ["signature"])[0]
            if s:
                import urllib.parse
                url = f"{url}&{sp}={urllib.parse.quote(s)}"
            return url
    except Exception as e:
        logger.debug(f"[Cipher] Decrypt xatosi: {e}")

    return None


def _innertube_to_info(video_details: Dict, fmt: Dict,
                       audio_only: bool = False) -> Dict[str, Any]:
    """InnerTube ma'lumotlarini yt-dlp info formatiga o'girish."""
    return {
        "id": video_details.get("videoId", ""),
        "title": video_details.get("title", "Noma'lum"),
        "description": video_details.get("shortDescription", ""),
        "duration": int(video_details.get("lengthSeconds", 0)),
        "view_count": int(video_details.get("viewCount", 0)),
        "uploader": video_details.get("author", ""),
        "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
        "webpage_url": f"https://www.youtube.com/watch?v={video_details.get('videoId', '')}",
        "extractor": "youtube",
        "formats": [{
            "format_id": str(fmt.get("itag", "")),
            "url": fmt.get("url", ""),
            "ext": "mp4",
            "height": fmt.get("height"),
            "width": fmt.get("width"),
            "vcodec": "none" if audio_only else "unknown",
            "acodec": "unknown",
            "bitrate": fmt.get("bitrate", 0),
        }],
    }


# ============================================================
# INVIDIOUS API
# ============================================================

async def _try_invidious(video_id: str, fast_only: bool = False) -> Optional[Dict[str, Any]]:
    """Invidious API orqali video ma'lumotlarini olish.

    YANGILANGAN: Invidious proxy emas — YouTube ga o'zi murojaat qiladi.
    Shuning uchun biz PROXY ishlatmaymiz, to'g'ridan-to'g'ri so'rov yuboramiz.
    """
    instances = _FAST_INVIDIOUS if fast_only else INVIDIOUS_INSTANCES

    for instance in instances:
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                url = f"{instance}/api/v1/videos/{video_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_INVIDIOUS_TIMEOUT)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Invidious] {instance}: HTTP {resp.status}")
                        continue

                    data = await resp.json()
                    formats = data.get("formatStreams", []) + data.get("adaptiveFormats", [])
                    video_formats = [f for f in formats if f.get("type", "").startswith("video")]
                    audio_formats = [f for f in formats if f.get("type", "").startswith("audio")]

                    if not video_formats and not audio_formats:
                        continue

                    logger.info(
                        f"[Invidious] {instance}: MUVOFAQIYATLI - "
                        f"Video: {len(video_formats)}, Audio: {len(audio_formats)}"
                    )
                    return {
                        "source": "invidious",
                        "instance": instance,
                        "data": data,
                        "video_id": video_id,
                    }

        except Exception as e:
            logger.debug(f"[Invidious] {instance}: {str(e)[:60]}")
            continue

    return None


# ============================================================
# PIPED API
# ============================================================

async def _try_piped(video_id: str, fast_only: bool = False) -> Optional[Dict[str, Any]]:
    """Piped API orqali video ma'lumotlarini olish.

    YANGILANGAN: Piped ham proxy emas — to'g'ridan-to'g'ri so'rov.
    """
    instances = _FAST_PIPED if fast_only else PIPED_INSTANCES

    for instance in instances:
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                url = f"{instance}/streams/{video_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=_PIPED_TIMEOUT)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Piped] {instance}: HTTP {resp.status}")
                        continue

                    data = await resp.json()
                    video_streams = data.get("videoStreams", [])
                    audio_streams = data.get("audioStreams", [])

                    if not video_streams and not audio_streams:
                        continue

                    logger.info(
                        f"[Piped] {instance}: MUVOFAQIYATLI - "
                        f"Video: {len(video_streams)}, Audio: {len(audio_streams)}"
                    )
                    return {
                        "source": "piped",
                        "instance": instance,
                        "data": data,
                        "video_id": video_id,
                    }

        except Exception as e:
            logger.debug(f"[Piped] {instance}: {str(e)[:60]}")
            continue

    return None


# ============================================================
# INVIDIOUS PROXY DOWNLOAD
# ============================================================

async def _try_invidious_proxy_download(video_id: str, quality: str = "720",
                                         audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Invidious orqali video yuklash — Invidious o'zi proxy bo'lib ishlaydi.

    YANGILANGAN: Proxy ishlatmaymiz (Invidious o'zi YouTube ga murojaat qiladi).
    """
    instances = _FAST_INVIDIOUS[:5]  # 5 ta instance sinash

    target_height = int(quality.replace("p", "")) if not audio_only else 0

    for instance in instances:
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                api_url = f"{instance}/api/v1/videos/{video_id}"
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=_INVIDIOUS_DOWNLOAD_TIMEOUT)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Invidious-Proxy] {instance}: HTTP {resp.status}")
                        continue

                    data = await resp.json()

                    if audio_only:
                        for fmt in data.get("adaptiveFormats", []):
                            if fmt.get("type", "").startswith("audio") and fmt.get("url"):
                                download_url = fmt["url"]
                                if not download_url.startswith("http"):
                                    download_url = f"{instance}{download_url}"

                                result = await _download_from_url(download_url, video_id, True, "mp3")
                                if result:
                                    info = _convert_invidious(data, video_id)
                                    return result, info
                        continue

                    # Video formatni topish
                    format_streams = data.get("formatStreams", [])
                    best = None
                    best_diff = 99999

                    for fmt in format_streams:
                        quality_label = fmt.get("qualityLabel", "")
                        if not quality_label:
                            continue
                        try:
                            height = int(quality_label.replace("p", ""))
                        except ValueError:
                            continue
                        diff = abs(height - target_height)
                        if diff < best_diff:
                            best_diff = diff
                            best = fmt

                    if best and best.get("url"):
                        download_url = best["url"]
                        if not download_url.startswith("http"):
                            download_url = f"{instance}{download_url}"

                        result = await _download_from_url(download_url, video_id, False, "mp4")
                        if result:
                            info = _convert_invidious(data, video_id)
                            return result, info

        except Exception as e:
            logger.debug(f"[Invidious-Proxy] {instance}: {str(e)[:60]}")
            continue

    return None


# ============================================================
# ASOSIY FUNKSIYALAR
# ============================================================

async def get_youtube_info_via_api(url: str, skip_cobalt: bool = False,
                                    skip_innertube: bool = False) -> Optional[Dict[str, Any]]:
    """YouTube video ma'lumotlarini API orqali olish.

    YANGI STRATEGIYA (datacenter IP uchun):
    1. Invidious (tezkor) — datacenter IP da ishlaydi
    2. Piped (tezkor) — datacenter IP da ishlaydi
    3. Invidious (to'liq) — ko'proq instance
    4. Piped (to'liq) — ko'proq instance
    5. Cobalt API — o'z server orqali
    6. InnerTube API — faqat SOCKS5 proxy bilan
    """
    video_id = _extract_video_id(url)
    if not video_id:
        logger.error("[API] Video ID topilmadi")
        return None

    logger.info(f"[API] Video ID: {video_id}")

    # === 1-USUL: Invidious (tezkor) — datacenter IP da ishlaydi ===
    result = await _try_invidious(video_id, fast_only=True)
    if result:
        return result

    # === 2-USUL: Piped (tezkor) ===
    result = await _try_piped(video_id, fast_only=True)
    if result:
        return result

    # === 3-USUL: Invidious (to'liq) ===
    result = await _try_invidious(video_id, fast_only=False)
    if result:
        return result

    # === 4-USUL: Piped (to'liq) ===
    result = await _try_piped(video_id, fast_only=False)
    if result:
        return result

    # === 5-USUL: Cobalt API ===
    if not skip_cobalt:
        cobalt_result = await _try_cobalt(url, "720", False)
        if cobalt_result and cobalt_result.get("download_url"):
            logger.info("[API] Cobalt orqali video mavjudligi tasdiqlandi!")
            return {
                "source": "cobalt",
                "data": {
                    "title": "YouTube Video",
                    "download_url": cobalt_result["download_url"],
                },
                "video_id": video_id,
                "_cobalt_available": True,
            }

    # === 6-USUL: InnerTube API (faqat SOCKS5 proxy bilan) ===
    if not skip_innertube and _is_proxy_available():
        innertube_result = await _try_innertube(video_id, "720", False)
        if innertube_result:
            logger.info("[API] InnerTube orqali ma'lumot olindi!")
            return innertube_result

    logger.error("[API] Barcha API serverlar ishlamadi")
    return None


async def download_youtube_via_api(url: str, quality: str = "720",
                                    audio_only: bool = False,
                                    skip_cobalt: bool = False,
                                    skip_innertube: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube videosini API orqali yuklab olish.

    YANGI STRATEGIYA (datacenter IP uchun optimallashtirilgan):
    1. Invidious proxy download — Invidious YouTube ga o'zi murojaat qiladi
    2. Invidious API -> download
    3. Piped API -> download
    4. Invidious (to'liq ro'yxat) -> download
    5. Piped (to'liq ro'yxat) -> download
    6. Cobalt API (agar skip_cobalt=False)
    7. InnerTube API (faqat SOCKS5 proxy bilan)
    """
    video_id = _extract_video_id(url)
    start_time = time.time()

    # 1: INVIDIOUS PROXY DOWNLOAD — eng yaxshi datacenter IP yechim
    logger.info("[API] 1-usul: Invidious proxy orqali yuklanmoqda...")
    inv_proxy_result = await _try_invidious_proxy_download(video_id, quality, audio_only)
    if inv_proxy_result:
        logger.info(f"[API] Invidious proxy MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
        return inv_proxy_result
    logger.info(f"[API] Invidious proxy ishlamadi ({time.time()-start_time:.1f}s), keyingi usul...")

    # 2: INVIDIOUS API (tezkor)
    elapsed = time.time() - start_time
    if elapsed < 50:
        logger.info("[API] 2-usul: Invidious orqali yuklanmoqda...")
        inv_result = await _try_invidious(video_id, fast_only=True)
        if inv_result:
            download_url, file_ext = _find_best_invidious_download(inv_result["data"], quality, audio_only)
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only, file_ext)
                if result:
                    info = convert_api_info_to_ytdlp(inv_result)
                    logger.info(f"[API] Invidious MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
                    return result, info
        logger.info(f"[API] Invidious ishlamadi ({time.time()-start_time:.1f}s), keyingi usul...")

    # 3: PIPED (tezkor)
    elapsed = time.time() - start_time
    if elapsed < 50:
        logger.info("[API] 3-usul: Piped orqali yuklanmoqda...")
        piped_result = await _try_piped(video_id, fast_only=True)
        if piped_result:
            download_url, file_ext = _find_best_piped_download(piped_result["data"], quality, audio_only)
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only, file_ext)
                if result:
                    info = convert_api_info_to_ytdlp(piped_result)
                    logger.info(f"[API] Piped MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
                    return result, info
        logger.info(f"[API] Piped ishlamadi ({time.time()-start_time:.1f}s), keyingi usul...")

    # 4: INVIDIOUS (to'liq ro'yxat)
    elapsed = time.time() - start_time
    if elapsed < 50:
        logger.info("[API] 4-usul: Invidious (to'liq) orqali yuklanmoqda...")
        inv_result = await _try_invidious(video_id, fast_only=False)
        if inv_result:
            download_url, file_ext = _find_best_invidious_download(inv_result["data"], quality, audio_only)
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only, file_ext)
                if result:
                    info = convert_api_info_to_ytdlp(inv_result)
                    logger.info(f"[API] Invidious (to'liq) MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
                    return result, info
        logger.info(f"[API] Invidious (to'liq) ishlamadi ({time.time()-start_time:.1f}s), keyingi usul...")

    # 5: PIPED (to'liq ro'yxat)
    elapsed = time.time() - start_time
    if elapsed < 50:
        logger.info("[API] 5-usul: Piped (to'liq) orqali yuklanmoqda...")
        piped_result = await _try_piped(video_id, fast_only=False)
        if piped_result:
            download_url, file_ext = _find_best_piped_download(piped_result["data"], quality, audio_only)
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only, file_ext)
                if result:
                    info = convert_api_info_to_ytdlp(piped_result)
                    logger.info(f"[API] Piped (to'liq) MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
                    return result, info
        logger.info(f"[API] Piped (to'liq) ishlamadi ({time.time()-start_time:.1f}s), keyingi usul...")

    # 6: COBALT (faqat skip bo'lmasa)
    elapsed = time.time() - start_time
    if elapsed < 50 and not skip_cobalt:
        logger.info("[API] 6-usul: Cobalt orqali yuklanmoqda...")
        cobalt_result = await _try_cobalt(url, quality, audio_only)
        if cobalt_result:
            download_url = cobalt_result.get("download_url")
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only)
                if result:
                    info = _make_basic_info(url, video_id, audio_only)
                    logger.info(f"[API] Cobalt MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
                    return result, info
        logger.info(f"[API] Cobalt ishlamadi ({time.time()-start_time:.1f}s), keyingi usul...")

    # 7: INNERTUBE (faqat SOCKS5 proxy bilan)
    elapsed = time.time() - start_time
    if elapsed < 50 and not skip_innertube and _is_proxy_available():
        logger.info("[API] 7-usul: InnerTube orqali yuklanmoqda...")
        innertube_result = await _try_innertube(video_id, quality, audio_only)
        if innertube_result:
            download_url, file_ext, fmt_info = _extract_innertube_download(
                innertube_result["data"], quality, audio_only
            )
            if download_url:
                result = await _download_from_url(download_url, video_id, audio_only, file_ext)
                if result:
                    info = fmt_info or _make_basic_info(url, video_id, audio_only)
                    logger.info(f"[API] InnerTube MUVOFAQIYATLI ({time.time()-start_time:.1f}s)")
                    return result, info

    total_time = time.time() - start_time
    logger.error(f"[API] Barcha yuklash usullari muvaffaqiyatsiz ({total_time:.1f}s)")
    return None


async def _download_from_url(url: str, video_id: str, audio_only: bool,
                              file_ext: str = None) -> Optional[str]:
    """To'g'ridan-to'g'ri URL dan fayl yuklab olish.

    YANGILANGAN: SOCKS5 proxy faqat connector orqali.
    Invidious/Piped URL lari uchun proxy kerak emas (ular o'z proksilari orqali xizmat qiladi).
    Faqat InnerTube dan olingan YouTube CDN URL lari uchun SOCKS5 proxy kerak bo'lishi mumkin.
    """
    output_path = tempfile.mkdtemp()

    if not file_ext:
        file_ext = "mp3" if audio_only else "mp4"

    file_name = f"{video_id or 'video'}.{file_ext}"
    file_path = os.path.join(output_path, file_name)

    logger.info(f"[API] Fayl yuklanmoqda: {file_ext}...")

    try:
        from app.config import config as app_config
        max_size = app_config.download.max_file_size_mb * 1024 * 1024

        # SOCKS5 proxy: faqat youtube.com/googlevideo.com URL lari uchun
        is_youtube_cdn = "googlevideo.com" in url or "youtube.com" in url
        use_proxy = _is_proxy_available() and is_youtube_cdn

        connector = _get_proxy_connector() if use_proxy else None
        proxy = None  # SOCKS5 connector orqali

        try:
            async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
                async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        if use_proxy:
                            _mark_proxy_broken(f"Download HTTP {resp.status}")
                            # Qayta urinish: proxysz
                            logger.info(f"[API] Proxy bilan yuklab bo'lmadi (HTTP {resp.status}), proxysz urinilmoqda...")
                            return await _download_from_url_no_proxy(url, video_id, audio_only, file_ext, max_size, file_path, output_path)
                        logger.error(f"[API] Yuklash HTTP xatosi: {resp.status}")
                        return None

                    total_size = 0
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            total_size += len(chunk)
                            if total_size > max_size:
                                logger.error(f"[API] Fayl juda katta: {total_size / 1024 / 1024:.1f}MB")
                                try:
                                    os.remove(file_path)
                                except OSError:
                                    pass
                                return None
                            f.write(chunk)

                    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                        logger.error("[API] Yuklangan fayl bo'sh")
                        return None

                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    logger.info(f"[API] Yuklash MUVOFAQIYATLI: {file_size_mb:.1f}MB")
                    return file_path

        except aiohttp.ClientConnectorError:
            if use_proxy:
                _mark_proxy_broken("Download connection error")
                logger.info("[API] Proxy bilan ulanib bo'lmadi, proxysz urinilmoqda...")
                return await _download_from_url_no_proxy(url, video_id, audio_only, file_ext, max_size, file_path, output_path)
            logger.error("[API] Ulanish xatosi")
            return None
        except Exception as e:
            err_str = str(e)
            if use_proxy and ("407" in err_str or "Proxy Authentication" in err_str or "ProxyError" in err_str):
                _mark_proxy_broken(f"407 Proxy Auth (download)")
                logger.info("[API] Proxy xatosi, proxysz urinilmoqda...")
                return await _download_from_url_no_proxy(url, video_id, audio_only, file_ext, max_size, file_path, output_path)
            if use_proxy:
                logger.info("[API] Proxy bilan yuklab bo'lmadi, proxysz urinilmoqda...")
                return await _download_from_url_no_proxy(url, video_id, audio_only, file_ext, max_size, file_path, output_path)
            logger.error(f"[API] Yuklash xatosi: {e}")
            return None

    except Exception as e:
        logger.error(f"[API] Yuklash xatosi: {e}")

    return None


async def _download_from_url_no_proxy(url: str, video_id: str, audio_only: bool,
                                       file_ext: str, max_size: int,
                                       file_path: str, output_path: str) -> Optional[str]:
    """Proxysz URL dan fayl yuklab olish (fallback)."""
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    logger.error(f"[API] Proxysz yuklash HTTP xatosi: {resp.status}")
                    return None

                # Faylni qayta yozish uchun tozalash
                try:
                    os.remove(file_path)
                except OSError:
                    pass

                total_size = 0
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        total_size += len(chunk)
                        if total_size > max_size:
                            logger.error(f"[API] Fayl juda katta: {total_size / 1024 / 1024:.1f}MB")
                            try:
                                os.remove(file_path)
                            except OSError:
                                pass
                            return None
                        f.write(chunk)

                if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                    logger.error("[API] Yuklangan fayl bo'sh (proxysz)")
                    return None

                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                logger.info(f"[API] Proxysz yuklash MUVOFAQIYATLI: {file_size_mb:.1f}MB")
                return file_path

    except Exception as e:
        logger.error(f"[API] Proxysz yuklash xatosi: {e}")

    return None


def _make_basic_info(url: str, video_id: str, audio_only: bool) -> Dict[str, Any]:
    """Cobalt uchun asosiy info yaratish."""
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
        "formats": [],
    }


# ============================================================
# FORMAT TANLASH
# ============================================================

def _find_best_invidious_download(data: Dict, quality: str, audio_only: bool) -> Tuple[Optional[str], str]:
    """Invidious dan eng yaxshi yuklab olish URL ini topish."""
    if audio_only:
        for fmt in data.get("adaptiveFormats", []):
            if fmt.get("type", "").startswith("audio") and fmt.get("url"):
                return fmt["url"], "mp3"
        for fmt in data.get("formatStreams", []):
            if fmt.get("url"):
                return fmt["url"], "mp3"
        return None, "mp3"

    target_height = int(quality.replace("p", ""))
    format_streams = data.get("formatStreams", [])
    best = None
    best_diff = 99999

    for fmt in format_streams:
        quality_label = fmt.get("qualityLabel", "")
        if not quality_label:
            continue
        try:
            height = int(quality_label.replace("p", ""))
        except ValueError:
            continue
        diff = abs(height - target_height)
        if diff < best_diff:
            best_diff = diff
            best = fmt

    if best and best.get("url"):
        return best["url"], "mp4"

    return None, "mp4"


def _find_best_piped_download(data: Dict, quality: str, audio_only: bool) -> Tuple[Optional[str], str]:
    """Piped dan eng yaxshi yuklab olish URL ini topish."""
    if audio_only:
        audio_streams = data.get("audioStreams", [])
        if audio_streams:
            best = audio_streams[0]
            for stream in audio_streams:
                try:
                    if int(stream.get("quality", 0)) > int(best.get("quality", 0)):
                        best = stream
                except (ValueError, TypeError):
                    pass
            if best.get("url"):
                return best["url"], "mp3"
        return None, "mp3"

    target_height = int(quality.replace("p", ""))
    video_streams = data.get("videoStreams", [])

    best = None
    best_diff = 99999

    for stream in video_streams:
        q = stream.get("quality")
        if not q:
            continue
        try:
            height = int(q)
        except (ValueError, TypeError):
            continue
        diff = abs(height - target_height)
        if diff < best_diff:
            best_diff = diff
            best = stream

    if best and best.get("url"):
        return best["url"], "mp4"

    return None, "mp4"


# ============================================================
# YT-DLP FORMATIGA O'GIRISH
# ============================================================

def convert_api_info_to_ytdlp(api_result: Dict[str, Any]) -> Dict[str, Any]:
    """API ma'lumotlarini yt-dlp formatiga aylantirish."""
    source = api_result["source"]
    data = api_result["data"]
    video_id = api_result["video_id"]

    if source == "cobalt":
        return _make_basic_info(
            f"https://www.youtube.com/watch?v={video_id}",
            video_id,
            False
        )
    elif source == "innertube":
        video_details = data.get("videoDetails", {})
        return {
            "id": video_id,
            "title": video_details.get("title", "Noma'lum"),
            "description": video_details.get("shortDescription", ""),
            "duration": int(video_details.get("lengthSeconds", 0)),
            "view_count": int(video_details.get("viewCount", 0)),
            "uploader": video_details.get("author", ""),
            "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
            "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
            "extractor": "youtube",
            "formats": [],
        }
    elif source == "invidious":
        return _convert_invidious(data, video_id)
    else:
        return _convert_piped(data, video_id)


def _convert_invidious(data: Dict, video_id: str) -> Dict[str, Any]:
    info = {
        "id": video_id,
        "title": data.get("title", "Noma'lum"),
        "description": data.get("description", ""),
        "duration": data.get("lengthSeconds", 0),
        "view_count": data.get("viewCount", 0),
        "like_count": data.get("likeCount", 0),
        "uploader": data.get("author", "Noma'lum"),
        "uploader_id": data.get("authorId", ""),
        "thumbnail": data.get("videoThumbnails", [{}])[0].get("url", "") if data.get("videoThumbnails") else "",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "extractor": "youtube",
        "formats": [],
    }

    for fmt in data.get("formatStreams", []):
        info["formats"].append({
            "format_id": fmt.get("itag", "unknown"),
            "url": fmt.get("url", ""),
            "ext": "mp4",
            "height": fmt.get("qualityLabel", "").replace("p", "") if "p" in fmt.get("qualityLabel", "") else None,
            "vcodec": "unknown",
            "acodec": "unknown",
        })

    for fmt in data.get("adaptiveFormats", []):
        fmt_type = fmt.get("type", "video/mp4")
        is_audio = fmt_type.startswith("audio")
        info["formats"].append({
            "format_id": fmt.get("itag", "unknown"),
            "url": fmt.get("url", ""),
            "ext": "mp4",
            "height": fmt.get("qualityLabel", "").replace("p", "") if "p" in fmt.get("qualityLabel", "") else None,
            "vcodec": "none" if is_audio else "unknown",
            "acodec": "none" if not is_audio else "unknown",
        })

    return info


def _convert_piped(data: Dict, video_id: str) -> Dict[str, Any]:
    info = {
        "id": video_id,
        "title": data.get("title", "Noma'lum"),
        "description": data.get("description", ""),
        "duration": data.get("duration", 0),
        "view_count": data.get("views", 0),
        "like_count": data.get("likes", 0),
        "uploader": data.get("uploader", "Noma'lum"),
        "uploader_id": data.get("uploaderUrl", "").replace("/channel/", ""),
        "thumbnail": data.get("thumbnailUrl", ""),
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "extractor": "youtube",
        "formats": [],
    }

    for stream in data.get("videoStreams", []):
        info["formats"].append({
            "format_id": str(stream.get("itag", "unknown")),
            "url": stream.get("url", ""),
            "ext": "mp4",
            "height": stream.get("quality"),
            "vcodec": "unknown",
            "acodec": "none",
        })

    for stream in data.get("audioStreams", []):
        info["formats"].append({
            "format_id": str(stream.get("itag", "unknown")),
            "url": stream.get("url", ""),
            "ext": "mp4",
            "height": None,
            "vcodec": "none",
            "acodec": "unknown",
        })

    return info