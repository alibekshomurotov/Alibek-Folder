"""FSM States for Admin Panel and other flows"""

from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    """Admin panel FSM states"""
    # Mailing
    mailing_message = State()
    mailing_confirm = State()

    # Forward
    forward_message = State()

    # Post
    post_message = State()
    post_confirm = State()

    # Add channel
    channel_type = State()
    channel_link = State()
    channel_name = State()

    # Remove channel
    channel_remove = State()

    # Ban
    ban_user_id = State()
    ban_reason = State()

    # Unban
    unban_user_id = State()

    # Settings
    settings_menu = State()


class DownloadStates(StatesGroup):
    """Download quality selection states"""
    selecting_quality = State()
