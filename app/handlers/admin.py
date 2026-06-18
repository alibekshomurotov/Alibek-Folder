
import logging
import os

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.config import config, CHANNEL_TYPES
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.admin_service import AdminService
from app.services.subscription_service import SubscriptionService
from app.keyboards.inline import (
    admin_menu_kb, channel_type_select_kb, channel_list_kb,
    confirm_kb, admin_back_kb, back_to_main_kb,
)
from app.utils.formatter import (
    format_admin_stats, bold, code, separator, success_message,
    error_message, warning_message,
)
from app.states.admin_states import AdminStates

logger = logging.getLogger(__name__)

router = Router()


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return config.bot.is_admin(user_id)


# ============ Admin Menu ============

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    """Open admin panel"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Siz admin emassiz.")
        return

    await state.clear()
    text = (
        f"🔧 {bold('Admin Panel')}\n\n"
        f"Bo'limni tanlang:"
    )
    await message.answer(text, reply_markup=admin_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery, state: FSMContext):
    """Back to admin panel"""
    if not is_admin(callback.from_user.id):
        return

    await state.clear()
    text = (
        f"🔧 {bold('Admin Panel')}\n\n"
        f"Bo'limni tanlang:"
    )
    await callback.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")


# ============ Statistics ============

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    """Show admin statistics"""
    if not is_admin(callback.from_user.id):
        return

    stats = await AdminService.get_stats()
    text = format_admin_stats(
        total_users=stats.get("total_users", 0),
        today_users=stats.get("today_users", 0),
        total_downloads=stats.get("total_downloads", 0),
        today_downloads=stats.get("today_downloads", 0),
        premium_count=stats.get("premium_count", 0),
        total_channels=stats.get("total_channels", 0),
        platform_stats=stats.get("platform_stats", {}),
    )
    kb = admin_back_kb()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ============ Users ============

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    """Show users list"""
    if not is_admin(callback.from_user.id):
        return

    users = await AdminService.get_top_users(10)

    text = f"👥 {bold('Eng faol foydalanuvchilar')}\n\n{separator()}\n\n"
    for i, user in enumerate(users, 1):
        premium_badge = " ⭐" if user.is_premium_active else ""
        banned_badge = " 🚫" if user.is_banned else ""
        text += (
            f"{i}. {user.first_name or 'N/A'} (@{user.username or 'N/A'}){premium_badge}{banned_badge}\n"
            f"   🆔 {user.id} | 📥 {user.downloads_count} | 🔄 {user.referrals_count}\n\n"
        )

    kb = admin_back_kb()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ============ Mailing ============

@router.callback_query(F.data == "admin_mailing")
async def admin_mailing(callback: CallbackQuery, state: FSMContext):
    """Start mailing process"""
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.mailing_message)
    text = (
        f"📢 {bold('Reklama yuborish')}\n\n"
        f"Barcha foydalanuvchilarga yuboriladigan xabarni yuboring:\n\n"
        f"⚠️ Xabar barcha foydalanuvchilarga yuboriladi!"
    )
    from app.keyboards.inline import cancel_kb
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.mailing_message)
async def process_mailing_message(message: Message, state: FSMContext):
    """Process mailing message"""
    await state.update_data(mailing_message=message.message_id, mailing_chat_id=message.chat.id)
    await state.set_state(AdminStates.mailing_confirm)

    text = (
        f"📢 {bold('Tasdiqlash')}\n\n"
        f"Quyidagi xabar barcha foydalanuvchilarga yuboriladi.\n\n"
        f"⚠️ Tasdiqlaysizmi?"
    )
    await message.answer(text, reply_markup=confirm_kb("mailing_confirm_yes", "admin_back"), parse_mode="HTML")


@router.callback_query(F.data == "mailing_confirm_yes", AdminStates.mailing_confirm)
async def confirm_mailing(callback: CallbackQuery, state: FSMContext):
    """Confirm and send mailing"""
    if not is_admin(callback.from_user.id):
        return

    data = await state.get_data()
    await state.clear()

    users = await AdminService.get_users(limit=10000)
    success = 0
    failed = 0

    status_msg = await callback.message.answer("📢 Yuborilmoqda... 0%")

    for i, user in enumerate(users):
        try:
            await callback.bot.copy_message(
                chat_id=user.id,
                from_chat_id=data["mailing_chat_id"],
                message_id=data["mailing_message"],
            )
            success += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Failed to send mailing to {user.id}: {e}")

        if (i + 1) % 10 == 0 and users:
            progress = int((i + 1) / len(users) * 100)
            try:
                await status_msg.edit_text(f"📢 Yuborilmoqda... {progress}%")
            except Exception:
                pass

    text = (
        f"📢 {bold('Yuborish tugadi')}\n\n"
        f"✅ Muvaffaqiyatli: {success}\n"
        f"❌ Xatolik: {failed}\n"
        f"📊 Jami: {len(users)}"
    )
    await status_msg.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Forward ============

@router.callback_query(F.data == "admin_forward")
async def admin_forward(callback: CallbackQuery, state: FSMContext):
    """Start forward process"""
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.forward_message)
    text = (
        f"📣 {bold('Forward xabar')}\n\n"
        f"Forward qilinadigan xabarni yuboring:"
    )
    from app.keyboards.inline import cancel_kb
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.forward_message)
async def process_forward_message(message: Message, state: FSMContext):
    """Process forward message"""
    await state.clear()
    users = await AdminService.get_users(limit=10000)
    success = 0
    failed = 0

    status_msg = await message.answer("📣 Forward qilinmoqda...")

    for i, user in enumerate(users):
        try:
            await message.forward(chat_id=user.id)
            success += 1
        except Exception:
            failed += 1

        if (i + 1) % 10 == 0 and users:
            progress = int((i + 1) / len(users) * 100)
            try:
                await status_msg.edit_text(f"📣 Forward qilinmoqda... {progress}%")
            except Exception:
                pass

    text = (
        f"📣 {bold('Forward tugadi')}\n\n"
        f"✅ Muvaffaqiyatli: {success}\n"
        f"❌ Xatolik: {failed}"
    )
    await status_msg.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Post ============

@router.callback_query(F.data == "admin_post")
async def admin_post(callback: CallbackQuery, state: FSMContext):
    """Start post creation"""
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.post_message)
    text = (
        f"📤 {bold('Post yuborish')}\n\n"
        f"Yuboriladigan post xabarini yuboring:"
    )
    from app.keyboards.inline import cancel_kb
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.post_message)
async def process_post_message(message: Message, state: FSMContext):
    """Process post message"""
    await state.update_data(post_message=message.message_id, post_chat_id=message.chat.id)
    await state.set_state(AdminStates.post_confirm)

    text = (
        f"📤 {bold('Tasdiqlash')}\n\n"
        f"Post barcha foydalanuvchilarga yuboriladi.\n\n"
        f"Tasdiqlaysizmi?"
    )
    await message.answer(text, reply_markup=confirm_kb("post_confirm_yes", "admin_back"), parse_mode="HTML")


@router.callback_query(F.data == "post_confirm_yes", AdminStates.post_confirm)
async def confirm_post(callback: CallbackQuery, state: FSMContext):
    """Confirm and send post"""
    if not is_admin(callback.from_user.id):
        return

    data = await state.get_data()
    await state.clear()

    users = await AdminService.get_users(limit=10000)
    success = 0
    failed = 0

    status_msg = await callback.message.answer("📤 Yuborilmoqda... 0%")

    for i, user in enumerate(users):
        try:
            await callback.bot.copy_message(
                chat_id=user.id,
                from_chat_id=data["post_chat_id"],
                message_id=data["post_message"],
            )
            success += 1
        except Exception:
            failed += 1

        if (i + 1) % 10 == 0 and users:
            progress = int((i + 1) / len(users) * 100)
            try:
                await status_msg.edit_text(f"📤 Yuborilmoqda... {progress}%")
            except Exception:
                pass

    text = (
        f"📤 {bold('Post yuborildi')}\n\n"
        f"✅ Muvaffaqiyatli: {success}\n"
        f"❌ Xatolik: {failed}"
    )
    await status_msg.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Ban ============

@router.callback_query(F.data == "admin_ban")
async def admin_ban(callback: CallbackQuery, state: FSMContext):
    """Start ban process"""
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.ban_user_id)
    text = (
        f"🚫 {bold('Ban')}\n\n"
        f"Foydalanuvchi ID sini yuboring:"
    )
    from app.keyboards.inline import cancel_kb
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.ban_user_id)
async def process_ban_user_id(message: Message, state: FSMContext):
    """Process ban user ID"""
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Raqam kiriting.")
        return

    if is_admin(user_id):
        await message.answer("❌ Adminni ban qilib bo'lmaydi.")
        return

    await state.update_data(ban_user_id=user_id)
    await state.set_state(AdminStates.ban_reason)

    text = f"🚫 Ban sababini yuboring (yoki '-' o'tkazib yuborish uchun):"
    await message.answer(text)


@router.message(AdminStates.ban_reason)
async def process_ban_reason(message: Message, state: FSMContext):
    """Process ban reason"""
    data = await state.get_data()
    user_id = data.get("ban_user_id")
    reason = message.text.strip() if message.text.strip() != "-" else None
    await state.clear()

    result = await AdminService.ban_user(user_id, reason)

    if result:
        text = success_message(f"Foydalanuvchi {code(str(user_id))} ban qilindi.")
    else:
        text = error_message(f"Foydalanuvchi {code(str(user_id))} topilmadi.")

    await message.answer(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Unban ============

@router.callback_query(F.data == "admin_unban")
async def admin_unban(callback: CallbackQuery, state: FSMContext):
    """Start unban process"""
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.unban_user_id)
    text = (
        f"✅ {bold('Unban')}\n\n"
        f"Foydalanuvchi ID sini yuboring:"
    )
    from app.keyboards.inline import cancel_kb
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.unban_user_id)
async def process_unban_user_id(message: Message, state: FSMContext):
    """Process unban user ID"""
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Raqam kiriting.")
        return

    await state.clear()
    result = await AdminService.unban_user(user_id)

    if result:
        text = success_message(f"Foydalanuvchi {code(str(user_id))} ban dan chiqarildi.")
    else:
        text = error_message(f"Foydalanuvchi {code(str(user_id))} topilmadi.")

    await message.answer(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Channel Add ============

@router.callback_query(F.data == "admin_channel_add")
async def admin_channel_add(callback: CallbackQuery, state: FSMContext):
    """Start channel adding"""
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.channel_type)
    text = (
        f"📺 {bold('Kanal qo\'shish')}\n\n"
        f"Kanal turini tanlang:"
    )
    await callback.message.edit_text(text, reply_markup=channel_type_select_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("channel_type_"), AdminStates.channel_type)
async def process_channel_type(callback: CallbackQuery, state: FSMContext):
    """Process channel type selection"""
    channel_type = callback.data.replace("channel_type_", "")
    await state.update_data(channel_type=channel_type)
    await state.set_state(AdminStates.channel_link)

    hint = CHANNEL_TYPES.get(channel_type, CHANNEL_TYPES["other"])["hint"]
    channel_name = CHANNEL_TYPES.get(channel_type, CHANNEL_TYPES["other"])["name"]
    channel_emoji = CHANNEL_TYPES.get(channel_type, CHANNEL_TYPES["other"])["emoji"]

    text = (
        f"📺 {bold(f'Kanal qo\'shish — {channel_emoji} {channel_name}')}\n\n"
        f"{hint}"
    )
    from app.keyboards.inline import cancel_kb
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(AdminStates.channel_link)
async def process_channel_link(message: Message, state: FSMContext):
    """Process channel link"""
    link = message.text.strip()
    data = await state.get_data()
    channel_type = data.get("channel_type", "telegram")

    channel_id = None
    if channel_type == "telegram":
        from app.utils.helpers import parse_telegram_channel_id
        channel_id = parse_telegram_channel_id(link)
        if not channel_id:
            await message.answer(
                "❌ Noto'g'ri Telegram kanal linki. @username yoki https://t.me/username formatida yuboring."
            )
            return

        try:
            chat = await message.bot.get_chat(channel_id)
            channel_name = chat.title
        except Exception as e:
            logger.warning(f"Could not get channel info: {e}")
            channel_name = link
    else:
        channel_name = link

    await SubscriptionService.add_channel(
        channel_link=link,
        channel_name=channel_name,
        channel_id=channel_id,
        channel_type=channel_type,
    )

    await state.clear()

    text = success_message(
        f"Kanal muvaffaqiyatli qo'shildi!\n\n"
        f"📺 {channel_name}\n"
        f"🔗 {link}\n"
        f"📋 Turi: {CHANNEL_TYPES.get(channel_type, {}).get('name', channel_type)}"
    )
    await message.answer(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Channel Remove ============

@router.callback_query(F.data == "admin_channel_remove")
async def admin_channel_remove(callback: CallbackQuery, state: FSMContext):
    """Show channels for removal"""
    if not is_admin(callback.from_user.id):
        return

    channels = await SubscriptionService.get_all_channels()

    if not channels:
        text = warning_message("Hech qanday kanal topilmadi.")
        await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")
        return

    text = f"🗑 {bold('Kanal o\'chirish')}\n\nO'chirish uchun kanalni tanlang:"
    kb = channel_list_kb(channels)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("remove_channel_"))
async def process_remove_channel(callback: CallbackQuery, state: FSMContext):
    """Remove a channel"""
    if not is_admin(callback.from_user.id):
        return

    channel_db_id = int(callback.data.replace("remove_channel_", ""))
    result = await SubscriptionService.remove_channel(channel_db_id)

    if result:
        text = success_message("Kanal muvaffaqiyatli o'chirildi.")
    else:
        text = error_message("Kanal topilmadi.")

    await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")


# ============ Settings ============

@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery, state: FSMContext):
    """Show settings"""
    if not is_admin(callback.from_user.id):
        return

    from app.utils.formatter import info_line
    text = (
        f"⚙ {bold('Sozlamalar')}\n\n"
        f"{separator()}\n\n"
        f"{info_line('FFmpeg', '✅ Mavjud' if config.download.ffmpeg_available else '❌ Yo\'q')}\n"
        f"{info_line('Max fayl hajmi', f'{config.download.max_file_size_mb} MB')}\n"
        f"{info_line('Default sifat', f'{config.download.default_quality}p')}\n"
        f"{info_line('Rate limit', f'{config.rate_limit.downloads}/{config.rate_limit.period}s')}\n"
        f"{info_line('Cookie fayl', '✅ Mavjud' if os.path.exists(config.download.cookies_file) else '❌ Yo\'q')}\n"
    )
    kb = admin_back_kb()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")