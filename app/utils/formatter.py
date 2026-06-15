"""Text Formatter - Premium message formatting utilities"""

from app.config import THEME


def bold(text: str) -> str:
    """Bold text"""
    return f"<b>{text}</b>"


def italic(text: str) -> str:
    """Italic text"""
    return f"<i>{text}</i>"


def code(text: str) -> str:
    """Code text"""
    return f"<code>{text}</code>"


def link(text: str, url: str) -> str:
    """Create a link"""
    return f'<a href="{url}">{text}</a>'


def separator() -> str:
    """Glassmorphism-style separator"""
    return "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"


def header(emoji: str, title: str) -> str:
    """Create a premium header"""
    return f"{emoji} {bold(title)}"


def info_line(label: str, value: str) -> str:
    """Create an info line with label and value"""
    return f"  {italic(label)}: {value}"


def success_message(text: str) -> str:
    """Format a success message"""
    return f"✅ {text}"


def error_message(text: str) -> str:
    """Format an error message"""
    return f"❌ {text}"


def warning_message(text: str) -> str:
    """Format a warning message"""
    return f"⚠️ {text}"


def premium_badge() -> str:
    """Premium star badge"""
    return "⭐"


def format_welcome() -> str:
    """Format the welcome/start message"""
    return (
        f"🎬 {bold('Video Downloader Pro')}\n\n"
        f"Ijtimoiy tarmoqlardan video yuklab olishning eng tez usuli.\n\n"
        f"📥 Link yuboring va videoni oling.\n\n"
        f"⚡ Tez yuklash\n"
        f"🎬 HD sifat\n"
        f"🔒 Xavfsiz"
    )


def format_subscription_required(channels: list) -> str:
    """Format subscription required message"""
    text = (
        f"⚠️ {bold('Botdan foydalanish uchun quyidagi kanallarga obuna bo\'ling:')}\n\n"
    )

    for i, ch in enumerate(channels, 1):
        emoji = "📢" if ch.channel_type == "telegram" else "🔗"
        text += f"{i}. {emoji} {ch.channel_name or ch.channel_link}\n"
        if ch.channel_type == "telegram" and ch.channel_link:
            text += f"   👉 {ch.channel_link}\n"

    text += f"\n✅ Obunani tekshirish tugmasini bosing."
    return text


def format_video_info(info: dict, platform: str) -> str:
    """Format video information message"""
    from app.utils.downloader import format_duration, format_file_size, format_view_count

    text = (
        f"🎬 {bold(info.get('title', 'Noma\'lum'))}\n\n"
        f"{separator()}\n\n"
    )

    # Platform
    platform_names = {
        "tiktok": "🎵 TikTok",
        "instagram": "📸 Instagram",
        "youtube": "▶️ YouTube",
        "facebook": "📘 Facebook",
        "twitter": "🐦 X (Twitter)",
        "pinterest": "📌 Pinterest",
        "snapchat": "👻 Snapchat",
        "threads": "🧵 Threads",
    }
    if platform in platform_names:
        text += f"📱 Platforma: {platform_names[platform]}\n"

    # Duration
    duration = info.get("duration")
    if duration:
        text += f"⏱ Davomiylik: {format_duration(int(duration))}\n"

    # Views
    views = info.get("view_count")
    if views:
        text += f"👀 Ko'rishlar: {format_view_count(views)}\n"

    # Likes
    likes = info.get("like_count")
    if likes:
        text += f"❤️ Layklar: {format_view_count(likes)}\n"

    # Upload date
    upload_date = info.get("upload_date")
    if upload_date:
        from datetime import datetime
        try:
            dt = datetime.strptime(upload_date, "%Y%m%d")
            text += f"📅 Sana: {dt.strftime('%d.%m.%Y')}\n"
        except ValueError:
            pass

    text += f"\n{separator()}\n"
    text += f"\n🎥 Sifatni tanlang:"

    return text


def format_video_caption(info: dict, quality: str = "HD") -> str:
    """Format video caption for sent video"""
    title = info.get("title", "Video")
    if len(title) > 50:
        title = title[:47] + "..."

    return (
        f"⚡ Yuklandi\n"
        f"🎬 {quality} Sifat\n"
        f"🤖 Downloader Pro"
    )


def format_profile(user) -> str:
    """Format user profile message"""
    premium_status = "⭐ Premium" if user.is_premium_active else "🆓 Bepul"
    if user.is_premium_active and user.premium_until:
        premium_status += f" ({user.premium_until.strftime('%d.%m.%Y')} gacha)"

    text = (
        f"👤 {bold('Profil')}\n\n"
        f"{separator()}\n\n"
        f"🆔 ID: {code(str(user.id))}\n"
        f"👤 Ism: {user.first_name or 'N/A'}\n"
        f"📱 Username: @{user.username or 'N/A'}\n"
        f"📅 Ro'yxatdan o'tgan: {user.registered_at.strftime('%d.%m.%Y')}\n"
        f"📥 Yuklangan: {bold(str(user.downloads_count))} video\n"
        f"🔄 Takliflar: {bold(str(user.referrals_count))} kishi\n"
        f"💎 Holat: {premium_status}\n\n"
        f"{separator()}\n\n"
        f"🔗 Taklif linki:\n{code(user.referral_link)}"
    )
    return text


def format_admin_stats(total_users: int, today_users: int, total_downloads: int,
                       today_downloads: int, premium_count: int,
                       total_channels: int, platform_stats: dict) -> str:
    """Format admin statistics message"""
    text = (
        f"📊 {bold('Statistika')}\n\n"
        f"{separator()}\n\n"
        f"👥 Jami foydalanuvchilar: {bold(str(total_users))}\n"
        f"🆕 Bugungi foydalanuvchilar: {bold(str(today_users))}\n\n"
        f"📥 Jami yuklashlar: {bold(str(total_downloads))}\n"
        f"📥 Bugungi yuklashlar: {bold(str(today_downloads))}\n\n"
        f"⭐ Premiumlar: {bold(str(premium_count))}\n"
        f"📺 Kanallar: {bold(str(total_channels))}\n\n"
    )

    if platform_stats:
        text += f"{separator()}\n\n"
        text += f"📱 {bold('Platformalar bo\'yicha:')}\n"
        for platform, count in platform_stats.items():
            text += f"  • {platform}: {count}\n"

    return text


def format_premium_info() -> str:
    """Format premium information message"""
    return (
        f"⭐ {bold('Premium')}\n\n"
        f"{separator()}\n\n"
        f"Premium foydalanuvchilar imtiyozlari:\n\n"
        f"⚡ Navbatsiz yuklash\n"
        f"🚀 Tezroq server\n"
        f"🎥 Maksimal sifat (1080p+)\n"
        f"🔓 Reklamasiz foydalanish\n"
        f"🎵 MP3 yuklash\n\n"
        f"{separator()}\n\n"
        f"🎁 {bold('Bepul Premium olish:')}\n\n"
        f"👥 5 ta do'st taklif qiling = 3 kun premium\n"
        f"👥 20 ta do'st taklif qiling = 30 kun premium\n\n"
        f"🔑 Promo kodga egasiz? Quyidagi tugmani bosing:"
    )


def format_help() -> str:
    """Format help message"""
    return (
        f"ℹ️ {bold('Yordam')}\n\n"
        f"{separator()}\n\n"
        f"📥 {bold('Qanday ishlaydi?')}\n"
        f"Ijtimoiy tarmoqdan video linkini yuboring va bot videoni yuklab beradi.\n\n"
        f"📱 {bold('Qo\'llab-quvvatlanadigan platformalar:')}\n"
        f"  🎵 TikTok\n"
        f"  📸 Instagram\n"
        f"  ▶️ YouTube\n"
        f"  📘 Facebook\n"
        f"  🐦 X (Twitter)\n"
        f"  📌 Pinterest\n"
        f"  👻 Snapchat\n"
        f"  🧵 Threads\n\n"
        f"{separator()}\n\n"
        f"🎥 {bold('Sifat tanlash:')}\n"
        f"Video topilgandan so'ng siz sifatni tanlashingiz mumkin.\n\n"
        f"🎵 {bold('MP3 yuklash:')}\n"
        f"Video tagidagi MP3 tugmasini bosib audio yuklab olishingiz mumkin."
    )


def format_loading_step(step: int) -> str:
    """Format loading animation step"""
    steps = [
        "🕐 Link tekshirilmoqda...",
        "🕑 Video topilmoqda...",
        "🕒 Server bilan bog'lanmoqda...",
        "🕓 Yuklab olinmoqda...",
        "🕔 Video tayyorlanmoqda...",
        "🕕 HD sifat optimallashtirilmoqda...",
        "🕖 Deyarli tayyor...",
        "🕗 Video yuborilmoqda...",
    ]
    idx = step % len(steps)
    return steps[idx]


def format_error(error_type: str) -> str:
    """Format error message by type"""
    errors = {
        "invalid_link": (
            f"❌ {bold('Link aniqlanmadi.')}\n\n"
            f"Qo'llab-quvvatlanadigan platformalardan foydalaning:\n"
            f"TikTok, Instagram, YouTube, Facebook, X, Pinterest, Snapchat, Threads"
        ),
        "server_error": (
            f"⚠️ {bold('Server vaqtincha band.')}\n\n"
            f"Iltimos, qayta urinib ko'ring."
        ),
        "download_error": (
            f"❌ {bold('Video yuklab bo\'lmadi.')}\n\n"
            f"Link to'g'ri ekanligini tekshiring va qayta urinib ko'ring."
        ),
        "file_too_large": (
            f"⚠️ {bold('Video hajmi juda katta.')}\n\n"
            f"Pastroq sifatni tanlang yoki Premiumga o'ting."
        ),
        "banned": (
            f"🚫 {bold('Siz ban qilingansiz.')}\n\n"
            f"Admin bilan bog'laning."
        ),
        "rate_limit": (
            f"⏳ {bold('Juda ko\'p so\'rov.')}\n\n"
            f"Biroz kuting va qayta urinib ko'ring."
        ),
        "premium_required": (
            f"⭐ {bold('Bu funksiya faqat Premium uchun.')}\n\n"
            f"Premium olish uchun profil bo'limiga o'ting."
        ),
    }
    return errors.get(error_type, f"❌ Noma'lum xatolik yuz berdi.")
