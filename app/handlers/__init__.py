from app.handlers.start import router as start_router
from app.handlers.video import router as video_router
from app.handlers.profile import router as profile_router
from app.handlers.admin import router as admin_router
from app.handlers.callback_cancel import router as cancel_router

__all__ = ["start_router", "video_router", "profile_router", "admin_router", "cancel_router"]