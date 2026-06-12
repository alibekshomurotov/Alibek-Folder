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
# Eski modul o'zgaruvchilari qoldirildi, lekin _try_cobalt da os.getenv() ishlatiladi
COBALT_API_URL = os.getenv("COBALT_API_URL", "")
COBALT_API_KEY = os.getenv("COBALT_API_KEY", "")

# Proxy sozlamalari
YOUTUBE_PROXY = os.getenv("YOUTUBE_PROXY", "") or os.getenv("HTTP_PROXY", "") or os.getenv("HTTPS_PROXY", "")

# Proxy holati - agar proxy xato bersa, keyingi so'rovlarda ishlatmaymiz
_proxy_broken = False

# PO Token - YouTube bot detektsiyasini chetlab o'tish uchun
PO_TOKEN = os.getenv("PO_TOKEN", "")

# Invidious instances - 2026 yil iyunda yangilangan
INVIDIOUS_INSTANCES = [
    # Eng ishonchli (tezroq)
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://iv.datura.network",
    "https://invidious.jing.rocks",
    "https://invidious.protokolla.fi",
    "https://yt.cdaut.de",
    "https://invidious.perennialte.ch",
    "https://inv.tux.pizza",
    "https://vid.puffyan.us",
    "https://invidious.privacyredirect.com",
    # Qo'shimcha instancelar
    "https://inv.in.projectsegfau.lt",
    "https://invidious.projectsegfau.lt",
    "https://invidious.fdn.fr",
    "https://iv.ggtyler.dev",
    "https://inv.oikei.net",
    "https://yewtu.be",
    "https://invidious.privacy.de",
    "https://invidious.lunar.icu",
    "https://inv.bp.projectsegfau.lt",
    # Qo'shimcha yangi instancelar
    "https://invidious.private.coffee",
    "https://inv.tux.pizza",
    "https://iv.melmac.space",
    "https://inv.citw.lgbt",
]

# Piped instances - 2026 yil iyunda yangilangan
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

_FAST_INVIDIOUS = INVIDIOUS_INSTANCES[:6]  # 6 ta tezkor
_FAST_PIPED = PIPED_INSTANCES[:4]  # 4 ta tezkor

# InnerTube API klientlari - har xil platforma emulyatsiyasi
INNERTUBE_CLIENTS = {
    "web": {
        "clientName": "WEB",
        "clientVersion": "2.20240726.00.00",
        "hl": "en",
        "gl": "US",
    },
    "android": {
        "clientName": "ANDROID",
        "clientVersion": "19.29.37",
        "androidSdkVersion": 30,
        "hl": "en",
        "gl": "US",
    },
    "ios": {
        "clientName": "IOS",
        "clientVersion": "19.29.1",
        "deviceModel": "iPhone16,2",
        "hl": "en",
        "gl": "US",
    },
    "mweb": {
        "clientName": "MWEB",
        "clientVersion": "2.20240726.01.00",
        "hl": "en",
        "gl": "US",
    },
    "tv": {
        "clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
        "clientVersion": "2.0",
        "hl": "en",
        "gl": "US",
        "thirdPartyEmbedUrl": "https://www.google.com",
    },
}

# Cookie cache - avtomatik yangilash uchun
_cookie_cache: Dict[str, Any] = {"cookies": None, "expires": 0}


def _is_proxy_available() -> bool:
    """Proxy ishlayaptimi tekshirish."""
    global _proxy_broken
    if not YOUTUBE_PROXY:
        return False
    if _proxy_broken:
        return False
    return True


def _get_proxy_connector() -> Optional[aiohttp.TCPConnector]:
    """Proxy uchun aiohttp connector yaratish."""
    if not _is_proxy_available():
        return None

    proxy_type = YOUTUBE_PROXY.lower()

    # SOCKS5 proxy
    if proxy_type.startswith("socks5"):
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(YOUTUBE_PROXY)
        except ImportError:
            logger.warning("[Proxy] aiohttp-socks o'rnatilmagan!")
            return None
        except Exception as e:
            logger.warning(f"[Proxy] SOCKS5 connector xatosi: {e}")
            return None

    return None


def _get_proxy_url() -> Optional[str]:
    """aiohttp uchun proxy URL qaytarish."""
    if _is_proxy_available():
        return YOUTUBE_PROXY
    return None


def _mark_proxy_broken():
    """Proxy buzilgan deb belgilash."""
    global _proxy_broken
    if not _proxy_broken:
        _proxy_broken = True
        logger.warning("[Proxy] Proxy ishlamadi, endi proxiesz ishlatamiz!")


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
    """Hugging Face Space ni uyg'otish (sleep mode dan chiqarish).

    HF Space bepul rejimida 5 daqiqadan keyin uxlaydi.
    Uyg'otish uchun GET so'rov yuboramiz va API tayyor bo'lishini kutamiz.
    Cobalt v11+: GET / → serverInfo JSON qaytaradi
    """
    if ".hf.space" not in api_url:
        return True  # HF Space emas, uyg'otish shart emas

    try:
        logger.info("[Cobalt] HF Space uyg'otilmoqda...")
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            # GET / — Cobalt v11 serverInfo qaytaradi, v7 redirect qiladi
            async with session.get(
                api_url.rstrip("/"),
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=True,
            ) as resp:
                status = resp.status
                body = await resp.text()
                logger.info(f"[Cobalt] HF Space javob: HTTP {status}")

                # Agar HTML qaytarsa — Space uyg'onyapti yoki ishlamayapti
                if body.strip().startswith("<"):
                    logger.info("[Cobalt] HF Space uyg'onyapti, 8 sekund kutilmoqda...")
                    await asyncio.sleep(8)

                    # Qayta tekshirish
                    async with session.get(
                        api_url.rstrip("/"),
                        timeout=aiohttp.ClientTimeout(total=20),
                        allow_redirects=True,
                    ) as resp2:
                        body2 = await resp2.text()
                        if body2.strip().startswith("<"):
                            logger.warning("[Cobalt] HF Space hali ham HTML qaytarayapti — API ishlamayapti")
                            return False
                        logger.info("[Cobalt] HF Space API tayyor!")
                        return True

                # JSON javob — Cobalt ishlayapti!
                if '"version"' in body or '"cobalt"' in body:
                    logger.info("[Cobalt] HF Space tayyor!")
                    return True

                return True  # Noma'lum javob, ammo HTML emas — davom etamiz

    except Exception as e:
        logger.warning(f"[Cobalt] HF Space uyg'otish xatosi: {e}")
        return False


async def _try_cobalt(url: str, quality: str = "720", audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """Cobalt API orqali video yuklab olish.

    Cobalt v11 API:
    - POST / (root) — Accept: application/json VA Content-Type: application/json SHART
    - Muvaffaqiyat: {"status":"redirect","url":"..."} yoki {"status":"tunnel","url":"/tunnel?..."}
    - Xato: {"status":"error","error":{"code":"error.api.fetch.fail","context":{...}}}
    """
    # Har safar env dan o'qish (modul yuklanganda emas)
    api_url = os.getenv("COBALT_API_URL", "")
    api_key = os.getenv("COBALT_API_KEY", "")

    if not api_url:
        logger.debug("[Cobalt] COBALT_API_URL o'rnatilmagan")
        return None

    # Faqat rasmiy cobalt.tools API uchun kalit talab qilinadi
    # O'z serverimizda (HF Space) kalit shart emas!
    if not api_key and ("cobalt.tools" in api_url or "api.cobalt.tools" == api_url.rstrip("/")):
        logger.warning("[Cobalt] Rasmiy API uchun COBALT_API_KEY talab qilinadi!")
        return None

    logger.info(f"[Cobalt] {api_url} ga so'rov yuborilmoqda (kalit: {'bor' if api_key else 'yo\'q'})...")

    # HF Space bo'lsa, avval uyg'otamiz
    space_ready = await _wake_hf_space(api_url)
    if not space_ready:
        logger.error("[Cobalt] HF Space ishlamayapti yoki API noto'g'ri o'rnatilgan!")
        return None

    # Cobalt API endpoint
    # v11+: POST / (root) — Accept va Content-Type application/json bo'lishi shart!
    # v7: POST /api/json (endi ishlatilmaydi)
    endpoint = api_url.rstrip('/')

    connector = _get_proxy_connector()
    proxy = _get_proxy_url() if not connector else None

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
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                endpoint,
                json=payload,
                headers=headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                # Avval text o'qymiz — logging va HTML tekshirish uchun
                # Keyin json.loads() bilan parse qilamiz (resp.json() ishlamaydi text dan keyin)
                body = await resp.text()
                logger.info(f"[Cobalt] POST {endpoint} → HTTP {resp.status} - {body[:300]}")

                # HTML javob = noto'g'ri
                if body.strip().startswith("<"):
                    logger.error("[Cobalt] HTML javob — API noto'g'ri sozlangan!")
                    return None

                if resp.status == 401:
                    logger.error("[Cobalt] 401 — API_KEY kerak! HF Space va Render ga qo'shing.")
                    return None

                # Cobalt v11: xatolar ham JSON qaytaradi (HTTP 400)
                # body ni json.loads bilan parse qilamiz
                try:
                    data = json.loads(body)
                except Exception:
                    # JSON parse bo'lmadi
                    if resp.status != 200:
                        logger.warning(f"[Cobalt] HTTP {resp.status} (JSON emas)")
                    else:
                        logger.warning("[Cobalt] JSON parse xatosi")
                    return None

                status = data.get("status", "")

                if status == "error":
                    # Cobalt v11 xato formati: {"status":"error","error":{"code":"error.api.fetch.fail","context":{"service":"youtube"}}}
                    # Cobalt v7: {"status":"error","text":"..."} yoki {"status":"error","error":{"code":"..."}}
                    error_text = data.get("text", "")
                    error_obj = data.get("error", {})

                    if error_text:
                        logger.warning(f"[Cobalt] Xato: {error_text[:200]}")
                        if "cookie" in error_text.lower() or "login" in error_text.lower():
                            logger.error("[Cobalt] YouTube cookie kerak! HF Space ga COOKIE_PATH qo'shing.")
                    elif isinstance(error_obj, dict):
                        error_code = error_obj.get("code", "noma'lum")
                        error_context = error_obj.get("context", {})
                        ctx_str = f" ({error_context})" if error_context else ""
                        logger.warning(f"[Cobalt] Xato kodi: {error_code}{ctx_str}")
                        # YouTube bilan bog'liq xatolarni aniqroq ko'rsatish
                        if "fetch.fail" in str(error_code) and error_context.get("service") == "youtube":
                            logger.error("[Cobalt] YouTube yuklab bo'lmadi — yt-session-generator kerak yoki cookie eski!")
                        elif "cookie" in str(error_code).lower() or "login" in str(error_code).lower():
                            logger.error("[Cobalt] YouTube cookie kerak! HF Space ga COOKIE_PATH qo'shing.")
                    else:
                        logger.warning(f"[Cobalt] Xato: {str(error_obj)[:200]}")
                    return None

                # Yuklash URL — Cobalt v11 javob formatlari:
                # redirect: {"status":"redirect","url":"https://..."}
                # tunnel:   {"status":"tunnel","url":"/tunnel?id=...&sig=..."}
                # picker:   {"status":"picker","picker":[{"url":"..."},...]}
                # local-processing: {"status":"local-processing","tunnel":{...},"output":{...}}
                download_url = None

                # 1. To'g'ridan-to'g'ri URL (redirect yoki tunnel)
                raw_url = data.get("url")
                if raw_url:
                    # Tunnel URL — /tunnel?id=... formatida, to'liq URL yasash
                    if raw_url.startswith("/tunnel"):
                        download_url = f"{api_url.rstrip('/')}{raw_url}"
                    else:
                        download_url = raw_url

                # 2. local-processing (Cobalt v11 audio uchun)
                if not download_url:
                    tunnel_obj = data.get("tunnel")
                    if isinstance(tunnel_obj, dict):
                        # Tunnel obyektdan URL olish
                        download_url = tunnel_obj.get("url") or tunnel_obj.get("proxy")
                        if download_url and download_url.startswith("/"):
                            download_url = f"{api_url.rstrip('/')}{download_url}"

                # 3. Picker (bir nechta variant)
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

    except aiohttp.ClientConnectorError as e:
        logger.warning(f"[Cobalt] Ulanish xatosi: {str(e)[:80]}")
        _mark_proxy_broken()
    except Exception as e:
        logger.warning(f"[Cobalt] Xato: {str(e)[:100]}")

    return None


# ============================================================
# INNERTUBE API - YouTube'ning ichki API si
# ============================================================

async def _try_innertube(video_id: str, quality: str = "720",
                         audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """YouTube InnerTube API orqali to'g'ridan-to'g'ri video ma'lumot olish.

    Bu usul yt-dlp va tashqi servislarga tayanmaydi.
    YouTube'ning o'z ichki API siga murojaat qiladi.

    InnerTube API endpoint: https://www.youtube.com/youtubei/v1/player
    Har xil klient kontekstida (web, android, ios, mweb, tv) urinadi.
    """
    logger.info(f"[InnerTube] Video ID: {video_id}")

    # Klientlarni sinash tartibi - tv birinchi (eng kam cheklov)
    client_order = ["tv", "android", "ios", "mweb", "web"]

    po_token = os.getenv("PO_TOKEN", "")

    for client_key in client_order:
        client_ctx = INNERTUBE_CLIENTS.get(client_key)
        if not client_ctx:
            continue

        try:
            result = await _innertube_player_request(video_id, client_key, client_ctx, po_token)
            if result:
                # Natijani tekshirish - playable bo'lishi kerak
                playability = result.get("playabilityStatus", {})
                status = playability.get("status", "")

                if status == "OK":
                    # Video ma'lumotlari va formatlar bor
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
                        logger.debug(f"[InnerTube] {client_key}: Formatlar topilmadi")
                        continue

                elif status == "LOGIN_REQUIRED":
                    reason = playability.get("reason", "")
                    messages = playability.get("messages", [])
                    logger.warning(
                        f"[InnerTube] {client_key}: LOGIN_REQUIRED - "
                        f"{reason} {' '.join(messages)[:100]}"
                    )
                    # PO Token bilan qayta urinish
                    if not po_token and client_key == "web":
                        logger.info("[InnerTube] PO_TOKEN yo'q, keyingi klientga o'tilmoqda...")
                    continue

                elif status == "UNPLAYABLE":
                    reason = playability.get("reason", "")
                    logger.warning(f"[InnerTube] {client_key}: UNPLAYABLE - {reason}")
                    continue

                else:
                    reason = playability.get("reason", "Noma'lum")
                    logger.warning(f"[InnerTube] {client_key}: {status} - {reason}")
                    continue

        except Exception as e:
            logger.debug(f"[InnerTube] {client_key}: {str(e)[:80]}")
            continue

    logger.warning("[InnerTube] Barcha klientlar muvaffaqiyatsiz")
    return None


async def _innertube_player_request(video_id: str, client_key: str,
                                     client_ctx: Dict, po_token: str = "") -> Optional[Dict]:
    """YouTube InnerTube player API ga so'rov yuborish."""

    api_url = "https://www.youtube.com/youtubei/v1/player"
    params = {
        "prettyPrint": "false",
    }

    # Klient kontekstini nusxalash
    client_info = dict(client_ctx)

    # PO Token qo'shish (faqat web klienti uchun)
    if po_token and client_key == "web":
        client_info["poToken"] = po_token

    # Cookies fayldan SAPISIDHASH olish
    sapisid = ""
    cookies_path = os.getenv("COOKIES_FILE", "cookies.txt")
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

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Origin": "https://www.youtube.com",
        "Referer": f"https://www.youtube.com/watch?v={video_id}",
    }

    # Cookie header qo'shish
    cookie_parts = []
    if sapisid:
        cookie_parts.append(f"SAPISID={sapisid}")
        # SAPISIDHASH yaratish
        import hashlib
        timestamp = str(int(time.time()))
        hash_input = f"{timestamp} {sapisid} https://www.youtube.com"
        sapisidhash = hashlib.sha1(hash_input.encode()).hexdigest()
        headers["Authorization"] = f"SAPISIDHASH {timestamp}_{sapisidhash}"

    if cookie_parts:
        headers["Cookie"] = "; ".join(cookie_parts)

    # Avval proxiesz, keyin proxy bilan
    for use_proxy in [False, True]:
        if use_proxy and not _is_proxy_available():
            continue

        connector = _get_proxy_connector() if use_proxy else None
        proxy = _get_proxy_url() if use_proxy and not connector else None

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    api_url,
                    params=params,
                    json=payload,
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"[InnerTube] {client_key}: HTTP {resp.status}")
                        if use_proxy:
                            continue
                        return None

                    data = await resp.json()
                    return data

        except aiohttp.ClientConnectorError:
            if use_proxy:
                _mark_proxy_broken()
                continue
            return None
        except Exception as e:
            if use_proxy:
                continue
            logger.debug(f"[InnerTube] {client_key} xato: {str(e)[:60]}")
            return None

    return None


def _extract_innertube_download(data: Dict, quality: str = "720",
                                 audio_only: bool = False) -> Tuple[Optional[str], str, Optional[Dict]]:
    """InnerTube natijasidan eng yaxshi yuklab olish URL ini topish.

    Returns:
        (download_url, file_ext, info_dict) yoki (None, "", None)
    """
    streaming_data = data.get("streamingData", {})
    video_details = data.get("videoDetails", {})

    formats = streaming_data.get("formats", [])  # Combined (video+audio)
    adaptive_formats = streaming_data.get("adaptiveFormats", [])  # Separated

    if audio_only:
        # Eng yaxshi audio formatni topish
        audio_fmts = [f for f in adaptive_formats if f.get("mimeType", "").startswith("audio/")]
        if not audio_fmts:
            # Combined formatlardan foydalanish
            audio_fmts = formats

        if not audio_fmts:
            return None, "", None

        # Bitrate bo'yicha saralash
        audio_fmts.sort(key=lambda f: int(f.get("bitrate", 0)), reverse=True)
        best = audio_fmts[0]

        url = best.get("url")
        if not url:
            # Cipher/signature URL ni tekshirish
            cipher = best.get("cipher") or best.get("signatureCipher", "")
            if cipher:
                url = _decrypt_cipher(cipher)

        if url:
            info = _innertube_to_info(video_details, best, audio_only=True)
            return url, "mp3", info

        return None, "", None

    # Video yuklash
    target_height = int(quality.replace("p", ""))

    # Combined formatlar (video+audio) - eng yaxshi tanlov
    combined = [f for f in formats if f.get("url") or f.get("cipher") or f.get("signatureCipher")]
    if combined:
        # Target height ga yaqin combined formatni topish
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

    # Adaptive formatlardan eng yaxshi videoni topish
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
            # Quality label dan olish
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
            # Bir xil height da kattaroq bitrate tanlash
            if int(fmt.get("bitrate", 0)) > int(best.get("bitrate", 0)):
                best = fmt

    return best


def _decrypt_cipher(cipher_text: str) -> Optional[str]:
    """YouTube cipher/signatureCipher dan URL ajratib olish.

    Eslatma: To'liq decrypt qilish uchun YouTube JavaScript player kodini
    tahlil qilish kerak. Bu yerda faqat URL ni ajratib olamiz.
    Agar signature qismlari bo'lsa, ularni qayta ishlash mumkin emas
    (player kodini talab qiladi).

    Amaliy yechim: Invidious orqali proxy qilish.
    """
    from urllib.parse import parse_qs as _parse_qs

    try:
        params = _parse_qs(cipher_text)
        url = params.get("url", [None])[0]
        if url:
            # s (signature) parametri bo'lsa, uni qo'shish
            s = params.get("s", [None])[0]
            sp = params.get("sp", ["signature"])[0]
            if s:
                # Signature bor - to'liq decrypt qilib bo'lmaydi
                # Lekin ba'zi hollarda ishlaydi
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
# INVIDIOUS API - avval proxiesz, keyin proxy bilan
# ============================================================

async def _try_invidious(video_id: str, fast_only: bool = False) -> Optional[Dict[str, Any]]:
    """Invidious API orqali video ma'lumotlarini olish.

    MUHIM: Invidious - bu YouTube alternative frontend.
    U YouTube emas, shuning uchun datacenter IP dan ham ishlashi kerak.
    Avval PROXIESZ sinaymiz (tezroq), keyin proxy bilan.
    """
    instances = _FAST_INVIDIOUS if fast_only else INVIDIOUS_INSTANCES

    # 1-urinish: Proxiesz (tezroq, Invidious o'zi proxy emas)
    for instance in instances:
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                url = f"{instance}/api/v1/videos/{video_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Invidious] {instance}: HTTP {resp.status} (proxiesz)")
                        continue

                    data = await resp.json()
                    formats = data.get("formatStreams", []) + data.get("adaptiveFormats", [])
                    video_formats = [f for f in formats if f.get("type", "").startswith("video")]
                    audio_formats = [f for f in formats if f.get("type", "").startswith("audio")]

                    if not video_formats and not audio_formats:
                        continue

                    logger.info(
                        f"[Invidious] {instance}: MUVOFAQIYATLI (proxiesz) - "
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

    # 2-urinish: Proxy bilan (faqat proxy mavjud bo'lsa)
    if _is_proxy_available():
        connector = _get_proxy_connector()
        proxy = _get_proxy_url() if not connector else None

        for instance in instances[:3]:  # Faqat 3 tasi bilan sinash
            try:
                async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
                    url = f"{instance}/api/v1/videos/{video_id}"
                    async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            continue

                        data = await resp.json()
                        formats = data.get("formatStreams", []) + data.get("adaptiveFormats", [])
                        if formats:
                            logger.info(f"[Invidious] {instance}: MUVOFAQIYATLI (proxy bilan)")
                            return {
                                "source": "invidious",
                                "instance": instance,
                                "data": data,
                                "video_id": video_id,
                            }

            except aiohttp.ClientConnectorError:
                _mark_proxy_broken()
                break
            except Exception:
                continue

    return None


# ============================================================
# PIPED API - avval proxiesz, keyin proxy bilan
# ============================================================

async def _try_piped(video_id: str, fast_only: bool = False) -> Optional[Dict[str, Any]]:
    """Piped API orqali video ma'lumotlarini olish.

    MUHIM: Piped ham YouTube alternative frontend.
    Avval PROXIESZ sinaymiz, keyin proxy bilan.
    """
    instances = _FAST_PIPED if fast_only else PIPED_INSTANCES

    # 1-urinish: Proxiesz
    for instance in instances:
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                url = f"{instance}/streams/{video_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Piped] {instance}: HTTP {resp.status} (proxiesz)")
                        continue

                    data = await resp.json()
                    video_streams = data.get("videoStreams", [])
                    audio_streams = data.get("audioStreams", [])

                    if not video_streams and not audio_streams:
                        continue

                    logger.info(
                        f"[Piped] {instance}: MUVOFAQIYATLI (proxiesz) - "
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

    # 2-urinish: Proxy bilan
    if _is_proxy_available():
        connector = _get_proxy_connector()
        proxy = _get_proxy_url() if not connector else None

        for instance in instances[:2]:
            try:
                async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
                    url = f"{instance}/streams/{video_id}"
                    async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            continue

                        data = await resp.json()
                        video_streams = data.get("videoStreams", [])
                        audio_streams = data.get("audioStreams", [])

                        if video_streams or audio_streams:
                            logger.info(f"[Piped] {instance}: MUVOFAQIYATLI (proxy bilan)")
                            return {
                                "source": "piped",
                                "instance": instance,
                                "data": data,
                                "video_id": video_id,
                            }

            except aiohttp.ClientConnectorError:
                _mark_proxy_broken()
                break
            except Exception:
                continue

    return None


# ============================================================
# INVIDIOUS PROXY DOWNLOAD - Invidious orqali video yuklash
# ============================================================

async def _try_invidious_proxy_download(video_id: str, quality: str = "720",
                                         audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Invidious orqali video yuklash — Invidious o'zi proxy bo'lib ishlaydi.

    Invidious /latest_version endpoint i orqali to'g'ridan-to'g'ri yuklab olish.
    Invidious proksi sifatida ishlaydi — YouTube CDN ga o'z IP sidan murojaat qiladi.
    Bu datacenter IP da ishlashi kerak, chunki YouTube Invidious server IP sini ko'radi.
    """
    instances = _FAST_INVIDIOUS[:6]

    target_height = int(quality.replace("p", "")) if not audio_only else 0

    for instance in instances:
        try:
            # Avval video ma'lumotlarini olish
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                api_url = f"{instance}/api/v1/videos/{video_id}"
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue

                    data = await resp.json()

                    if audio_only:
                        # Audio formatni topish
                        for fmt in data.get("adaptiveFormats", []):
                            if fmt.get("type", "").startswith("audio") and fmt.get("url"):
                                # Invidious local proxy URL
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

async def get_youtube_info_via_api(url: str) -> Optional[Dict[str, Any]]:
    """YouTube video ma'lumotlarini API orqali olish.

    STRATEGIYA:
    1. Cobalt API - o'z serverimiz, eng ishonchli
    2. InnerTube API - YouTube'ning ichki API si
    3. Invidious - tezkor, keyin to'liq
    4. Piped - tezkor, keyin to'liq
    """
    video_id = _extract_video_id(url)
    if not video_id:
        logger.error("[API] Video ID topilmadi")
        return None

    logger.info(f"[API] Video ID: {video_id}")

    # === 1-USUL: Cobalt API (o'z serverimiz) ===
    cobalt_result = await _try_cobalt(url, "720", False)
    if cobalt_result and cobalt_result.get("download_url"):
        logger.info("[API] Cobalt orqali video mavjudligi tasdiqlandi!")
        # Cobalt to'liq info bermaydi, lekin mavjudligini tasdiqlaydi
        return {
            "source": "cobalt",
            "data": {
                "title": "YouTube Video",
                "download_url": cobalt_result["download_url"],
            },
            "video_id": video_id,
            "_cobalt_available": True,
        }
    else:
        logger.info("[API] Cobalt ishlamadi, keyingi usulga o'tilmoqda...")

    # === 2-USUL: InnerTube API (YouTube ichki API) ===
    innertube_result = await _try_innertube(video_id, "720", False)
    if innertube_result:
        logger.info("[API] InnerTube orqali ma'lumot olindi!")
        return innertube_result

    # === 3-USUL: Invidious (tezkor) ===
    result = await _try_invidious(video_id, fast_only=True)
    if result:
        return result

    # === 4-USUL: Piped (tezkor) ===
    result = await _try_piped(video_id, fast_only=True)
    if result:
        return result

    # === 5-USUL: Invidious (to'liq) ===
    result = await _try_invidious(video_id, fast_only=False)
    if result:
        return result

    # === 6-USUL: Piped (to'liq) ===
    result = await _try_piped(video_id, fast_only=False)
    if result:
        return result

    logger.error("[API] Barcha API serverlar ishlamadi")
    return None


async def download_youtube_via_api(url: str, quality: str = "720",
                                    audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube videosini API orqali yuklab olish.

    STRATEGIYA:
    1. Cobalt API - o'z serverimiz
    2. InnerTube API - YouTube ichki API
    3. Invidious proxy download - Invidious orqali proksi yuklash
    4. Invidious/Piped API orqali yuklash
    """
    video_id = _extract_video_id(url)

    # 1: COBALT
    logger.info("[API] 1-usul: Cobalt orqali yuklanmoqda...")
    cobalt_result = await _try_cobalt(url, quality, audio_only)
    if cobalt_result:
        download_url = cobalt_result.get("download_url")
        if download_url:
            result = await _download_from_url(download_url, video_id, audio_only)
            if result:
                info = _make_basic_info(url, video_id, audio_only)
                return result, info
            else:
                logger.warning("[API] Cobalt URL topildi lekin yuklab bo'lmadi")
        else:
            logger.warning("[API] Cobalt javobida download_url yo'q")
    else:
        logger.info("[API] Cobalt ishlamadi, keyingi usulga o'tilmoqda...")

    # 2: INNERTUBE
    logger.info("[API] 2-usul: InnerTube orqali yuklanmoqda...")
    innertube_result = await _try_innertube(video_id, quality, audio_only)
    if innertube_result:
        download_url, file_ext, fmt_info = _extract_innertube_download(
            innertube_result["data"], quality, audio_only
        )
        if download_url:
            result = await _download_from_url(download_url, video_id, audio_only, file_ext)
            if result:
                info = fmt_info or _make_basic_info(url, video_id, audio_only)
                return result, info
            else:
                logger.warning("[API] InnerTube URL topildi lekin yuklab bo'lmadi")
    else:
        logger.info("[API] InnerTube ishlamadi, keyingi usulga o'tilmoqda...")

    # 3: INVIDIOUS PROXY DOWNLOAD
    logger.info("[API] 3-usul: Invidious proxy orqali yuklanmoqda...")
    inv_proxy_result = await _try_invidious_proxy_download(video_id, quality, audio_only)
    if inv_proxy_result:
        logger.info("[API] Invidious proxy orqali yuklash MUVOFAQIYATLI!")
        return inv_proxy_result

    # 4: INVIDIOUS API
    logger.info("[API] 4-usul: Invidious orqali yuklanmoqda...")
    inv_result = await _try_invidious(video_id, fast_only=True)
    if inv_result:
        download_url, file_ext = _find_best_invidious_download(inv_result["data"], quality, audio_only)
        if download_url:
            result = await _download_from_url(download_url, video_id, audio_only, file_ext)
            if result:
                info = convert_api_info_to_ytdlp(inv_result)
                return result, info

    # 5: PIPED
    logger.info("[API] 5-usul: Piped orqali yuklanmoqda...")
    piped_result = await _try_piped(video_id, fast_only=True)
    if piped_result:
        download_url, file_ext = _find_best_piped_download(piped_result["data"], quality, audio_only)
        if download_url:
            result = await _download_from_url(download_url, video_id, audio_only, file_ext)
            if result:
                info = convert_api_info_to_ytdlp(piped_result)
                return result, info

    logger.error("[API] Barcha yuklash usullari muvaffaqiyatsiz")
    return None


async def _download_from_url(url: str, video_id: str, audio_only: bool,
                              file_ext: str = None) -> Optional[str]:
    """To'g'ridan-to'g'ri URL dan fayl yuklab olish."""
    output_path = tempfile.mkdtemp()

    if not file_ext:
        file_ext = "mp3" if audio_only else "mp4"

    file_name = f"{video_id or 'video'}.{file_ext}"
    file_path = os.path.join(output_path, file_name)

    logger.info(f"[API] Fayl yuklanmoqda: {file_ext}...")

    try:
        from app.config import config as app_config
        max_size = app_config.download.max_file_size_mb * 1024 * 1024

        # Avval proxiesz, keyin proxy bilan
        for use_proxy in [False, True]:
            if use_proxy and not _is_proxy_available():
                continue

            connector = _get_proxy_connector() if use_proxy else None
            proxy = _get_proxy_url() if use_proxy and not connector else None

            try:
                async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
                    async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status != 200:
                            if use_proxy:
                                continue
                            logger.error(f"[API] Yuklash HTTP xatosi: {resp.status}")
                            return None

                        total_size = 0
                        with open(file_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(65536):  # 64KB — tezroq
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
                    _mark_proxy_broken()
                    continue
                logger.error("[API] Ulanish xatosi")
                return None
            except Exception as e:
                if use_proxy:
                    continue
                logger.error(f"[API] Yuklash xatosi: {e}")
                return None

    except Exception as e:
        logger.error(f"[API] Yuklash xatosi: {e}")

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
        # Cobalt to'liq info bermaydi, basic info yaratamiz
        return _make_basic_info(
            f"https://www.youtube.com/watch?v={video_id}",
            video_id,
            False
        )
    elif source == "innertube":
        # InnerTube ma'lumotlarini konvertatsiya qilish
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
