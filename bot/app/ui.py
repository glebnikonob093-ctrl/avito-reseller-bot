from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def main_menu_kb(*, webapp_url: str = "", is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if webapp_url:
        rows.append([InlineKeyboardButton(text="🧾 Открыть Mini App", web_app=WebAppInfo(url=webapp_url))])
    rows.append([InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile:show")])
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:home")],
        ]
    )


def profile_menu_kb(*, webapp_url: str = "", is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if webapp_url:
        rows.append([InlineKeyboardButton(text="🧾 Открыть Mini App", web_app=WebAppInfo(url=webapp_url))])
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin:home")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:home")])
    return InlineKeyboardMarkup(rows)


def subs_list_kb(sub_ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for sub_id in sub_ids:
        rows.append(
            [
                InlineKeyboardButton(text=f"Подписка #{sub_id}", callback_data=f"sub:open:{sub_id}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:home")])
    return InlineKeyboardMarkup(rows)


def sub_actions_kb(sub_id: int, is_paused: bool) -> InlineKeyboardMarkup:
    pause_btn = InlineKeyboardButton(
        text=("▶️ Включить" if is_paused else "⏸ Пауза"),
        callback_data=f"sub:pause:{sub_id}:{0 if is_paused else 1}",
    )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="🆕 Показать новые (топ-10)", callback_data=f"sub:peek:{sub_id}")],
            [pause_btn],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"sub:del:{sub_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub:list")],
        ]
    )

