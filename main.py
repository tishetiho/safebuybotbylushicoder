import asyncio
import logging
import sqlite3
import time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiocryptopay import AioCryptoPay
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8033692443:AAEN894GteeRWWYpvjk0t9t-z7hcCYExEsY'
ADMIN_ID = 5078764886  # Твой ID в Telegram
CRYPTO_BOT_TOKEN = '579651:AAPm1XLH5mHg6hYlZepMXmYc4j22JrqV6Al'

logging.basicConfig(level=logging.INFO)

crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN) # Поменяй на 'testnet' для тестов
scheduler = AsyncIOScheduler()
scheduler.start()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('safebuy.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, 
        balance REAL DEFAULT 0, 
        rating REAL DEFAULT 5.0, 
        deals_count INTEGER DEFAULT 0,
        currency TEXT DEFAULT 'RUB'
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER,
        title TEXT,
        price REAL,
        description TEXT,
        type TEXT, -- 'auto' or 'manual'
        status TEXT DEFAULT 'on_moderation' -- 'active', 'sold'
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        buyer_id INTEGER,
        seller_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'open', -- 'open', 'dispute', 'closed'
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

# --- СОСТОЯНИЯ (FSM) ---
class CreateItem(StatesGroup):
    title = State()
    price = State()
    description = State()
    delivery_type = State()
class AdminStates(StatesGroup):
    mailing_text = State()
    ban_id = State()
    
# --- КЛАВИАТУРЫ ---
def main_menu():
    kb = [
        [InlineKeyboardButton(text="🛒 Маркет", callback_data="market")],
        [InlineKeyboardButton(text="📦 Продать", callback_data="sell")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ХЕНДЛЕРЫ ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: types.Message):
    conn = sqlite3.connect('safebuy.db')
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    
    await message.answer(
        f"👋 Добро пожаловать в **SafeBuy**!\n\n"
        f"Здесь ты можешь безопасно торговать вещами с минимальной комиссией.",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "market")
async def show_market(callback: types.CallbackQuery):
    conn = sqlite3.connect('safebuy.db')
    # Берем только активные товары
    items = conn.execute("SELECT id, title, price FROM items WHERE status = 'active'").fetchall()
    conn.close()

    if not items:
        return await callback.answer("📦 Маркет пока пуст. Будь первым продавцом!", show_alert=True)

    text = "🛒 **Витрина товаров SafeBuy**\n\nВыберите интересующий товар:"
    kb = []
    
    for item in items:
        kb.append([InlineKeyboardButton(text=f"{item[1]} | {item[2]} ₽", callback_data=f"view_{item[0]}")])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu_back")])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("view_"))
async def view_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_")[1]
    conn = sqlite3.connect('safebuy.db')
    item = conn.execute("SELECT title, price, description, seller_id, type FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()

    text = (f"📦 **{item[0]}**\n\n"
            f"💰 Цена: {item[1]} ₽\n"
            f"📝 Описание: {item[2]}\n"
            f"🚚 Тип: {item[4]}\n"
            f"👤 Продавец: ID {item[3]}")

    kb = [
        [InlineKeyboardButton(text="💳 Купить", callback_data=f"buy_{item_id}")],
        [InlineKeyboardButton(text="🔙 К маркету", callback_data="market")]
    ]
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("dispute_"))
async def open_dispute(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[1]
    
    conn = sqlite3.connect('safebuy.db')
    conn.execute("UPDATE deals SET status = 'dispute' WHERE id = ?", (deal_id,))
    deal = conn.execute("SELECT buyer_id, seller_id, amount FROM deals WHERE id = ?", (deal_id,)).fetchone()
    conn.commit()
    conn.close()

    # Уведомляем стороны
    await callback.message.answer("⚠️ Спор открыт. Ожидайте вмешательства администратора. Деньги заморожены.")
    await bot.send_message(deal[1], f"⚠️ Покупатель открыл спор по сделке #{deal_id}. Не выходите из сети.")

    # Уведомляем админа
    admin_kb = [
        [InlineKeyboardButton(text="💰 Вернуть покупателю", callback_data=f"refund_{deal_id}")],
        [InlineKeyboardButton(text="💸 Отдать продавцу", callback_data=f"pay_seller_{deal_id}")]
    ]
    await bot.send_message(ADMIN_ID, 
                           f"🚨 **АРБИТРАЖ (Сделка #{deal_id})**\n\n"
                           f"Покупатель: {deal[0]}\n"
                           f"Продавец: {deal[1]}\n"
                           f"Сумма: {deal[2]} ₽", 
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_kb))

# --- ЛОГИКА РЕШЕНИЯ СПОРА АДМИНОМ ---

@dp.callback_query(F.data.startswith("refund_"))
async def admin_refund(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[1]
    # Логика: деньги просто не зачисляются продавцу, 
    # а в идеале — возвращаются на баланс покупателя в боте
    await callback.message.edit_text(f"✅ Спор #{deal_id} решен: Деньги возвращены покупателю.")
    # (Здесь нужно дописать UPDATE users SET balance = balance + amount...)

@dp.callback_query(F.data.startswith("pay_seller_"))
async def admin_pay_seller(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[1]
    # Принудительно вызываем функцию разморозки из предыдущего шага
    # release_funds(...)
    await callback.message.edit_text(f"✅ Спор #{deal_id} решен: Деньги отправлены продавцу.")
    
@dp.callback_query(F.data == "profile")
async def profile(callback: types.CallbackQuery):
    conn = sqlite3.connect('safebuy.db')
    user = conn.execute(
        "SELECT balance, rating, deals_count, currency FROM users WHERE id = ?", 
        (callback.from_user.id,)
    ).fetchone()
    conn.close()
    
    # Логика комиссии
    deals = user[2]
    comm = 9
    if 10 < deals <= 50: comm = 7
    elif deals > 50: comm = 5

    # Выбор символа валюты
    cur_symbol = "₽" if user[3] == 'RUB' else "₮"
    
    text = (f"👤 **Ваш профиль:**\n\n"
            f"💰 Баланс: {user[0]:.2f} {cur_symbol}\n"
            f"⭐ Рейтинг: {user[1]}/5.0\n"
            f"🤝 Сделок: {user[2]}\n"
            f"📉 Ваша комиссия на вывод: {comm}%\n"
            f"🌍 Валюта отображения: {user[3]}")
    
    # Кнопки профиля
    kb = [
        [InlineKeyboardButton(text="💸 Вывести средства", callback_data="withdraw_start")],
        [InlineKeyboardButton(text="⚙️ Сменить валюту (RUB/USDT)", callback_data="switch_currency")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu_back")]
    ]
    
    await callback.message.edit_text(
        text, 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), 
        parse_mode="Markdown"
    )
  
@dp.callback_query(F.data == "sell")
async def start_sell(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите название товара:")
    await state.set_state(CreateItem.title)

@dp.message(CreateItem.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Введите цену товара (в рублях):")
    await state.set_state(CreateItem.price)

@dp.message(CreateItem.price)
async def process_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Пожалуйста, введите число.")
    await state.update_data(price=float(message.text))
    await message.answer("Введите описание товара:")
    await state.set_state(CreateItem.description)

@dp.message(CreateItem.description)
async def process_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    kb = [
        [InlineKeyboardButton(text="🤖 Авто-выдача", callback_data="type_auto")],
        [InlineKeyboardButton(text="🤝 Ручная передача", callback_data="type_manual")]
    ]
    await message.answer("Выберите тип передачи:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(CreateItem.delivery_type)

@dp.callback_query(F.data.startswith("buy_"))
async def create_deal(callback: types.CallbackQuery):
    item_id = callback.data.split("_")[1]
    
    conn = sqlite3.connect('safebuy.db')
    item = conn.execute("SELECT title, price, seller_id FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()

    # Создаем инвойс (убедись, что закрывающая скобка ) стоит на своем месте)
    invoice = await crypto.create_invoice(
        amount=item[1], 
        fiat='RUB', 
        currency_type='fiat'
    )

    # Строка 262 — должна иметь ровно 4 пробела от края
    await callback.message.answer(
        f"💳 Оплатите товар **{item[0]}**\n"
        f"Сумма: {item[1]} ₽\n\n"
        f"После оплаты бот автоматически откроет чат с продавцом.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", url=invoice.bot_invoice_url)],
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check_{invoice.id}_{item_id}")]
        ])
    )

@dp.message(F.text)
async def chat_mediator(message: types.Message):
    """Пересылает сообщения между покупателем и продавцом во время сделки"""
    conn = sqlite3.connect('safebuy.db')
    # Ищем активную сделку, где участвует этот пользователь
    deal = conn.execute("""
        SELECT id, buyer_id, seller_id FROM deals 
        WHERE (buyer_id = ? OR seller_id = ?) AND status = 'open'
    """, (message.from_user.id, message.from_user.id)).fetchone()
    
    if deal:
        target_id = deal[2] if message.from_user.id == deal[1] else deal[1]
        role = "Покупатель" if message.from_user.id == deal[1] else "Продавец"
        
        try:
            await bot.send_message(
                target_id, 
                f"💬 **Сообщение от {role}:**\n{message.text}"
            )
        except Exception:
            await message.answer("⚠️ Не удалось отправить сообщение оппоненту.")
    conn.close()

# --- ЗАВЕРШЕНИЕ СДЕЛКИ И ХОЛД (36 ЧАСОВ) ---

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_deal(callback: types.CallbackQuery):
    deal_id = callback.data.split("_")[1]
    
    conn = sqlite3.connect('safebuy.db')
    deal = conn.execute("SELECT seller_id, amount FROM deals WHERE id = ?", (deal_id,)).fetchone()
    
    # Закрываем сделку
    conn.execute("UPDATE deals SET status = 'closed' WHERE id = ?", (deal_id,))
    conn.commit()
    
    await callback.message.answer("✅ Вы подтвердили получение. Деньги заморожены на 36 часов для безопасности.")
    
    # Ставим задачу в планировщик на выплату через 36 часов
    run_date = datetime.now() + timedelta(hours=36)
    scheduler.add_job(release_funds, 'date', run_date=run_date, args=[deal[0], deal[1], deal_id])
    conn.close()

async def release_funds(seller_id, amount, deal_id):
    """Функция разморозки денег и зачисления на баланс с учетом комиссии"""
    conn = sqlite3.connect('safebuy.db')
    user = conn.execute("SELECT deals_count FROM users WHERE id = ?", (seller_id,)).fetchone()
    
    # Твоя логика комиссии (5-9%)
    deals = user[0]
    fee_percent = 9 if deals <= 10 else (7 if deals <= 50 else 5)
    final_amount = amount * (1 - fee_percent/100)
    
    conn.execute("UPDATE users SET balance = balance + ?, deals_count = deals_count + 1 WHERE id = ?", 
                 (final_amount, seller_id))
    conn.commit()
    conn.close()
    
    await bot.send_message(seller_id, f"💰 Деньги за сделку #{deal_id} разморожены и зачислены на баланс!")

# --- ВЫВОД СРЕДСТВ (Через чеки Crypto Bot) ---

@dp.callback_query(F.data == "withdraw")
async def withdraw_money(callback: types.CallbackQuery):
    conn = sqlite3.connect('safebuy.db')
    user = conn.execute("SELECT balance FROM users WHERE id = ?", (callback.from_user.id,)).fetchone()
    
    if user[0] < 100:
        return await callback.answer("Минимальная сумма вывода — 100 ₽", show_alert=True)
    
    # Создаем чек на выплату через API
    # В реальности тут нужен баланс на самом Crypto Pay приложении
    try:
        check = await crypto.create_check(asset='USDT', amount=user[0]/90) # Конвертация по курсу (примерно)
        conn.execute("UPDATE users SET balance = 0 WHERE id = ?", (callback.from_user.id,))
        conn.commit()
        
        await callback.message.answer(
            f"✅ Выплата сформирована!\nЗаберите ваш чек: {check.bot_check_url}",
            reply_markup=main_menu()
        )
    except Exception as e:
        await callback.answer("Ошибка API или недостаточно средств на шлюзе.", show_alert=True)
    conn.close()

# --- АДМИН-ПАНЕЛЬ ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect('safebuy.db')
    cur = conn.cursor()
    
    # Собираем статистику
    total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_balance = cur.execute("SELECT SUM(balance) FROM users").fetchone()[0] or 0
    in_hold = cur.execute("SELECT SUM(amount) FROM deals WHERE status = 'open'").fetchone()[0] or 0
    pending_items = cur.execute("SELECT COUNT(*) FROM items WHERE status = 'on_moderation'").fetchone()[0]
    conn.close()

    text = (f"🛠 **Админ-панель SafeBuy**\n\n"
            f"👥 Всего юзеров: {total_users}\n"
            f"💰 Баланс всех: {total_balance:.2f} ₽\n"
            f"⏳ В холде (сделки): {in_hold:.2f} ₽\n"
            f"📦 Ожидают модерации: {pending_items}\n\n"
            f"Выберите действие:")

    kb = [
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="🚫 Забанить юзера", callback_data="admin_ban")],
        [InlineKeyboardButton(text="🔄 Режим: Mainnet", callback_data="toggle_net")] # Можно допилить смену текста
    ]
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

# --- ЛОГИКА РАССЫЛКИ ---

@dp.callback_query(F.data == "admin_mailing")
async def start_mailing(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст рассылки (или /cancel для отмены):")
    await state.set_state(AdminStates.mailing_text)

@dp.message(AdminStates.mailing_text)
async def process_mailing(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("Отменено.")

    conn = sqlite3.connect('safebuy.db')
    users = conn.execute("SELECT id FROM users").fetchall()
    conn.close()

    count = 0
    for user in users:
        try:
            await bot.send_message(user[0], message.text)
            count += 1
            await asyncio.sleep(0.05) # Защита от спам-фильтра ТГ
        except Exception:
            continue
    
    await message.answer(f"✅ Рассылка завершена! Получили: {count} чел.")
    await state.clear()

# --- МОДЕРАЦИЯ ТОВАРОВ (КНОПКИ ИЗ ПРЕДЫДУЩИХ ШАГОВ) ---

@dp.callback_query(F.data.startswith("approve_"))
async def approve_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_")[1]
    conn = sqlite3.connect('safebuy.db')
    # Получаем ID продавца перед обновлением
    seller_id = conn.execute("SELECT seller_id FROM items WHERE id = ?", (item_id,)).fetchone()[0]
    
    conn.execute("UPDATE items SET status = 'active' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text("✅ Товар одобрен и выставлен на маркет!")
    await bot.send_message(seller_id, "🥳 Ваш товар прошел модерацию и доступен для покупки!")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_item(callback: types.CallbackQuery):
    item_id = callback.data.split("_")[1]
    # Тут можно добавить FSM для ввода причины, но пока сделаем просто удаление
    conn = sqlite3.connect('safebuy.db')
    seller_id = conn.execute("SELECT seller_id FROM items WHERE id = ?", (item_id,)).fetchone()[0]
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text("❌ Товар отклонен и удален.")
    await bot.send_message(seller_id, "⚠️ Ваш товар отклонен модератором.")
    
@dp.callback_query(F.data == "switch_currency")
async def switch_currency(callback: types.CallbackQuery):
    conn = sqlite3.connect('safebuy.db')
    user = conn.execute("SELECT currency FROM users WHERE id = ?", (callback.from_user.id,)).fetchone()
    new_cur = 'USDT' if user[0] == 'RUB' else 'RUB'
    
    conn.execute("UPDATE users SET currency = ? WHERE id = ?", (new_cur, callback.from_user.id))
    conn.commit()
    conn.close()
    
    await callback.answer(f"Валюта изменена на {new_cur}")
    await profile(callback) # Обновляем сообщение профиля

@dp.callback_query(F.data == "withdraw_start")
async def withdraw_start(callback: types.CallbackQuery):
    conn = sqlite3.connect('safebuy.db')
    user = conn.execute("SELECT balance FROM users WHERE id = ?", (callback.from_user.id,)).fetchone()
    conn.close()

    if user[0] < 100:
        return await callback.answer("❌ Минимальная сумма для вывода — 100 ₽", show_alert=True)

    kb = [
        [InlineKeyboardButton(text="✅ Подтвердить вывод через Crypto Bot", callback_data="withdraw_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="profile")]
    ]
    
    await callback.message.edit_text(
        f"💳 **Запрос на вывод**\n\n"
        f"Ваш баланс: {user[0]:.2f} ₽\n"
        f"Будет создан чек в @CryptoBot.\n\n"
        f"Вы уверены?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

# Кнопка отмены/назад в главное меню
@dp.callback_query(F.data == "main_menu_back")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "👋 Добро пожаловать в SafeBuy!", 
        reply_markup=main_menu()
  )
  
@dp.callback_query(CreateItem.delivery_type)
async def finalize_item(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    item_type = "Авто" if callback.data == "type_auto" else "Ручная"
    
    # Сохраняем в БД со статусом модерации
    conn = sqlite3.connect('safebuy.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO items (seller_id, title, price, description, type) VALUES (?, ?, ?, ?, ?)",
                (callback.from_user.id, data['title'], data['price'], data['description'], item_type))
    item_id = cur.lastrowid
    conn.commit()
    conn.close()

    await callback.message.answer("✅ Товар отправлен на модерацию админу!")
    
    # Уведомление админу
    admin_kb = [
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{item_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{item_id}")]
    ]
    await bot.send_message(ADMIN_ID, 
                           f"🔔 **Новый товар на модерацию!**\n\n"
                           f"📦 {data['title']}\n"
                           f"💰 Цена: {data['price']} ₽\n"
                           f"📝 {data['description']}\n"
                           f"Тип: {item_type}", 
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_kb))
    await state.clear()

# --- ЗАПУСК ---
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
