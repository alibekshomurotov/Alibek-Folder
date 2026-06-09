import logging
import os
import tempfile
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs

import aiohttp

logger = logging.getLogger(__name__)

# Invidious instances - these are public API servers that proxy YouTube
# They run on residential IPs and bypass YouTube's bot detection
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacyredirect.com",
    "https://iv.datura.network",
    "https://invidious.jing.rocks",
    "https://invidious.protokolla.fi",
    "https://yt.cdaut.de",
    "https://invidious.perennialte.ch",
    "https://inv.tux.pizza",
    "https://vid.puffyan.us",
]

# Piped instances - alternative YouTube frontend with API
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.r4fo.com",
    "https://api.piped.yt",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.leptons.xyz",
]


def _extract_video_id(url: str) -> Optional[str]:
    """YouTube video ID sini URL dan ajratib olish."""
    parsed = urlparse(url)

    # youtube.com/watch?v=ID
    if "youtube.com" in parsed.netloc:
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            return qs.get("v", [None])[0]
        # youtube.com/shorts/ID
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/")[1].split("?")[0]
        # youtube.com/embed/ID
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/embed/")[1].split("?")[0]

    # youtu.be/ID
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/").split("?")[0]

    return None


async def _try_invidious(video_id: str) -> Optional[Dict[str, Any]]:
    """Invidious API orqali video ma'lumotlarini olish."""
    for instance in INVIDIOUS_INSTANCES:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{instance}/api/v1/videos/{video_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        continue

                    data = await resp.json()

                    # Video formatlar borligini tekshirish
                    formats = data.get("formatStreams", []) + data.get("adaptiveFormats", [])
                    video_formats = [f for f in formats if f.get("type", "").startswith("video")]
                    audio_formats = [f for f in formats if f.get("type", "").startswith("audio")]

                    if not video_formats and not audio_formats:
                        logger.debug(f"[Invidious] {instance}: formatlar topilmadi")
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
            logger.debug(f"[Invidious] {instance}: xato - {str(e)[:80]}")
            continue

    logger.warning("[Invidious] Barcha serverlar ishlamadi")
    return None


async def _try_piped(video_id: str) -> Optional[Dict[str, Any]]:
    """Piped API orqali video ma'lumotlarini olish."""
    for instance in PIPED_INSTANCES:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{instance}/streams/{video_id}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        continue

                    data = await resp.json()

                    video_streams = data.get("videoStreams", [])
                    audio_streams = data.get("audioStreams", [])

                    if not video_streams and not audio_streams:
                        logger.debug(f"[Piped] {instance}: formatlar topilmadi")
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
            logger.debug(f"[Piped] {instance}: xato - {str(e)[:80]}")
            continue

    logger.warning("[Piped] Barcha serverlar ishlamadi")
    return None


async def get_youtube_info_via_api(url: str) -> Optional[Dict[str, Any]]:
    """Invidious yoki Piped API orqali YouTube video ma'lumotlarini olish.

    Bu API lar o'z serverlarida (oddiy uy IP bilan) ishlaydi,
    shuning uchun YouTube'ning bot-aniqlashidan o'tadi.
    """
    video_id = _extract_video_id(url)
    if not video_id:
        logger.error("[API] Video ID topilmadi")
        return None

    logger.info(f"[API] Video ID: {video_id}, Invidious/Piped orqali qidirilmoqda...")

    # Avval Invidious, keyin Piped
    result = await _try_invidious(video_id)
    if result:
        return result

    result = await _try_piped(video_id)
    if result:
        return result

    logger.error("[API] Invidious va Piped ham ishlamadi")
    return None


def convert_api_info_to_ytdlp(api_result: Dict[str, Any]) -> Dict[str, Any]:
    """Invidious/Piped ma'lumotlarini yt-dlp formatiga aylantirish.

    Bu kerak, chunki qolgan kod (video handler, formatter) yt-dlp formatida
    ma'lumot kutadi.
    """
    source = api_result["source"]
    data = api_result["data"]
    video_id = api_result["video_id"]

    if source == "invidious":
        return _convert_invidious(data, video_id)
    else:
        return _convert_piped(data, video_id)


def _convert_invidious(data: Dict, video_id: str) -> Dict[str, Any]:
    """Invidious formatini yt-dlp formatiga aylantirish."""
    info = {
        "id": video_id,
        "title": data.get("title", "Noma'lum"),
        "description": data.get("description", ""),
        "duration": data.get("lengthSeconds", 0),
        "view_count": data.get("viewCount", 0),
        "like_count": data.get("likeCount", 0),
        "uploader": data.get("author", "Noma'lum"),
        "uploader_id": data.get("authorId", ""),
        "thumbnail": data.get("videoThumbnails", [{}])[0].get("url", ""),
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "extractor": "youtube",
        "formats": [],
    }

    # Formatlarni aylantirish
    for fmt in data.get("formatStreams", []):
        info["formats"].append({
            "format_id": fmt.get("itag", "unknown"),
            "url": fmt.get("url", ""),
            "ext": fmt.get("container", fmt.get("type", "").split("/")[-1] if "/" in fmt.get("type", "") else "mp4"),
            "height": fmt.get("qualityLabel", "").replace("p", "") if "p" in fmt.get("qualityLabel", "") else None,
            "vcodec": fmt.get("type", "").startswith("video") and "avc1" or "unknown",
            "acodec": "unknown",
            "filesize": fmt.get("clen") and int(fmt.get("clen", 0)) or None,
        })

    for fmt in data.get("adaptiveFormats", []):
        fmt_type = fmt.get("type", "video/mp4")
        is_video = fmt_type.startswith("video")
        is_audio = fmt_type.startswith("audio")

        info["formats"].append({
            "format_id": fmt.get("itag", "unknown"),
            "url": fmt.get("url", ""),
            "ext": fmt.get("container", fmt_type.split("/")[-1] if "/" in fmt_type else "mp4"),
            "height": fmt.get("qualityLabel", "").replace("p", "") if "p" in fmt.get("qualityLabel", "") else None,
            "vcodec": "none" if is_audio else "unknown",
            "acodec": "none" if is_video else "unknown",
            "filesize": fmt.get("clen") and int(fmt.get("clen", 0)) or None,
        })

    return info


def _convert_piped(data: Dict, video_id: str) -> Dict[str, Any]:
    """Piped formatini yt-dlp formatiga aylantirish."""
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
            "ext": stream.get("mimeType", "video/mp4").split(";")[0].split("/")[-1],
            "height": stream.get("quality"),
            "vcodec": "unknown",
            "acodec": "none",
            "filesize": None,
        })

    for stream in data.get("audioStreams", []):
        info["formats"].append({
            "format_id": str(stream.get("itag", "unknown")),
            "url": stream.get("url", ""),
            "ext": stream.get("mimeType", "audio/mp4").split(";")[0].split("/")[-1],
            "height": None,
            "vcodec": "none",
            "acodec": "unknown",
            "filesize": None,
        })

    return info


async def download_youtube_via_api(url: str, quality: str = "720",
                                    audio_only: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Invidious/Piped API orqali YouTube videosini yuklab olish.

    1. API dan video ma'lumotlarini olish
    2. Eng yaxshi formatni tanlash
    3. To'g'ridan-to'g'ri URL dan yuklab olish (yt-dlp kerak EMAS)
    """
    import shutil

    api_result = await get_youtube_info_via_api(url)
    if not api_result:
        return None

    source = api_result["source"]
    data = api_result["data"]

    # Eng yaxshi yuklab olish URL ini topish
    download_url = None
    file_ext = "mp4"
    info = convert_api_info_to_ytdlp(api_result)

    if audio_only:
        download_url, file_ext = _find_best_audio_url(data, source)
    else:
        download_url, file_ext = _find_best_video_url(data, source, quality)

    if not download_url:
        logger.error("[API] Yuklab olish URL topilmadi")
        return None

    # Faylni yuklab olish
    output_path = tempfile.mkdtemp()
    file_name = f"{api_result['video_id']}.{file_ext}"
    file_path = os.path.join(output_path, file_name)

    logger.info(f"[API] Yuklab olinmoqda: {file_ext} | Quality: {quality}p | Audio: {audio_only}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    logger.error(f"[API] Yuklash xatosi: HTTP {resp.status}")
                    return None

                max_size = config_download_max_size()
                total_size = 0

                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        total_size += len(chunk)
                        if total_size > max_size:
                            logger.error(f"[API] Fayl juda katta: {total_size / 1024 / 1024:.1f}MB")
                            os.remove(file_path)
                            return None
                        f.write(chunk)

                if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                    logger.error("[API] Yuklangan fayl bo'sh")
                    return None

                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                logger.info(f"[API] Yuklash MUVOFAQIYATLI: {file_size_mb:.1f}MB")

                return file_path, info

    except Exception as e:
        logger.error(f"[API] Yuklash xatosi: {e}")
        return None


def _find_best_video_url(data: Dict, source: str, quality: str = "720") -> Tuple[Optional[str], str]:
    """Eng yaxshi video URL ini topish."""
    target_height = int(quality.replace("p", ""))

    if source == "invidious":
        return _find_best_invidious_video(data, target_height)
    else:
        return _find_best_piped_video(data, target_height)


def _find_best_invidious_video(data: Dict, target_height: int) -> Tuple[Optional[str], str]:
    """Invidious dan eng yaxshi video URL ini topish."""
    # formatStreams - bu audio+video birgalikda (pre-merged)
    # Bular eng yaxshi, chunki FFmpeg kerak emas
    format_streams = data.get("formatStreams", [])

    # Target height ga eng yaqin formatni topish
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

    # Agar target height da topilmasa, pastroq sifatni tanlash
    if not best:
        for fmt in format_streams:
            quality_label = fmt.get("qualityLabel", "")
            if quality_label:
                best = fmt
                break

    if best and best.get("url"):
        return best["url"], "mp4"

    # Agar formatStreams bo'sh bo'lsa, adaptiveFormats dan yuklash
    # Buning uchun FFmpeg kerak bo'ladi, hozircha oddiy video qaytaramiz
    logger.warning("[Invidious] formatStreams topilmadi, adaptiveFormats sinab ko'rilmoqda...")
    return None, "mp4"


def _find_best_piped_video(data: Dict, target_height: int) -> Tuple[Optional[str], str]:
    """Piped dan eng yaxshi video URL ini topish."""
    video_streams = data.get("videoStreams", [])

    # Video+audio birga bo'lgan streamlarni afzal ko'rish
    # Piped da videoStreams odatda video only, lekin ba'zida birga ham bo'ladi

    best = None
    best_diff = 99999

    for stream in video_streams:
        quality = stream.get("quality")
        if not quality:
            continue

        try:
            height = int(quality)
        except (ValueError, TypeError):
            continue

        diff = abs(height - target_height)
        if diff < best_diff:
            best_diff = diff
            best = stream

    if best and best.get("url"):
        mime = best.get("mimeType", "video/mp4")
        ext = mime.split(";")[0].split("/")[-1]
        if ext == "webm":
            ext = "mp4"  # Telegram webm ni yoqtirmaydi
        return best["url"], ext

    return None, "mp4"


def _find_best_audio_url(data: Dict, source: str) -> Tuple[Optional[str], str]:
    """Eng yaxshi audio URL ini topish (MP3 uchun)."""
    if source == "invidious":
        # Invidious da adaptiveFormats dan audio qidirish
        for fmt in data.get("adaptiveFormats", []):
            if fmt.get("type", "").startswith("audio"):
                if fmt.get("url"):
                    return fmt["url"], "mp3"
        # Audio topilmasa, video+audio formatni qaytarish
        for fmt in data.get("formatStreams", []):
            if fmt.get("url"):
                return fmt["url"], "mp3"
    else:
        # Piped da audioStreams
        audio_streams = data.get("audioStreams", [])
        if audio_streams:
            # Eng yuqori sifatli audio
            best = audio_streams[0]
            for stream in audio_streams:
                if stream.get("quality") and best.get("quality"):
                    try:
                        if int(stream["quality"]) > int(best["quality"]):
                            best = stream
                    except (ValueError, TypeError):
                        pass
            if best.get("url"):
                return best["url"], "mp3"

    return None, "mp3"


def config_download_max_size() -> int:
    """Max fayl hajmini olish."""
    from app.config import config
    return config.download.max_file_size_mb * 1024 * 1024
