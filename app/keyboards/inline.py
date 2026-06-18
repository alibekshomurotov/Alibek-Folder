"""Inline Keyboards"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu_kb() -> InlineKeyboardMarkup:
    """Start xabari ostidagi inline tugma — faqat Profil."""
    kb = [[
        InlineKeyboardButton(text="👤 Profil", callback_data="profile")
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def mp3_download_kb(cache_key: str) -> InlineKeyboardMarkup:
    """Video ostidagi MP3 yuklash tugmasi (cache_key orqali)."""
    kb = [[
        InlineKeyboardButton(text="🎵 MP3 yuklash", callback_data=f"mp3_{cache_key}")
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def back_to_main_kb() -> InlineKeyboardMarkup:
    """Orqaga qaytish tugmasi."""
    kb = [[
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def cancel_kb() -> InlineKeyboardMarkup:
    """Bekor qilish tugmasi."""
    kb = [[
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel")
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def quality_select_kb(video_id: str, qualities: list = None) -> InlineKeyboardMarkup:
    """Video sifat tanlash klaviaturasi."""
    if qualities is None:
        qualities = ["1080p", "720p", "480p", "360p"]

    kb = []
    row = []
    for q in qualities:
        row.append(InlineKeyboardButton(
            text=f"🎥 {q.upper()}",
            callback_data=f"quality_{video_id}_{q}",
        ))
        if len(row) == 2:
            kb.append(row)
            row = []

    # MP3 tugma
    kb.append([InlineKeyboardButton(
        text="🎵 Audio MP3",
        callback_data=f"quality_{video_id}_mp3",
    )])
    # Bekor qilish
    kb.append([InlineKeyboardButton(
        text="❌ Bekor qilish",
        callback_data="cancel_download",
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


# ============ Admin Keyboards ============

def admin_menu_kb() -> InlineKeyboardMarkup:
    """Admin panel inline menyu — Promo va Premium O'CHIRILDI."""
    kb = [
        [
            InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats"),
            InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton(text="📢 Reklama yuborish", callback_data="admin_mailing"),
            InlineKeyboardButton(text="📣 Forward xabar", callback_data="admin_forward"),
        ],
        [
            InlineKeyboardButton(text="📤 Post yuborish", callback_data="admin_post"),
        ],
        [
            InlineKeyboardButton(text="🚫 Ban", callback_data="admin_ban"),
            InlineKeyboardButton(text="✅ Unban", callback_data="admin_unban"),
        ],
        [
            InlineKeyboardButton(text="📺 Kanal qo'shish", callback_data="admin_channel_add"),
            InlineKeyboardButton(text="🗑 Kanal o'chirish", callback_data="admin_channel_remove"),
        ],
        [
            InlineKeyboardButton(text="⚙ Sozlamalar", callback_data="admin_settings"),
        ],
        [
            InlineKeyboardButton(text="🔙 Bosh menyu", callback_data="back_main"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def channel_type_select_kb() -> InlineKeyboardMarkup:
    """Kanal turini tanlash klaviaturasi."""
    from app.config import CHANNEL_TYPES
    kb = []
    row = []
    for type_key, type_info in CHANNEL_TYPES.items():
        row.append(InlineKeyboardButton(
            text=f"{type_info['emoji']} {type_info['name']}",
            callback_data=f"channel_type_{type_key}",
        ))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def channel_list_kb(channels: list) -> InlineKeyboardMarkup:
    """Kanal ro'yxati o'chirish uchun."""
    kb = []
    for ch in channels:
        from app.config import CHANNEL_TYPES
        emoji = CHANNEL_TYPES.get(ch.channel_type, CHANNEL_TYPES["other"])["emoji"]
        kb.append([InlineKeyboardButton(
            text=f"{emoji} {ch.channel_name or ch.channel_link}",
            callback_data=f"remove_channel_{ch.id}",
        )])
    kb.append([InlineKeyboardButton(text="🔙 Admin panel", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def confirm_kb(callback_yes: str, callback_no: str = "admin_back") -> InlineKeyboardMarkup:
    """Tasdiqlash klaviaturasi."""
    kb = [[
        InlineKeyboardButton(text="✅ Ha", callback_data=callback_yes),
        InlineKeyboardButton(text="❌ Yo'q", callback_data=callback_no),
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_back_kb() -> InlineKeyboardMarkup:
    """Admin panega qaytish tugmasi."""
    kb = [[
        InlineKeyboardButton(text="🔙 Admin panel", callback_data="admin_back"),
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)