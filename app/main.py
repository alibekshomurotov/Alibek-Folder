"""Video Downloader Pro - Main Entry Point"""

import asyncio
import logging
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


async def main():
    """Main function to start the bot"""
    # Validate config
    if not config.bot.token:
        logger.error("BOT_TOKEN is not set! Please set it in .env file or environment variables.")
        sys.exit(1)

    # Initialize database
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized.")

    # Create bot and dispatcher
    bot = Bot(
        token=config.bot.token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher()

    # Register middleware
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
    from app.handlers.premium import router as premium_router
    from app.handlers.admin import router as admin_router
    from app.handlers.callback_cancel import router as cancel_router

    dp.include_router(start_router)
    dp.include_router(video_router)
    dp.include_router(profile_router)
    dp.include_router(premium_router)
    dp.include_router(admin_router)
    dp.include_router(cancel_router)

    logger.info("Starting Video Downloader Pro bot...")

    # Check FFmpeg
    if config.download.ffmpeg_available:
        logger.info("FFmpeg detected - full quality support enabled")
    else:
        logger.warning("FFmpeg not found - limited quality support (pre-merged formats only)")

    # Start polling or webhook
    if config.webhook.enabled and config.webhook.url:
        logger.info(f"Starting webhook mode at {config.webhook.url}")
        await bot.set_webhook(
            url=config.webhook.url,
            drop_pending_updates=True,
        )
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        from aiohttp import web

        app = web.Application()
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, config.webhook.host, config.webhook.port)
        await site.start()
        await asyncio.Event().wait()
    else:
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
