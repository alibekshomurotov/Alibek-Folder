import asyncio
import logging
import os
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.handlers import start, video, profile, admin, cancel
from app.middleware.throttle import ThrottleMiddleware
from app.middleware.auth import AuthMiddleware
from app.middleware.logging import LoggingMiddleware
from app.utils.db import init_db

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global bot reference for shutdown
_bot = None
_dp = None


async def shutdown(sig=None):
    """To'g'ri shutdown — pollingni to'xtatib, bot sessionni yopadi."""
    global _bot, _dp
    logger.info(f"🛑 Shutdown signal olindi: {sig}")
    if _dp:
        try:
            await _dp.stop_polling()
            logger.info("✅ Polling to'xtatildi")
        except Exception as e:
            logger.error(f"Polling to'xtatishda xato: {e}")
    if _bot:
        try:
            await _bot.session.close()
            logger.info("✅ Bot session yopildi")
        except Exception as e:
            logger.error(f"Session yopishda xato: {e}")


async def main():
    global _bot, _dp

    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN topilmadi! .env faylini tekshiring.")
        sys.exit(1)

    _bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    _dp = Dispatcher()

    # Middleware lar
    _dp.message.middleware(ThrottleMiddleware())
    _dp.message.middleware(AuthMiddleware())
    _dp.message.middleware(LoggingMiddleware())
    _dp.callback_query.middleware(AuthMiddleware())

    # Router larni ro'yxatdan o'tkazish (TARTIB MUHIM: admin oxirida)
    _dp.include_router(start.router)
    _dp.include_router(video.router)
    _dp.include_router(profile.router)
    _dp.include_router(cancel.router)
    _dp.include_router(admin.router)

    # DB ni ishga tushirish
    await init_db()
    logger.info("✅ Database tayyor")

    # Signal handling
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
        except NotImplementedError:
            # Windows da signal handler ishlamaydi
            pass

    logger.info("🤖 Bot ishga tushdi...")

    # Polling — close_bot_session=True agar shutdown to'g'ri ishlasa
    # allow_restart=False — qayta boshlashni oldini oladi
    try:
        await _dp.start_polling(
            _bot,
            close_bot_session=False,  # Biz o'zimiz yopamiz shutdown() da
            allowed_updates=["message", "callback_query"],
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await shutdown("finally block")
        logger.info("👋 Bot to'xtatildi")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass