"""Inline Keyboards - Premium styled buttons"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import CHANNEL_TYPES


def main_menu_kb() -> InlineKeyboardMarkup:
    """Main menu inline keyboard — faqat Profil va Yordam"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Profil", callback_data="profile")
    builder.button(text="ℹ️ Yordam", callback_data="help")
    builder.adjust(2)
    return builder.as_markup()


def subscription_check_kb(channels: list) -> InlineKeyboardMarkup:
    """Subscription check keyboard"""
    builder = InlineKeyboardBuilder()

    # Add channel links (only for Telegram channels)
    for ch in channels:
        if ch.channel_type == "telegram" and ch.channel_link:
            if ch.channel_link.startswith("@"):
                link = f"https://t.me/{ch.channel_link[1:]}"
            else:
                link = ch.channel_link
            builder.button(
                text=f"📢 {ch.channel_name or 'Kanal'}",
                url=link,
            )

    builder.button(text="✅ Obunani tekshirish", callback_data="check_subscription")
    builder.adjust(1)
    return builder.as_markup()


def quality_select_kb(video_id: str, qualities: list = None) -> InlineKeyboardMarkup:
    """Video quality selection keyboard"""
    builder = InlineKeyboardBuilder()

    if qualities is None:
        qualities = ["1080p", "720p", "480p", "360p"]

    quality_emojis = {
        "1080p": "🎥",
        "720p": "🎥",
        "480p": "📷",
        "360p": "📷",
    }

    for q in qualities:
        emoji = quality_emojis.get(q, "🎥")
        builder.button(
            text=f"{emoji} {q.upper()}",
            callback_data=f"quality_{video_id}_{q}",
        )

    builder.button(text="🎵 Audio MP3", callback_data=f"quality_{video_id}_mp3")
    builder.button(text="❌ Bekor qilish", callback_data="cancel_download")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def profile_kb() -> InlineKeyboardMarkup:
    """Profile keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Taklif linki", callback_data="referral_link")
    builder.button(text="🔙 Orqaga", callback_data="back_main")
    builder.adjust(2)
    return builder.as_markup()


def back_to_main_kb() -> InlineKeyboardMarkup:
    """Back to main menu keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Bosh menyu", callback_data="back_main")
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    """Cancel action keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="cancel")
    return builder.as_markup()


# ============ Admin Keyboards ============

def admin_menu_kb() -> InlineKeyboardMarkup:
    """Admin panel main menu keyboard — premium va promo olib tashlandi"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Statistika", callback_data="admin_stats")
    builder.button(text="👥 Foydalanuvchilar", callback_data="admin_users")
    builder.button(text="📢 Reklama yuborish", callback_data="admin_mailing")
    builder.button(text="📣 Forward xabar", callback_data="admin_forward")
    builder.button(text="📤 Post yuborish", callback_data="admin_post")
    builder.button(text="🚫 Ban", callback_data="admin_ban")
    builder.button(text="✅ Unban", callback_data="admin_unban")
    builder.button(text="📺 Kanal qo'shish", callback_data="admin_channel_add")
    builder.button(text="🗑 Kanal o'chirish", callback_data="admin_channel_remove")
    builder.button(text="⚙ Sozlamalar", callback_data="admin_settings")
    builder.button(text="🔙 Bosh menyu", callback_data="back_main")
    builder.adjust(2, 2, 2, 2, 2, 1)
    return builder.as_markup()


def channel_type_select_kb() -> InlineKeyboardMarkup:
    """Channel type selection keyboard"""
    builder = InlineKeyboardBuilder()
    for type_key, type_info in CHANNEL_TYPES.items():
        builder.button(
            text=f"{type_info['emoji']} {type_info['name']}",
            callback_data=f"channel_type_{type_key}",
        )
    builder.button(text="❌ Bekor qilish", callback_data="admin_back")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def channel_list_kb(channels: list) -> InlineKeyboardMarkup:
    """Channel list for removal keyboard"""
    builder = InlineKeyboardBuilder()
    for ch in channels:
        emoji = CHANNEL_TYPES.get(ch.channel_type, CHANNEL_TYPES["other"])["emoji"]
        builder.button(
            text=f"{emoji} {ch.channel_name or ch.channel_link}",
            callback_data=f"remove_channel_{ch.id}",
        )
    builder.button(text="🔙 Admin panel", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()


def confirm_kb(callback_yes: str, callback_no: str = "admin_back") -> InlineKeyboardMarkup:
    """Confirmation keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha", callback_data=callback_yes)
    builder.button(text="❌ Yo'q", callback_data=callback_no)
    builder.adjust(2)
    return builder.as_markup()


def admin_back_kb() -> InlineKeyboardMarkup:
    """Back to admin panel keyboard"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Admin panel", callback_data="admin_back")
    return builder.as_markup()
