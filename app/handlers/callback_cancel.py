"""Cancel handler for FSM states"""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from app.keyboards.inline import admin_menu_kb, back_to_main_kb
from app.utils.formatter import bold

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    """Cancel current action and clear state"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()

    await callback.message.edit_text(
        f"❌ Amal bekor qilindi.",
        reply_markup=back_to_main_kb(),
    )
