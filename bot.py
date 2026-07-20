import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    KeyboardButton, InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8929985932:AAFRHiKDVvz_6xCg5bLMNOEUwFt6SFKn0I8"   # <-- сюда вставьте токен от @BotFather
ADMIN_ID = 795935690                   # <-- сюда вставьте свой Telegram ID

PROTECTION_PRICE = 500  # доплата за защиту двигателя (мойка двигателя)

ENGINE_OPTIONS = {
    "top":    {"label": "Сверху", "base_price": 1000},
    "bottom": {"label": "Снизу", "base_price": 1000},
    "both":   {"label": "Сверху и снизу", "base_price": 2000},
}

BODY_OPTIONS = {
    "phase1":        {"name": "🚗 Мойка кузова: первая фаза", "price": 500, "duration": 30, "category": "body"},
    "phase2":        {"name": "🚗 Мойка кузова: вторая фаза", "price": 1000, "duration": 60, "category": "body"},
    "phase2_quartz": {"name": "🚗 Мойка кузова: двухфазная + кварц", "price": 1500, "duration": 90, "category": "body"},
}

ANTIKOR_TEXT = (
    "🧪🛡️ Антикоррозийная обработка\n\n"
    "Временно в разработке 🛠\n\n"
    "Если хотите записаться на эту услугу, пишите:\n"
    "Telegram: @vna222renko\n"
    "MAX: +79375977780"
)

WORK_START_MIN = 10 * 60   # 10:00
WORK_END_MIN = 18 * 60     # 18:00
SLOT_STEP_MIN = 30         # шаг сетки времени
DAYS_AHEAD = 7             # только на неделю вперёд

DEFAULT_DISCOUNT_PERCENT = 10   # скидка по умолчанию на любую услугу
ALL_CATEGORIES_DISCOUNT_PERCENT = 15  # скидка, если выбраны все 3 направления

WEEKDAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_RU_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
                 "июля", "августа", "сентября", "октября", "ноября", "декабря"]
# =====================================================

logging.basicConfig(level=logging.INFO)


# ---------- УТИЛИТЫ ----------
def format_date_ru(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{WEEKDAYS_RU[d.weekday()]}, {d.day} {MONTHS_RU_GEN[d.month - 1]}"


def time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def minutes_to_time(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def pluralize(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 10 < n_abs < 20:
        return many
    if 1 < n1 < 5:
        return few
    if n1 == 1:
        return one
    return many


def format_duration(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    parts = []
    if hours:
        parts.append(f"{hours} {pluralize(hours, 'час', 'часа', 'часов')}")
    if minutes:
        parts.append(f"{minutes} {pluralize(minutes, 'минута', 'минуты', 'минут')}")
    if not parts:
        parts.append("0 минут")
    return " ".join(parts)


def engine_duration(option: str, protection_yes: bool) -> int:
    if option == "bottom":
        return 60 if protection_yes else 30
    if option == "both":
        return 90
    return 30  # top


def cart_total_duration(cart):
    return sum(i["duration"] for i in cart)


def cart_total_price(cart):
    return sum(i["price"] for i in cart)


def cart_categories(cart):
    return {i.get("category") for i in cart if i.get("category")}


def cart_discount_percent(cart):
    if not cart:
        return 0
    cats = cart_categories(cart)
    if {"engine", "bottom", "body"}.issubset(cats):
        return ALL_CATEGORIES_DISCOUNT_PERCENT
    return DEFAULT_DISCOUNT_PERCENT


def cart_price_breakdown(cart):
    """Возвращает (изначальная сумма, сумма со скидкой, процент скидки)."""
    total = cart_total_price(cart)
    percent = cart_discount_percent(cart)
    discounted = round(total * (100 - percent) / 100)
    return total, discounted, percent


def cart_service_string(cart):
    return "; ".join(f"{i['name']} ({i['price']}₽)" for i in cart)


# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            service TEXT,
            duration_minutes INTEGER,
            price INTEGER,
            date TEXT,
            time TEXT,
            name TEXT,
            phone TEXT,
            car TEXT,
            created_at TEXT,
            reminder_day_sent INTEGER DEFAULT 0,
            reminder_hour_sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def add_booking(user_id, username, service, duration, price, date, time, name, phone, car):
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bookings (user_id, username, service, duration_minutes, price, date, time, name, phone, car, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, username, service, duration, price, date, time, name, phone, car, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_bookings_for_date(date):
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("SELECT time, duration_minutes FROM bookings WHERE date=?", (date,))
    rows = cur.fetchall()
    conn.close()
    return rows


def is_slot_available(date, start_time, duration):
    new_start = time_to_minutes(start_time)
    new_end = new_start + duration
    for existing_time, existing_duration in get_bookings_for_date(date):
        existing_start = time_to_minutes(existing_time)
        existing_end = existing_start + (existing_duration or 60)
        if new_start < existing_end and new_end > existing_start:
            return False
    return True


def generate_available_times(date, duration):
    times = []
    now = datetime.now()
    is_today = (date == now.strftime("%Y-%m-%d"))
    current_minutes = now.hour * 60 + now.minute

    t = WORK_START_MIN
    while t + duration <= WORK_END_MIN:
        if is_today and t <= current_minutes:
            t += SLOT_STEP_MIN
            continue
        time_str = minutes_to_time(t)
        if is_slot_available(date, time_str, duration):
            times.append(time_str)
        t += SLOT_STEP_MIN
    return times


def get_user_bookings(user_id):
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT id, service, date, time, price FROM bookings
        WHERE user_id=? AND date >= ? ORDER BY date, time
    """, (user_id, datetime.now().strftime("%Y-%m-%d")))
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_booking(booking_id, user_id):
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM bookings WHERE id=? AND user_id=?", (booking_id, user_id))
    conn.commit()
    conn.close()


def get_booking_by_id(booking_id):
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("SELECT service, date, time, name, phone, car, price FROM bookings WHERE id=?", (booking_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_all_bookings():
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT service, date, time, name, phone, car, price FROM bookings
        WHERE date >= ? ORDER BY date, time
    """, (datetime.now().strftime("%Y-%m-%d"),))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_due_reminders():
    now = datetime.now()
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, service, date, time, reminder_day_sent, reminder_hour_sent
        FROM bookings WHERE date >= ?
    """, (now.strftime("%Y-%m-%d"),))
    rows = cur.fetchall()
    conn.close()

    day_due, hour_due = [], []
    for id_, user_id, service, date, time_, day_sent, hour_sent in rows:
        appt = datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M")
        remaining_hours = (appt - now).total_seconds() / 3600
        if remaining_hours <= 0:
            continue
        if not day_sent and remaining_hours <= 24:
            day_due.append((id_, user_id, service, date, time_))
        if not hour_sent and remaining_hours <= 2:
            hour_due.append((id_, user_id, service, date, time_))
    return day_due, hour_due


def mark_reminder_sent(booking_id, column):
    conn = sqlite3.connect("bookings.db")
    cur = conn.cursor()
    cur.execute(f"UPDATE bookings SET {column}=1 WHERE id=?", (booking_id,))
    conn.commit()
    conn.close()


# ---------- СОСТОЯНИЯ (FSM) ----------
class Booking(StatesGroup):
    choosing_service = State()
    choosing_engine_option = State()
    choosing_engine_protection = State()
    choosing_bottom_option = State()
    choosing_bottom_protection = State()
    choosing_body_option = State()
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()
    entering_car = State()
    confirming = State()


router = Router()

# Постоянное меню внизу экрана (всегда под рукой)
MAIN_MENU_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧾 Услуги"), KeyboardButton(text="📋 Мои записи")],
        [KeyboardButton(text="🖼 Работы")]
    ],
    resize_keyboard=True
)


# ---------- ЭКРАНЫ ----------
async def show_main_menu(message_obj, state: FSMContext, send_new: bool = False):
    data = await state.get_data()
    cart = data.get("cart", [])

    if cart:
        total, discounted, percent = cart_price_breakdown(cart)
        text = "🧾 Ваш заказ:\n"
        for item in cart:
            text += f"• {item['name']} — {item['price']}₽\n"
        text += (
            f"\n⏱ Общее время: {format_duration(cart_total_duration(cart))}\n"
            f"💰 Итого: <s>{total}₽</s> <b>{discounted}₽</b> (скидка {percent}%)\n\n"
            "⬇️ Выберите ещё услугу или продолжите:\n\n"
        )
    else:
        text = (
            "👋 Добро пожаловать!\n\n"
            "Мы рады видеть вас. Чтобы мы могли предложить наиболее подходящие "
            "услуги и предоставить актуальную информацию, пожалуйста, выберите, "
            "что вас интересует.\n\n"
            "✅ Вы можете выбрать одну или несколько услуг.\n"
            f"🔥 Скидка {DEFAULT_DISCOUNT_PERCENT}% на любую услугу! А если выберете "
            f"мойку двигателя + днища + кузова — скидка {ALL_CATEGORIES_DISCOUNT_PERCENT}%!\n\n"
            "⬇️ Выберите интересующие вас услуги:\n\n"
        )

    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Мойка двигателя", callback_data="svc_engine")
    kb.button(text="🚘👇 Мойка днища", callback_data="svc_bottom")
    kb.button(text="🚗 Мойка кузова", callback_data="svc_body")
    if not cart:
        kb.button(text="🧪🛡️ Антикор", callback_data="svc_anticor")
    if cart:
        kb.button(text="✅ Продолжить к выбору даты", callback_data="cart_continue")
        kb.button(text="🗑 Очистить всё", callback_data="cart_clear")
    kb.adjust(1)
    markup = kb.as_markup()

    if send_new:
        await message_obj.answer(text, reply_markup=markup)
    else:
        try:
            await message_obj.edit_text(text, reply_markup=markup)
        except Exception:
            await message_obj.answer(text, reply_markup=markup)


async def show_antikor_info(message: Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data="back_to_main")
    kb.adjust(1)
    await message.edit_text(ANTIKOR_TEXT, reply_markup=kb.as_markup())


async def show_engine_options(message: Message):
    text = (
        "⚙️ Мойка двигателя\n\n"
        f"🔥 Скидка {DEFAULT_DISCOUNT_PERCENT}% на эту услугу (применяется при оформлении заказа)\n\n"
        "Сверху — 1000₽\n"
        "Снизу — от 1000₽ (уточним про защиту)\n"
        "Сверху и снизу — от 2000₽ (уточним про защиту)\n\n"
        "Выберите вариант:"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Сверху — 1000₽", callback_data="engine_opt:top")
    kb.button(text="Снизу — от 1000₽", callback_data="engine_opt:bottom")
    kb.button(text="Сверху и снизу — от 2000₽", callback_data="engine_opt:both")
    kb.button(text="⬅ Назад", callback_data="back_to_main")
    kb.adjust(1)
    await message.edit_text(text, reply_markup=kb.as_markup())


async def show_engine_protection(message: Message, label: str):
    text = f"⚙️ Мойка двигателя: {label}\n\nЕсть ли защита двигателя (потребуется её снятие)?"
    kb = InlineKeyboardBuilder()
    kb.button(text=f"Да (+{PROTECTION_PRICE}₽)", callback_data="engine_prot:yes")
    kb.button(text="Нет (+0₽)", callback_data="engine_prot:no")
    kb.button(text="⬅ Назад", callback_data="back_to_engine_opt")
    kb.adjust(1)
    await message.edit_text(text, reply_markup=kb.as_markup())


async def show_bottom_options(message: Message):
    text = (
        "🚘👇 Мойка днища\n\n"
        f"🔥 Скидка {DEFAULT_DISCOUNT_PERCENT}% на эту услугу (применяется при оформлении заказа)\n\n"
        "Стандартная — 1500₽ (без снятия колёс)\n"
        "Расширенная — 2500₽ (со снятием колёс и тщательной промывкой подвески)\n\n"
        "Выберите вариант:"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Стандартная — 1500₽", callback_data="bottom_opt:standard")
    kb.button(text="Расширенная — 2500₽", callback_data="bottom_opt:extended")
    kb.button(text="⬅ Назад", callback_data="back_to_main")
    kb.adjust(1)
    await message.edit_text(text, reply_markup=kb.as_markup())


async def show_bottom_protection(message: Message):
    text = "🚘👇 Расширенная мойка днища\n\nСнимать все защиты и подкрылки?"
    kb = InlineKeyboardBuilder()
    kb.button(text="Да (+2000₽)", callback_data="bottom_prot:yes")
    kb.button(text="Нет (+0₽)", callback_data="bottom_prot:no")
    kb.button(text="⬅ Назад", callback_data="back_to_bottom_opt")
    kb.adjust(1)
    await message.edit_text(text, reply_markup=kb.as_markup())


async def show_body_options(message: Message):
    text = (
        "🚗 Мойка кузова\n\n"
        f"🔥 Скидка {DEFAULT_DISCOUNT_PERCENT}% на эту услугу (применяется при оформлении заказа)\n\n"
        "Первая фаза — 500₽\n"
        "Вторая фаза — 1000₽\n"
        "Двухфазная + кварц — 1500₽\n\n"
        "Выберите вариант:"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Первая фаза — 500₽", callback_data="body_opt:phase1")
    kb.button(text="Вторая фаза — 1000₽", callback_data="body_opt:phase2")
    kb.button(text="Двухфазная + кварц — 1500₽", callback_data="body_opt:phase2_quartz")
    kb.button(text="⬅ Назад", callback_data="back_to_main")
    kb.adjust(1)
    await message.edit_text(text, reply_markup=kb.as_markup())


def dates_keyboard():
    kb = InlineKeyboardBuilder()
    today = datetime.now()
    for i in range(DAYS_AHEAD):
        d = today + timedelta(days=i)
        kb.button(text=format_date_ru(d.strftime("%Y-%m-%d")), callback_data=f"date:{d.strftime('%Y-%m-%d')}")
    kb.button(text="⬅ Назад", callback_data="back_to_main")
    kb.adjust(1)
    return kb.as_markup()


def times_keyboard(times):
    kb = InlineKeyboardBuilder()
    for t in times:
        kb.button(text=t, callback_data=f"time:{t}")
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_date"))
    return kb.as_markup()


def confirm_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data="confirm")
    kb.button(text="❌ Отменить", callback_data="cancel")
    kb.adjust(2)
    return kb.as_markup()


def contact_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


async def add_to_cart(state: FSMContext, item: dict):
    data = await state.get_data()
    cart = data.get("cart", [])
    cart.append(item)
    await state.update_data(cart=cart)


# ---------- СТАРТ ----------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Здравствуйте! 👋 Я бот для записи на автомойку.", reply_markup=MAIN_MENU_KB)
    await show_main_menu(message, state, send_new=True)
    await state.set_state(Booking.choosing_service)


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. Введите /start чтобы начать заново.", reply_markup=MAIN_MENU_KB)


@router.message(Command("anticor"))
async def anticor_cmd(message: Message):
    await message.answer(ANTIKOR_TEXT)


# ---------- ПОСТОЯННОЕ МЕНЮ ВНИЗУ ----------
@router.message(F.text == "🧾 Услуги")
async def menu_services(message: Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message, state, send_new=True)
    await state.set_state(Booking.choosing_service)


@router.message(F.text == "📋 Мои записи")
async def menu_my_bookings(message: Message):
    await my_bookings(message)


@router.message(F.text == "🖼 Работы")
async def menu_our_works(message: Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="Открыть группу с примерами работ", url="https://t.me/aautooo")
    kb.adjust(1)
    await message.answer("Здесь наши примеры работ 👇", reply_markup=kb.as_markup())


# ---------- ГЛАВНОЕ МЕНЮ / КОРЗИНА ----------
@router.callback_query(Booking.choosing_service, F.data == "svc_engine")
async def svc_engine(callback: CallbackQuery, state: FSMContext):
    await show_engine_options(callback.message)
    await state.set_state(Booking.choosing_engine_option)
    await callback.answer()


@router.callback_query(Booking.choosing_service, F.data == "svc_bottom")
async def svc_bottom(callback: CallbackQuery, state: FSMContext):
    await show_bottom_options(callback.message)
    await state.set_state(Booking.choosing_bottom_option)
    await callback.answer()


@router.callback_query(Booking.choosing_service, F.data == "svc_body")
async def svc_body(callback: CallbackQuery, state: FSMContext):
    await show_body_options(callback.message)
    await state.set_state(Booking.choosing_body_option)
    await callback.answer()


@router.callback_query(Booking.choosing_service, F.data == "svc_anticor")
async def svc_anticor(callback: CallbackQuery, state: FSMContext):
    await show_antikor_info(callback.message)
    await callback.answer()


@router.callback_query(Booking.choosing_service, F.data == "cart_clear")
async def cart_clear(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart=[])
    await show_main_menu(callback.message, state)
    await callback.answer("Корзина очищена")


@router.callback_query(Booking.choosing_service, F.data == "cart_continue")
async def cart_continue(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", [])
    if not cart:
        await callback.answer("Сначала выберите хотя бы одну услугу", show_alert=True)
        return
    await callback.message.edit_text("Выберите дату:", reply_markup=dates_keyboard())
    await state.set_state(Booking.choosing_date)
    await callback.answer()


# ---------- НАЗАД (универсальные обработчики) ----------
@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Booking.choosing_service)
    await show_main_menu(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "back_to_engine_opt")
async def back_to_engine_opt(callback: CallbackQuery, state: FSMContext):
    await show_engine_options(callback.message)
    await state.set_state(Booking.choosing_engine_option)
    await callback.answer()


@router.callback_query(F.data == "back_to_bottom_opt")
async def back_to_bottom_opt(callback: CallbackQuery, state: FSMContext):
    await show_bottom_options(callback.message)
    await state.set_state(Booking.choosing_bottom_option)
    await callback.answer()


@router.callback_query(F.data == "back_to_date")
async def back_to_date_handler(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Выберите дату:", reply_markup=dates_keyboard())
    await state.set_state(Booking.choosing_date)
    await callback.answer()


@router.callback_query(F.data == "back_to_time")
async def back_to_time_handler(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date = data.get("date")
    total_duration = cart_total_duration(data.get("cart", []))
    times = generate_available_times(date, total_duration)
    if not times:
        await callback.message.edit_text("Похоже, слоты изменились. Выберите дату заново:", reply_markup=dates_keyboard())
        await state.set_state(Booking.choosing_date)
        await callback.answer()
        return
    await callback.message.edit_text(
        f"Дата: {format_date_ru(date)}\n\nВыберите время:",
        reply_markup=times_keyboard(times)
    )
    await state.set_state(Booking.choosing_time)
    await callback.answer()


@router.callback_query(F.data == "back_to_name")
async def back_to_name_handler(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Как к вам обращаться? Напишите ваше имя:", reply_markup=ReplyKeyboardRemove())
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data="back_to_time")
    kb.adjust(1)
    await callback.message.answer("Для возврата к выбору времени:", reply_markup=kb.as_markup())
    await state.set_state(Booking.entering_name)
    await callback.answer()


@router.callback_query(F.data == "back_to_phone")
async def back_to_phone_handler(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Отправьте номер телефона кнопкой ниже или введите вручную:",
        reply_markup=contact_keyboard()
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data="back_to_name")
    kb.adjust(1)
    await callback.message.answer("Для возврата к вводу имени:", reply_markup=kb.as_markup())
    await state.set_state(Booking.entering_phone)
    await callback.answer()


# ---------- МОЙКА ДВИГАТЕЛЯ ----------
@router.callback_query(Booking.choosing_engine_option, F.data.startswith("engine_opt:"))
async def engine_opt_handler(callback: CallbackQuery, state: FSMContext):
    option = callback.data.split(":", 1)[1]
    label = ENGINE_OPTIONS[option]["label"]
    base_price = ENGINE_OPTIONS[option]["base_price"]

    if option == "top":
        item = {"name": f"⚙️ Мойка двигателя: {label}", "price": base_price, "duration": 30, "category": "engine"}
        await add_to_cart(state, item)
        await show_main_menu(callback.message, state)
        await state.set_state(Booking.choosing_service)
    else:
        await state.update_data(engine_temp_option=option, engine_temp_label=label, engine_temp_base=base_price)
        await show_engine_protection(callback.message, label)
        await state.set_state(Booking.choosing_engine_protection)
    await callback.answer()


@router.callback_query(Booking.choosing_engine_protection, F.data.startswith("engine_prot:"))
async def engine_prot_handler(callback: CallbackQuery, state: FSMContext):
    answer = callback.data.split(":", 1)[1]
    data = await state.get_data()
    option = data["engine_temp_option"]
    label = data["engine_temp_label"]
    base_price = data["engine_temp_base"]

    protection_yes = (answer == "yes")
    price = base_price + (PROTECTION_PRICE if protection_yes else 0)
    duration = engine_duration(option, protection_yes)
    protection_text = "со снятием защиты" if protection_yes else "без защиты"

    item = {
        "name": f"⚙️ Мойка двигателя: {label} ({protection_text})",
        "price": price,
        "duration": duration,
        "category": "engine",
    }
    await add_to_cart(state, item)
    await show_main_menu(callback.message, state)
    await state.set_state(Booking.choosing_service)
    await callback.answer()


# ---------- МОЙКА ДНИЩА ----------
@router.callback_query(Booking.choosing_bottom_option, F.data.startswith("bottom_opt:"))
async def bottom_opt_handler(callback: CallbackQuery, state: FSMContext):
    option = callback.data.split(":", 1)[1]
    if option == "standard":
        item = {
            "name": "🚘👇 Мойка днища: стандартная (без снятия колёс)",
            "price": 1500,
            "duration": 30,
            "category": "bottom",
        }
        await add_to_cart(state, item)
        await show_main_menu(callback.message, state)
        await state.set_state(Booking.choosing_service)
    else:
        await show_bottom_protection(callback.message)
        await state.set_state(Booking.choosing_bottom_protection)
    await callback.answer()


@router.callback_query(Booking.choosing_bottom_protection, F.data.startswith("bottom_prot:"))
async def bottom_prot_handler(callback: CallbackQuery, state: FSMContext):
    answer = callback.data.split(":", 1)[1]
    yes = (answer == "yes")
    price = 2500 + (2000 if yes else 0)
    duration = 150 if yes else 60
    suffix = "со снятием защит и подкрылков" if yes else "без снятия защит и подкрылков"

    item = {
        "name": f"🚘👇 Мойка днища: расширенная ({suffix})",
        "price": price,
        "duration": duration,
        "category": "bottom",
    }
    await add_to_cart(state, item)
    await show_main_menu(callback.message, state)
    await state.set_state(Booking.choosing_service)
    await callback.answer()


# ---------- МОЙКА КУЗОВА ----------
@router.callback_query(Booking.choosing_body_option, F.data.startswith("body_opt:"))
async def body_opt_handler(callback: CallbackQuery, state: FSMContext):
    option = callback.data.split(":", 1)[1]
    item = dict(BODY_OPTIONS[option])
    await add_to_cart(state, item)
    await show_main_menu(callback.message, state)
    await state.set_state(Booking.choosing_service)
    await callback.answer()


# ---------- ДАТА / ВРЕМЯ ----------
@router.callback_query(Booking.choosing_date, F.data.startswith("date:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    date = callback.data.split(":", 1)[1]
    await state.update_data(date=date)
    data = await state.get_data()
    total_duration = cart_total_duration(data.get("cart", []))

    times = generate_available_times(date, total_duration)
    if not times:
        await callback.answer("На эту дату нет свободных окон на нужное время, выберите другую", show_alert=True)
        return

    await callback.message.edit_text(
        f"Дата: {format_date_ru(date)}\n\nВыберите время:",
        reply_markup=times_keyboard(times)
    )
    await state.set_state(Booking.choosing_time)
    await callback.answer()


@router.callback_query(Booking.choosing_time, F.data.startswith("time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    time = callback.data.split(":", 1)[1]
    await state.update_data(time=time)

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data="back_to_time")
    kb.adjust(1)
    await callback.message.edit_text("Как к вам обращаться? Напишите ваше имя:", reply_markup=kb.as_markup())
    await state.set_state(Booking.entering_name)
    await callback.answer()


# ---------- ИМЯ / ТЕЛЕФОН / МАШИНА ----------
@router.message(Booking.entering_name)
async def enter_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(
        "Отправьте номер телефона кнопкой ниже или введите вручную:",
        reply_markup=contact_keyboard()
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data="back_to_name")
    kb.adjust(1)
    await message.answer("Либо вернуться назад:", reply_markup=kb.as_markup())
    await state.set_state(Booking.entering_phone)


@router.message(Booking.entering_phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    await process_phone(message, state, message.contact.phone_number)


@router.message(Booking.entering_phone, F.text)
async def enter_phone_text(message: Message, state: FSMContext):
    await process_phone(message, state, message.text)


async def process_phone(message: Message, state: FSMContext, phone: str):
    await state.update_data(phone=phone)
    await message.answer(
        "Напишите марку и модель вашего автомобиля (например: Toyota Camry):",
        reply_markup=ReplyKeyboardRemove()
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data="back_to_phone")
    kb.adjust(1)
    await message.answer("Либо вернуться назад:", reply_markup=kb.as_markup())
    await state.set_state(Booking.entering_car)


@router.message(Booking.entering_car)
async def enter_car(message: Message, state: FSMContext):
    await state.update_data(car=message.text)
    data = await state.get_data()
    cart = data.get("cart", [])

    total, discounted, percent = cart_price_breakdown(cart)
    total_duration = cart_total_duration(cart)
    services_text = "\n".join(f"• {i['name']} — {i['price']}₽" for i in cart)
    date_fmt = format_date_ru(data["date"])

    text = (
        "Проверьте данные записи:\n\n"
        f"🛠 Услуги:\n{services_text}\n\n"
        f"⏱ Общее время: {format_duration(total_duration)}\n"
        f"💰 Итого: <s>{total}₽</s> <b>{discounted}₽</b> (скидка {percent}%)\n"
        f"📅 Дата: {date_fmt}\n"
        f"🕐 Время: {data['time']}\n"
        f"👤 Имя: {data['name']}\n"
        f"📞 Телефон: {data['phone']}\n"
        f"🚗 Машина: {data['car']}\n\n"
        "Всё верно?"
    )
    await message.answer(text, reply_markup=confirm_keyboard())
    await state.set_state(Booking.confirming)


# ---------- ПОДТВЕРЖДЕНИЕ ----------
@router.callback_query(Booking.confirming, F.data == "confirm")
async def confirm_booking(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    cart = data.get("cart", [])
    total_duration = cart_total_duration(cart)
    total, discounted, percent = cart_price_breakdown(cart)
    service_str = cart_service_string(cart)

    if not is_slot_available(data["date"], data["time"], total_duration):
        await callback.message.edit_text("К сожалению, это время уже заняли. Начните запись заново: /start")
        await state.clear()
        await callback.answer()
        return

    add_booking(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        service=service_str,
        duration=total_duration,
        price=discounted,
        date=data["date"],
        time=data["time"],
        name=data["name"],
        phone=data["phone"],
        car=data["car"],
    )

    date_fmt = format_date_ru(data["date"])
    services_list_text = "\n".join(f"• {i['name']}" for i in cart)

    await callback.message.edit_text(
        "✅ Вы успешно записаны!\n\n"
        f"🛠 Услуги:\n{services_list_text}\n\n"
        f"📅 {date_fmt} в {data['time']}\n"
        f"⏱ Время: {format_duration(total_duration)}\n"
        f"💰 Итого: <s>{total}₽</s> <b>{discounted}₽</b> (скидка {percent}%)\n\n"
        "Ждём вас!"
    )

    admin_services_text = "\n".join(f"• {i['name']} — {i['price']}₽" for i in cart)
    admin_text = (
        "🆕 Новая запись!\n\n"
        f"🛠 Услуги:\n{admin_services_text}\n\n"
        f"📅 Дата: {date_fmt}\n"
        f"🕐 Время: {data['time']}\n"
        f"⏱ Длительность: {format_duration(total_duration)}\n"
        f"👤 Имя: {data['name']}\n"
        f"📞 Телефон: {data['phone']}\n"
        f"🚗 Машина: {data['car']}\n"
        f"💰 Сумма без скидки: {total}₽\n"
        f"💰 Итого со скидкой {percent}%: {discounted}₽\n"
        f"👤 Telegram: @{callback.from_user.username or 'нет username'} (id: {callback.from_user.id})"
    )
    try:
        await bot.send_message(ADMIN_ID, admin_text)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление админу: {e}")

    await state.clear()
    await callback.message.answer("Хотите записаться ещё раз?", reply_markup=MAIN_MENU_KB)
    await show_main_menu(callback.message, state, send_new=True)
    await state.set_state(Booking.choosing_service)
    await callback.answer()


@router.callback_query(Booking.confirming, F.data == "cancel")
async def cancel_booking(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Запись отменена.")
    await state.clear()
    await callback.message.answer("Выберите услугу:", reply_markup=MAIN_MENU_KB)
    await show_main_menu(callback.message, state, send_new=True)
    await state.set_state(Booking.choosing_service)
    await callback.answer()


# ---------- МОИ ЗАПИСИ / ОТМЕНА ----------
@router.message(Command("my_bookings"))
async def my_bookings(message: Message):
    rows = get_user_bookings(message.from_user.id)
    if not rows:
        await message.answer("У вас пока нет предстоящих записей.")
        return

    await message.answer("📋 Ваши предстоящие записи:")
    for booking_id, service, date, time_, price in rows:
        date_fmt = format_date_ru(date)
        price_text = f"\n💰 {price}₽" if price else ""
        text = f"🛠 {service}\n📅 {date_fmt} в {time_}{price_text}"
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Отменить эту запись", callback_data=f"cancel_booking:{booking_id}")
        await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("cancel_booking:"))
async def cancel_existing_booking(callback: CallbackQuery, bot: Bot):
    booking_id = int(callback.data.split(":", 1)[1])
    booking = get_booking_by_id(booking_id)
    delete_booking(booking_id, callback.from_user.id)
    await callback.message.edit_text("Запись отменена ❌")
    await callback.answer("Запись отменена")

    if booking:
        service, date, time_, name, phone, car, price = booking
        date_fmt = format_date_ru(date)
        price_text = f"{price}₽" if price else "уточняется"
        admin_text = (
            "❌ Клиент отменил запись!\n\n"
            f"🛠 Услуги: {service}\n"
            f"📅 Дата: {date_fmt}\n"
            f"🕐 Время: {time_}\n"
            f"👤 Имя: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"🚗 Машина: {car}\n"
            f"💰 Сумма: {price_text}\n"
            f"👤 Telegram: @{callback.from_user.username or 'нет username'} (id: {callback.from_user.id})"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление об отмене: {e}")


@router.message(Command("bookings"))
async def all_bookings(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = get_all_bookings()
    if not rows:
        await message.answer("Записей пока нет.")
        return
    text = "📋 Все предстоящие записи:\n\n"
    for service, date, time_, name, phone, car, price in rows:
        date_fmt = format_date_ru(date)
        price_text = f"{price}₽" if price else "уточняется"
        text += f"🛠 {service}\n📅 {date_fmt} в {time_}\n👤 {name}, 📞 {phone}, 🚗 {car}\n💰 {price_text}\n\n"
    await message.answer(text)


# ---------- НАПОМИНАНИЯ ----------
async def reminder_loop(bot: Bot):
    while True:
        try:
            day_due, hour_due = get_due_reminders()

            for id_, user_id, service, date, time_ in day_due:
                try:
                    await bot.send_message(
                        user_id,
                        f"⏰ Напоминание: завтра у вас запись!\n\n🛠 {service}\n📅 {format_date_ru(date)} в {time_}"
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить напоминание за день: {e}")
                mark_reminder_sent(id_, "reminder_day_sent")

            for id_, user_id, service, date, time_ in hour_due:
                try:
                    await bot.send_message(
                        user_id,
                        f"⏰ Напоминание: через пару часов у вас запись!\n\n🛠 {service}\n📅 {format_date_ru(date)} в {time_}"
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить напоминание за пару часов: {e}")
                mark_reminder_sent(id_, "reminder_hour_sent")
        except Exception as e:
            logging.error(f"Ошибка в reminder_loop: {e}")

        await asyncio.sleep(60)


# ---------- ЗАПУСК ----------
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    asyncio.create_task(reminder_loop(bot))

    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())