from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.repos import (
    create_subscription,
    delete_subscription,
    get_subscription,
    list_subscriptions,
    select_catalog,
    set_subscription_paused,
    upsert_user,
)
from app.sources.registry import SourceRegistry
from app.ui import main_menu_kb, sub_actions_kb, subs_list_kb
from app.scoring import deal_score


CATEGORY, REGION, QUERY, PRICE_MIN, PRICE_MAX = range(5)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> tuple[async_sessionmaker[AsyncSession], Settings, SourceRegistry]:
    session_factory: async_sessionmaker[AsyncSession] = context.application.bot_data["session_factory"]
    settings: Settings = context.application.bot_data["settings"]
    sources: SourceRegistry = context.application.bot_data["sources"]
    return session_factory, settings, sources


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory, settings, _ = _deps(context)
    if not update.effective_user or not update.effective_chat or not update.message:
        return
    async with session_factory() as session:
        await upsert_user(session, tg_user_id=update.effective_user.id, chat_id=update.effective_chat.id)
        await session.commit()
    await update.message.reply_text(
        "Привет! Управление каталогами и лентой теперь в Mini App.\n"
        "Открой приложение кнопкой ниже.",
        reply_markup=main_menu_kb(webapp_url=settings.webapp_url),
    )


async def _render_profile_text(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Не удалось получить данные профиля."
    username = f"@{user.username}" if user.username else "не указан"
    first_name = user.first_name or "—"
    return (
        "👤 Мой профиль\n\n"
        f"Имя: `{first_name}`\n"
        f"Username: `{username}`\n"
        f"Telegram ID: `{user.id}`"
    )


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, settings, _ = _deps(context)
    if not update.message:
        return
    text = await _render_profile_text(update)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(webapp_url=settings.webapp_url),
    )


async def cb_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, settings, _ = _deps(context)
    if not update.callback_query or not update.callback_query.message:
        return
    text = await _render_profile_text(update)
    await update.callback_query.message.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(webapp_url=settings.webapp_url),
    )
    await update.callback_query.answer()


async def show_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory, settings, _ = _deps(context)
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    async with session_factory() as session:
        u = await upsert_user(session, tg_user_id=user.id, chat_id=chat.id)
        subs = await list_subscriptions(session, u.id)
        await session.commit()

    if not subs:
        text = "У вас пока нет подписок. Нажмите «Добавить подписку»."
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(
                text,
                reply_markup=main_menu_kb(webapp_url=settings.webapp_url),
            )
        elif update.message:
            await update.message.reply_text(text, reply_markup=main_menu_kb(webapp_url=settings.webapp_url))
        return

    kb = subs_list_kb([s.id for s in subs])
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text("Ваши подписки:", reply_markup=kb)
    elif update.message:
        await update.message.reply_text("Ваши подписки:", reply_markup=kb)


async def nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.callback_query.message:
        return
    _, settings, _ = _deps(context)
    await update.callback_query.message.edit_text(
        "Главное меню:",
        reply_markup=main_menu_kb(webapp_url=settings.webapp_url),
    )
    await update.callback_query.answer()


async def cb_sub_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_subs(update, context)
    if update.callback_query:
        await update.callback_query.answer()


async def sub_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _, settings, _ = _deps(context)
    if not update.callback_query or not update.callback_query.message:
        return ConversationHandler.END
    context.user_data.clear()
    await update.callback_query.message.edit_text(
        "Введите категорию (slug). Например: `telefony`, `noutbuki`, `odezhda`.\n\n"
        f"По умолчанию: `{settings.default_category}`.\n"
        "Можно отправить `-` чтобы взять значение по умолчанию.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await update.callback_query.answer()
    return CATEGORY


async def sub_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _, settings, _ = _deps(context)
    if not update.message:
        return ConversationHandler.END
    val = (update.message.text or "").strip()
    context.user_data["category"] = settings.default_category if val in {"-", ""} else val
    await update.message.reply_text(
        "Введите регион (slug). Например: `moskva`, `sankt-peterburg`.\n\n"
        f"По умолчанию: `{settings.default_region}`.\n"
        "Можно отправить `-` чтобы взять значение по умолчанию.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return REGION


async def sub_add_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _, settings, _ = _deps(context)
    if not update.message:
        return ConversationHandler.END
    val = (update.message.text or "").strip()
    context.user_data["region"] = settings.default_region if val in {"-", ""} else val
    await update.message.reply_text("Введите поисковый запрос (ключевые слова) или `-` чтобы пропустить.")
    return QUERY


async def sub_add_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    val = (update.message.text or "").strip()
    context.user_data["query"] = "" if val in {"-", ""} else val
    await update.message.reply_text("Минимальная цена (число) или `-` чтобы пропустить.")
    return PRICE_MIN


async def sub_add_pmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    val = (update.message.text or "").strip()
    if val in {"-", ""}:
        context.user_data["price_min"] = None
    else:
        try:
            context.user_data["price_min"] = int(val)
        except ValueError:
            await update.message.reply_text("Не понял. Пришлите число или `-`.")
            return PRICE_MIN
    await update.message.reply_text("Максимальная цена (число) или `-` чтобы пропустить.")
    return PRICE_MAX


async def sub_add_pmax(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    session_factory, settings, _ = _deps(context)
    if not update.message or not update.effective_user or not update.effective_chat:
        return ConversationHandler.END
    val = (update.message.text or "").strip()
    if val in {"-", ""}:
        price_max = None
    else:
        try:
            price_max = int(val)
        except ValueError:
            await update.message.reply_text("Не понял. Пришлите число или `-`.")
            return PRICE_MAX

    async with session_factory() as session:
        u = await upsert_user(
            session,
            tg_user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
        )
        sub = await create_subscription(
            session,
            user_id=u.id,
            source="avito_public_web",
            category=str(context.user_data.get("category") or ""),
            region=str(context.user_data.get("region") or ""),
            query=str(context.user_data.get("query") or ""),
            price_min=context.user_data.get("price_min"),
            price_max=price_max,
        )
        await select_catalog(session, user_id=u.id, catalog_id=sub.id)
        await session.commit()

    context.user_data.clear()
    await update.message.reply_text(
        f"Готово! Подписка #{sub.id} создана.\n"
        f"Категория: `{sub.category}`\nРегион: `{sub.region}`\n"
        f"Запрос: `{sub.query or '-'}`\n"
        f"Цена: `{sub.price_min or '-'} .. {sub.price_max or '-'}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(webapp_url=settings.webapp_url),
    )
    return ConversationHandler.END


async def sub_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory, _, _ = _deps(context)
    if not update.callback_query or not update.callback_query.message or not update.effective_user or not update.effective_chat:
        return
    _, _, sub_id_s = update.callback_query.data.split(":", 2)
    sub_id = int(sub_id_s)
    async with session_factory() as session:
        u = await upsert_user(session, tg_user_id=update.effective_user.id, chat_id=update.effective_chat.id)
        sub = await get_subscription(session, user_id=u.id, subscription_id=sub_id)
        await session.commit()
    if not sub:
        await update.callback_query.answer("Подписка не найдена", show_alert=True)
        return
    text = (
        f"Подписка #{sub.id}\n"
        f"Источник: `{sub.source}`\n"
        f"Категория: `{sub.category}`\n"
        f"Регион: `{sub.region}`\n"
        f"Запрос: `{sub.query or '-'}`\n"
        f"Цена: `{sub.price_min or '-'} .. {sub.price_max or '-'}`\n"
        f"Статус: `{'пауза' if sub.is_paused else 'активна'}`"
    )
    await update.callback_query.message.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=sub_actions_kb(sub.id, sub.is_paused),
    )
    await update.callback_query.answer()


async def sub_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory, _, _ = _deps(context)
    if not update.callback_query or not update.callback_query.message or not update.effective_user or not update.effective_chat:
        return
    _, _, rest = update.callback_query.data.split(":", 2)
    sub_id_s, flag_s = rest.split(":")
    sub_id = int(sub_id_s)
    is_paused = int(flag_s) == 1

    async with session_factory() as session:
        u = await upsert_user(session, tg_user_id=update.effective_user.id, chat_id=update.effective_chat.id)
        sub = await get_subscription(session, user_id=u.id, subscription_id=sub_id)
        if not sub:
            await session.commit()
            await update.callback_query.answer("Подписка не найдена", show_alert=True)
            return
        await set_subscription_paused(session, subscription_id=sub.id, is_paused=is_paused)
        await session.commit()

    await update.callback_query.answer("Ок")
    await sub_open(update, context)


async def sub_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory, _, _ = _deps(context)
    if not update.callback_query or not update.callback_query.message or not update.effective_user or not update.effective_chat:
        return
    _, _, sub_id_s = update.callback_query.data.split(":", 2)
    sub_id = int(sub_id_s)
    async with session_factory() as session:
        u = await upsert_user(session, tg_user_id=update.effective_user.id, chat_id=update.effective_chat.id)
        sub = await get_subscription(session, user_id=u.id, subscription_id=sub_id)
        if not sub:
            await session.commit()
            await update.callback_query.answer("Подписка не найдена", show_alert=True)
            return
        await delete_subscription(session, subscription_id=sub.id)
        await session.commit()
    await update.callback_query.answer("Удалено")
    await show_subs(update, context)


async def sub_peek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory, _, sources = _deps(context)
    if not update.callback_query or not update.callback_query.message or not update.effective_user or not update.effective_chat:
        return
    _, _, sub_id_s = update.callback_query.data.split(":", 2)
    sub_id = int(sub_id_s)
    async with session_factory() as session:
        u = await upsert_user(session, tg_user_id=update.effective_user.id, chat_id=update.effective_chat.id)
        sub = await get_subscription(session, user_id=u.id, subscription_id=sub_id)
        await session.commit()
    if not sub:
        await update.callback_query.answer("Подписка не найдена", show_alert=True)
        return
    try:
        listings = await sources.fetch_latest(sub, limit=10)
    except Exception:
        await update.callback_query.answer("Источник временно недоступен", show_alert=True)
        return

    if not listings:
        await update.callback_query.message.reply_text("Ничего не нашёл (или источник изменил разметку).")
        await update.callback_query.answer()
        return

    ranked = sorted(listings, key=lambda it: deal_score(sub, it), reverse=True)
    lines = []
    for it in ranked[:10]:
        price = f"{it.price} ₽" if it.price is not None else "—"
        lines.append(f"• {it.title}\n  {price}\n  {it.url}")
    await update.callback_query.message.reply_text("\n\n".join(lines))
    await update.callback_query.answer()


def register_handlers(app: Application) -> None:
    add_sub_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(sub_add_entry, pattern=r"^sub:add$")],
        states={
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_add_category)],
            REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_add_region)],
            QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_add_query)],
            PRICE_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_add_pmin)],
            PRICE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_add_pmax)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        name="add_sub",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("profile", cmd_profile))

    app.add_handler(CallbackQueryHandler(nav_home, pattern=r"^nav:home$"))
    app.add_handler(CallbackQueryHandler(cb_profile, pattern=r"^profile:show$"))

