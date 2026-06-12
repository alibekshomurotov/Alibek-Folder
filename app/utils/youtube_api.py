"""YouTube alternative downloader using Cobalt, Invidious, Piped APIs.

Cobalt (cobalt.tools) - eng ishonchli, maxsus YouTube yuklash uchun yaratilgan.
Invidious/Piped - alternative YouTube frontendlar.

MUHIM: YOUTUBE_PROXY env orqali SOCKS5/HTTP proxy berish mumkin.
Bu Render kabi datacenter IP larda YouTube yuklash uchun kerak.

MUHIM: Agar proxy buzilgan bo'lsa, kod avtomatik proxiesz urinadi!
"""

import logging
import os
import tempfile
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs

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

# Invidious instances - 2026 yil iyunda yangilangan
INVIDIOUS_INSTANCES = [
    # Eng ishonchli
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
    "https://invidious.fdn.fr",
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

_FAST_INVIDIOUS = INVIDIOUS_INSTANCES[:4]  # 4 ta (8 juda sekin)
_FAST_PIPED = PIPED_INSTANCES[:3]  # 3 ta


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
        logger.warning(f"[Proxy] Proxy ishlamadi, endi proxiesz ishlatamiz!")


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
    """
    if ".hf.space" not in api_url:
        return True  # HF Space emas, uyg'otish shart emas

    try:
        logger.info(f"[Cobalt] HF Space uyg'otilmoqda...")
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            # Space sahifasini ochish — uyg'onishni boshlaydi
            async with session.get(
                api_url.rstrip("/"),
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                status = resp.status
                logger.info(f"[Cobalt] HF Space javob: HTTP {status}")

                # Agar HTML qaytarsa — Space uyg'onyapti yoki ishlamayapti
                body = await resp.text()
                if body.strip().startswith("<"):
                    # Space uyg'onyapti — biroz kutamiz
                    import asyncio
                    logger.info("[Cobalt] HF Space uyg'onyapti, 5 sekund kutilmoqda...")
                    await asyncio.sleep(5)

                    # Qayta tekshirish
                    async with session.get(
                        f"{api_url.rstrip('/')}/api/json",
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp2:
                        body2 = await resp2.text()
                        if body2.strip().startswith("<"):
                            logger.warning("[Cobalt] HF Space hali ham HTML qaytarayapti — API ishlamayapti")
                            return False
                        logger.info("[Cobalt] HF Space API tayyor!")
                        return True

                return True  # HTML emas — tayyor

    except Exception as e:
        logger.warning(f"[Cobalt] HF Space uyg'otish xatosi: {e}")
        return False


async def _try_cobalt(url: str, quality: str = "720", audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """Cobalt API orqali video yuklab olish."""
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

    # Cobalt v7+ API endpoint
    # v7.15+: /api/json ishlaydi (GET / → /api/serverInfo ga redirect)
    endpoint = f"{api_url.rstrip('/')}/api/json"

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
                body = await resp.text()
                logger.info(f"[Cobalt] POST {endpoint} → HTTP {resp.status} - {body[:300]}")

                # HTML javob = noto'g'ri
                if body.strip().startswith("<"):
                    logger.error("[Cobalt] HTML javob — API noto'g'ri sozlangan!")
                    return None

                if resp.status == 401:
                    logger.error("[Cobalt] 401 — API_KEY kerak! HF Space va Render ga qo'shing.")
                    return None

                if resp.status != 200:
                    logger.warning(f"[Cobalt] HTTP {resp.status}")
                    return None

                try:
                    data = await resp.json()
                except Exception:
                    logger.warning("[Cobalt] JSON parse xatosi")
                    return None

                status = data.get("status", "")

                if status == "error":
                    # Cobalt v7.15 xato formati: {"status":"error","text":"..."}
                    # yoki {"status":"error","error":{"code":"..."}}
                    error_text = data.get("text", "")
                    if error_text:
                        logger.warning(f"[Cobalt] Xato: {error_text[:200]}")
                        if "cookie" in error_text.lower() or "login" in error_text.lower():
                            logger.error("[Cobalt] YouTube cookie kerak! HF Space ga COOKIE_PATH qo'shing.")
                    else:
                        error_obj = data.get("error", {})
                        if isinstance(error_obj, dict):
                            error_code = error_obj.get("code", "noma'lum")
                        else:
                            error_code = str(error_obj)
                        logger.warning(f"[Cobalt] Xato kodi: {error_code}")
                        if "cookie" in str(error_code).lower() or "login" in str(error_code).lower():
                            logger.error("[Cobalt] YouTube cookie kerak! HF Space ga COOKIE_PATH qo'shing.")
                    return None

                # Yuklash URL
                download_url = data.get("url")
                if not download_url:
                    tunnel = data.get("tunnel")
                    if tunnel:
                        download_url = tunnel
                if not download_url:
                    picker = data.get("picker", [])
                    if picker and isinstance(picker, list):
                        download_url = picker[0].get("url")

                if download_url:
                    logger.info("[Cobalt] Yuklash URL topildi!")
                    return {
                        "source": "cobalt",
                        "download_url": download_url,
                        "audio_only": audio_only,
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
# ASOSIY FUNKSIYALAR
# ============================================================

async def get_youtube_info_via_api(url: str) -> Optional[Dict[str, Any]]:
    """YouTube video ma'lumotlarini API orqali olish.

    STRATEGIYA:
    1. Cobalt API - o'z serverimiz, eng ishonchli
    2. Invidious - tezkor, keyin to'liq
    3. Piped - tezkor, keyin to'liq
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
        logger.info("[API] Cobalt ishlamadi, Invidious/Piped sinab ko'rilmoqda...")

    # === 2-USUL: Invidious (tezkor) ===
    result = await _try_invidious(video_id, fast_only=True)
    if result:
        return result

    # === 3-USUL: Piped (tezkor) ===
    result = await _try_piped(video_id, fast_only=True)
    if result:
        return result

    # === 4-USUL: Invidious (to'liq) ===
    result = await _try_invidious(video_id, fast_only=False)
    if result:
        return result

    # === 5-USUL: Piped (to'liq) ===
    result = await _try_piped(video_id, fast_only=False)
    if result:
        return result

    logger.error("[API] Barcha API serverlar ishlamadi")
    return None


async def download_youtube_via_api(url: str, quality: str = "720",
                                    audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """YouTube videosini API orqali yuklab olish."""
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
        logger.info("[API] Cobalt ishlamadi, Invidious/Piped sinab ko'rilmoqda...")

    # 2: INVIDIOUS
    logger.info("[API] 2-usul: Invidious orqali yuklanmoqda...")
    inv_result = await _try_invidious(video_id, fast_only=True)
    if inv_result:
        download_url, file_ext = _find_best_invidious_download(inv_result["data"], quality, audio_only)
        if download_url:
            result = await _download_from_url(download_url, video_id, audio_only, file_ext)
            if result:
                info = convert_api_info_to_ytdlp(inv_result)
                return result, info

    # 3: PIPED
    logger.info("[API] 3-usul: Piped orqali yuklanmoqda...")
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
                            async for chunk in resp.content.iter_chunked(8192):
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
                logger.error(f"[API] Ulanish xatosi")
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