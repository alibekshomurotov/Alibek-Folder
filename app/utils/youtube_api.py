import logging
import os
import tempfile
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs

import aiohttp

logger = logging.getLogger(__name__)

# Cobalt API - JWT autentifikatsiya talab qiladi
COBALT_API_URL = os.getenv("COBALT_API_URL", "https://api.cobalt.tools")
COBALT_API_KEY = os.getenv("COBALT_API_KEY", "")

# Proxy sozlamalari
YOUTUBE_PROXY = os.getenv("YOUTUBE_PROXY", "") or os.getenv("HTTP_PROXY", "") or os.getenv("HTTPS_PROXY", "")

# Invidious instances - 2026 yil iyunda yangilangan
# Ba'zi instancalar datacenter IP lardan bloklaydi,
# shuning uchun ko'proq alternative serverlar kerak
INVIDIOUS_INSTANCES = [
    # Eng ishonchli (ko'p ishlaydi)
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
    "https://inv.tux.pizza",
    "https://invidious.fdn.fr",
    "https://iv.ggtyler.dev",
    "https://invidious.privacyredirect.com",
    "https://inv.oikei.net",
    "https://yewtu.be",
    "https://invidious.privacy.de",
    "https://vid.puffyan.us",
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


def _get_proxy_connector() -> Optional[aiohttp.TCPConnector]:
    """Proxy uchun aiohttp connector yaratish."""
    if not YOUTUBE_PROXY:
        return None

    proxy_type = YOUTUBE_PROXY.lower()

    # SOCKS5 proxy
    if proxy_type.startswith("socks5"):
        try:
            from aiohttp_socks import ProxyConnector
            logger.info(f"[Proxy] SOCKS5 ishlatilmoqda: {YOUTUBE_PROXY.split('@')[-1] if '@' in YOUTUBE_PROXY else YOUTUBE_PROXY}")
            return ProxyConnector.from_url(YOUTUBE_PROXY)
        except ImportError:
            logger.warning("[Proxy] aiohttp-socks o'rnatilmagan! pip install aiohttp-socks")
            return None
        except Exception as e:
            logger.warning(f"[Proxy] SOCKS5 connector xatosi: {e}")
            return None

    # HTTP/HTTPS proxy
    if proxy_type.startswith("http"):
        # HTTP proxy uchun maxsus connector kerak emas
        # aiohttp session ga proxy parametri uzatiladi
        return None

    return None


def _get_proxy_url() -> Optional[str]:
    """aiohttp uchun proxy URL qaytarish."""
    if YOUTUBE_PROXY:
        return YOUTUBE_PROXY
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

async def _try_cobalt(url: str, quality: str = "720", audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """Cobalt API orqali video yuklab olish."""
    api_url = COBALT_API_URL

    if not COBALT_API_KEY and "cobalt.tools" in api_url:
        logger.warning("[Cobalt] API kalit yo'q! COBALT_API_KEY env o'zgaruvchisini o'rnating.")
        return None

    try:
        connector = _get_proxy_connector()
        async with aiohttp.ClientSession(connector=connector) as session:
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

            if COBALT_API_KEY:
                headers["Authorization"] = f"Bearer {COBALT_API_KEY}"

            proxy = _get_proxy_url() if not connector else None

            logger.info(f"[Cobalt] {api_url} ga so'rov yuborilmoqda...")
            async with session.post(
                f"{api_url}/",
                json=payload,
                headers=headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                body = await resp.text()
                logger.info(f"[Cobalt] {api_url}: HTTP {resp.status} - {body[:200]}")

                if resp.status == 400 and "jwt.missing" in body:
                    logger.error("[Cobalt] JWT autentifikatsiya talab qilinadi!")
                    return None

                if resp.status != 200:
                    return None

                try:
                    data = await resp.json()
                except Exception:
                    logger.warning("[Cobalt] JSON parse xatosi")
                    return None

                status = data.get("status", "")

                if status == "error":
                    error_code = data.get("error", {}).get("code", "noma_lum") if isinstance(data.get("error"), dict) else str(data.get("error", ""))
                    logger.warning(f"[Cobalt] {api_url}: Xato - {error_code}")
                    return None

                download_url = data.get("url")

                if not download_url:
                    picker = data.get("picker", [])
                    if picker and isinstance(picker, list):
                        download_url = picker[0].get("url")

                if download_url:
                    logger.info(f"[Cobalt] {api_url}: Yuklash URL topildi!")
                    return {
                        "source": "cobalt",
                        "download_url": download_url,
                        "audio_only": audio_only,
                    }
                else:
                    logger.warning(f"[Cobalt] {api_url}: URL topilmadi")

    except Exception as e:
        logger.warning(f"[Cobalt] {api_url}: Xato - {str(e)[:100]}")

    return None


# ============================================================
# INVIDIOUS API - proxy bilan
# ============================================================

_FAST_INVIDIOUS = INVIDIOUS_INSTANCES[:6]  # 6 ta tezkor Invidious
_FAST_PIPED = PIPED_INSTANCES[:4]  # 4 ta tezkor Piped


async def _try_invidious(video_id: str, fast_only: bool = False) -> Optional[Dict[str, Any]]:
    """Invidious API orqali video ma'lumotlarini olish (proxy bilan)."""
    instances = _FAST_INVIDIOUS if fast_only else INVIDIOUS_INSTANCES
    connector = _get_proxy_connector()
    proxy = _get_proxy_url() if not connector else None

    for instance in instances:
        try:
            async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
                url = f"{instance}/api/v1/videos/{video_id}"
                async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.info(f"[Invidious] {instance}: HTTP {resp.status}")
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
# PIPED API - proxy bilan
# ============================================================

async def _try_piped(video_id: str, fast_only: bool = False) -> Optional[Dict[str, Any]]:
    """Piped API orqali video ma'lumotlarini olish (proxy bilan)."""
    instances = _FAST_PIPED if fast_only else PIPED_INSTANCES
    connector = _get_proxy_connector()
    proxy = _get_proxy_url() if not connector else None

    for instance in instances:
        try:
            async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
                url = f"{instance}/streams/{video_id}"
                async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.info(f"[Piped] {instance}: HTTP {resp.status}")
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
# ASOSIY FUNKSIYALAR
# ============================================================

async def get_youtube_info_via_api(url: str) -> Optional[Dict[str, Any]]:
    """YouTube video ma'lumotlarini API orqali olish."""
    video_id = _extract_video_id(url)
    if not video_id:
        logger.error("[API] Video ID topilmadi")
        return None

    logger.info(f"[API] Video ID: {video_id}")

    # Tezkor urinish
    result = await _try_invidious(video_id, fast_only=True)
    if result:
        return result

    result = await _try_piped(video_id, fast_only=True)
    if result:
        return result

    # To'liq urinish
    result = await _try_invidious(video_id, fast_only=False)
    if result:
        return result

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
        result = await _download_from_url(cobalt_result["download_url"], video_id, audio_only)
        if result:
            info = _make_basic_info(url, video_id, audio_only)
            return result, info

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

        connector = _get_proxy_connector()
        proxy = _get_proxy_url() if not connector else None

        async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
            async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
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

    if source == "invidious":
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
