"""Video Downloader Pro - Main Entry Point"""

import asyncio
import logging
import os
import signal
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

_bot = None


async def shutdown(sig=None):
    """To'g'ri shutdown — pollingni to'xtatib, session yopadi."""
    global _bot
    logger.info(f"Shutdown signal: {sig}")
    try:
        await close_db()
    except Exception:
        pass
    if _bot:
        try:
            await _bot.session.close()
        except Exception:
            pass


async def _dummy_http_server():
    """Render.com Web Service uchun soxta HTTP server — port bog'lash uchun."""
    import http.server
    import socketserver

    port = int(os.getenv("PORT", "10000"))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is running")

        def log_message(self, format, *args):
            pass

    try:
        httpd = socketserver.TCPServer(("", port), Handler)
        logger.info(f"Dummy HTTP server port {port} da (Render health check)")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, httpd.serve_forever)
    except Exception as e:
        logger.warning(f"HTTP server xato: {e}")


async def main():
    """Main function to start the bot"""
    global _bot

    if not config.bot.token:
        logger.error("BOT_TOKEN is not set!")
        sys.exit(1)

    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized.")

    _bot = Bot(
        token=config.bot.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    # Register middleware
    from app.middleware.throttle import ThrottleMiddleware
    from app.middleware.auth import AuthMiddleware
    from app.middleware.logging import LoggingMiddleware

    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())

    # Register handlers — premium_router O'CHIRILDI
    from app.handlers.start import router as start_router
    from app.handlers.video import router as video_router
    from app.handlers.profile import router as profile_router
    from app.handlers.admin import router as admin_router
    from app.handlers.callback_cancel import router as cancel_router

    dp.include_router(start_router)
    dp.include_router(video_router)
    dp.include_router(profile_router)
    dp.include_router(admin_router)
    dp.include_router(cancel_router)

    logger.info("Starting Video Downloader Pro bot...")

    if config.download.ffmpeg_available:
        logger.info("FFmpeg detected - full quality support enabled")
    else:
        logger.warning("FFmpeg not found - limited quality support")

    # Signal handling
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
        except NotImplementedError:
            pass

    # Render.com Web Service uchun dummy HTTP server
    port_env = os.getenv("PORT")
    if port_env:
        asyncio.create_task(_dummy_http_server())

    # Start polling
    logger.info("Starting polling mode...")
    await _bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(
            _bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await shutdown("finally")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")