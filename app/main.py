"""Video Downloader Pro - Main Entry Point"""

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.config import config
from app.database import init_db, close_db

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


async def start_health_server():
    """Start a simple HTTP health check server (required by Render Web Service)"""
    from aiohttp import web

    async def health_handler(request):
        return web.Response(text="OK - Video Downloader Pro bot is running", status=200)

    async def debug_cookies_handler(request):
        """Debug endpoint to check cookies and yt-dlp status"""
        from app.utils.downloader import _find_cookies_file, _validate_instagram_cookies
        import yt_dlp as _ydl

        path = _find_cookies_file()
        result = {
            "yt_dlp_version": _ydl.version.__version__,
            "cookies_path": path,
        }

        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                yt_cookies = [l for l in lines if "youtube.com" in l.lower() and not l.startswith("#")]
                ig_cookies = [l for l in lines if "instagram.com" in l.lower() and not l.startswith("#")]
                critical = ["__Secure-1PSID", "__Secure-3PSID", "SID", "HSID", "SSID", "SAPISID"]
                cookie_text = "".join(lines)
                found_critical = [c for c in critical if c in cookie_text]
                missing_critical = [c for c in critical if c not in cookie_text]

                ig_validation = _validate_instagram_cookies(path)

                result.update({
                    "status": "cookies_found",
                    "total_lines": len(lines),
                    "youtube_cookies": len(yt_cookies),
                    "instagram_cookies": len(ig_cookies),
                    "found_critical_yt": found_critical,
                    "missing_critical_yt": missing_critical,
                    "instagram_story_ready": ig_validation["valid"],
                    "instagram_missing_cookies": ig_validation["missing"],
                    "instagram_found_critical": ig_validation["found_critical"],
                    "instagram_found_useful": ig_validation["found_useful"],
                    "instagram_total_cookies": ig_validation["total_ig_cookies"],
                })
            except Exception as e:
                result.update({"status": "error", "error": str(e)})
        else:
            result.update({
                "status": "no_cookies",
                "message": "No cookies.txt found!",
            })

        return web.json_response(result)

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/debug/cookies", debug_cookies_handler)
    port = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check server started on port {port}")


async def main():
    """Main function to start the bot"""
    if not config.bot.token:
        logger.error("BOT_TOKEN is not set! Please set it in .env file or environment variables.")
        sys.exit(1)

    await start_health_server()

    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized.")

    import yt_dlp as _ydl
    yt_version = _ydl.version.__version__
    logger.info(f"[yt-dlp] Running version: {yt_version}")

    try:
        year, month = yt_version.split(".")[:2]
        if int(year) < 2026 or (int(year) == 2025 and int(month) < 12):
            logger.warning(
                f"[yt-dlp] Version {yt_version} is TOO OLD for current YouTube! "
                f"YouTube will NOT work. Ensure __main__.py or start.sh upgrades yt-dlp."
            )
    except (ValueError, IndexError):
        pass

    from app.utils.downloader import log_cookies_status
    log_cookies_status()

    bot = Bot(
        token=config.bot.token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher()

    from app.middleware.auth import AuthMiddleware
    from app.middleware.throttle import ThrottleMiddleware
    from app.middleware.logging import LoggingMiddleware

    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())

    # Register handlers
    from app.handlers.start import router as start_router
    from app.handlers.video import router as video_router
    from app.handlers.profile import router as profile_router
    from app.handlers.music_recognize import router as music_router
    from app.handlers.admin import router as admin_router
    from app.handlers.callback_cancel import router as cancel_router

    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(profile_router)
    dp.include_router(music_router)
    dp.include_router(video_router)
    dp.include_router(cancel_router)

    logger.info("Starting Video Downloader Pro bot...")

    if config.download.ffmpeg_available:
        logger.info("FFmpeg detected - full quality support enabled")
    else:
        logger.warning("FFmpeg not found - limited quality support (pre-merged formats only)")

    logger.info("Starting polling mode...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")