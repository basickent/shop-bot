import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from html import escape

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo,
)

from config import ADMIN_IDS, BOT_TOKEN, DB_PATH, WEBAPP_URL
from database import init_db
from utils.receipt import format_receipt, fmt_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

dp = Dispatcher()

def shop_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛍️ Открыть магазин", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )

def order_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принял", callback_data=f"o:accept:{order_id}"),
        InlineKeyboardButton(text="❌ Отклонил", callback_data=f"o:reject:{order_id}"),
    ]])

# ============ АДМИН: ИЗМЕНЕНИЕ ЦЕН ============

class PriceState(StatesGroup):
    waiting_for_price = State()

async def get_root_categories_kb() -> InlineKeyboardMarkup:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM categories WHERE parent_id IS NULL OR parent_id = '' ORDER BY name")
        rows = await cur.fetchall()
    kb = [[InlineKeyboardButton(text=row[1], callback_data=f"admin_cat:{row[0]}")] for row in rows]
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("Выберите категорию:", reply_markup=await get_root_categories_kb())

@dp.callback_query(F.data.startswith("admin_cat:"))
async def admin_choose_cat(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    cat_id = call.data.split(":", 1)[1]
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM categories WHERE parent_id = ? ORDER BY name", (cat_id,))
        subs = await cur.fetchall()
        
    kb = []
    if subs:
        for s in subs:
            kb.append([InlineKeyboardButton(text=f"📁 {s[1]}", callback_data=f"admin_sub:{s[0]}")])
        kb.append([InlineKeyboardButton(text="📦 Все товары этой категории", callback_data=f"admin_items:{cat_id}")])
    else:
        kb.append([InlineKeyboardButton(text="📦 Выбрать товар", callback_data=f"admin_items:{cat_id}")])
        kb.append([InlineKeyboardButton(text="📂 Изменить цену на ВСЮ категорию", callback_data=f"admin_set_price:cat:{cat_id}")])
        
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")])
    await call.message.edit_text("Выберите подкатегорию (бренд) или действие:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data.startswith("admin_sub:"))
async def admin_choose_subcat(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    sub_id = call.data.split(":", 1)[1]
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM categories WHERE id = ?", (sub_id,))
        row = await cur.fetchone()
    
    kb = [
        [InlineKeyboardButton(text=f"📂 Изменить цену на ВСЕ товары в {row[0]}", callback_data=f"admin_set_price:sub:{sub_id}")],
        [InlineKeyboardButton(text="📦 Выбрать конкретный товар", callback_data=f"admin_items:{sub_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ]
    await call.message.edit_text(f"Подкатегория: <b>{escape(row[0])}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data == "admin_back")
async def admin_back(call: CallbackQuery):
    await call.message.edit_text("Выберите категорию:", reply_markup=await get_root_categories_kb())
    await call.answer()

@dp.callback_query(F.data.startswith("admin_items:"))
async def admin_list_items(call: CallbackQuery):
    cat_id = call.data.split(":", 1)[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, price FROM products WHERE category_id = ? ORDER BY name LIMIT 50", (cat_id,))
        rows = await cur.fetchall()
    
    if not rows:
        await call.message.edit_text("В этой категории нет товаров.")
        return
    
    kb = [[InlineKeyboardButton(text=f"{r[1]} — {fmt_price(r[2])}", callback_data=f"admin_item:{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")])
    await call.message.edit_text("Выберите товар для изменения цены:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data.startswith("admin_set_price:"))
async def admin_set_cat_price(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    mode = parts[1]
    target_id = parts[2]
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM categories WHERE id = ?", (target_id,))
        row = await cur.fetchone()
    
    await state.update_data(target_id=target_id, target_name=row[0], mode=mode)
    await state.set_state(PriceState.waiting_for_price)
    
    label = "категорию" if mode == 'cat' else "подкатегорию (бренд)"
    await call.message.edit_text(f"Введите новую цену для всех товаров в {label} <b>{escape(row[0])}</b> (в белорусских рублях):")
    await call.answer()

@dp.callback_query(F.data.startswith("admin_item:"))
async def admin_set_item_price(call: CallbackQuery, state: FSMContext):
    item_id = call.data.split(":", 1)[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM products WHERE id = ?", (item_id,))
        row = await cur.fetchone()
    
    await state.update_data(target_id=item_id, target_name=row[0], mode='item')
    await state.set_state(PriceState.waiting_for_price)
    await call.message.edit_text(f"Товар: <b>{escape(row[0])}</b>\nВведите новую цену (в BYN):")
    await call.answer()

@dp.message(PriceState.waiting_for_price)
async def admin_apply_price(message: Message, state: FSMContext):
    try:
        new_price = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer("⚠️ Введите число (например: 15.50 или 20)")
        return

    data = await state.get_data()
    mode = data['mode']
    target_id = data['target_id']
    target_name = data['target_name']
    
    prices_file = "prices.json"
    prices = {}
    if os.path.exists(prices_file):
        with open(prices_file, "r", encoding="utf-8") as f:
            try: prices = json.load(f)
            except: pass

    async with aiosqlite.connect(DB_PATH) as db:
        if mode == 'item':
            prices[target_id] = new_price
            await db.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, target_id))
            msg = f"✅ Цена на <b>{escape(target_name)}</b> изменена на <b>{fmt_price(new_price)}</b>."
        
        elif mode == 'cat':
            cur = await db.execute("""
                SELECT id FROM products WHERE category_id = ? 
                OR category_id IN (SELECT id FROM categories WHERE parent_id = ?)
            """, (target_id, target_id))
            rows = await cur.fetchall()
            for r in rows: prices[r[0]] = new_price
            
            await db.execute("""
                UPDATE products SET price = ? WHERE category_id = ? 
                OR category_id IN (SELECT id FROM categories WHERE parent_id = ?)
            """, (new_price, target_id, target_id))
            msg = f"✅ Цена на ВСЮ категорию <b>{escape(target_name)}</b> изменена на <b>{fmt_price(new_price)}</b>.\nОбновлено товаров: {len(rows)}."
            
        elif mode == 'sub':
            cur = await db.execute("SELECT id FROM products WHERE category_id = ?", (target_id,))
            rows = await cur.fetchall()
            for r in rows: prices[r[0]] = new_price
            
            await db.execute("UPDATE products SET price = ? WHERE category_id = ?", (new_price, target_id))
            msg = f"✅ Цена на подкатегорию (бренд) <b>{escape(target_name)}</b> изменена на <b>{fmt_price(new_price)}</b>.\nОбновлено товаров: {len(rows)}."
            
        await db.commit()

    with open(prices_file, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)

    await message.answer(f"{msg}\n\n<i>(Не забудьте запустить parser.py, чтобы обновить сайт)</i>")
    await state.clear()

# ============ ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ============

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Здесь можно заказать товары из нашего каталога.\nНажми кнопку ниже 👇",
        reply_markup=shop_keyboard(),
    )

@dp.message(F.web_app_data)
async def on_webapp_data(message: Message) -> None:
    try:
        data = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        await message.answer("⚠️ Не удалось прочитать данные заказа.")
        return

    items = data.get("items") or []
    cust = data.get("customer") or {}
    name = (cust.get("name") or "").strip()
    
    if not items or not name:
        await message.answer("⚠️ Заказ неполный.")
        return

    total = sum(float(i["price"]) * int(i["quantity"]) for i in items)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders(user_id,username,first_name,items_json,total,
               customer_name,phone,address,comment,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (message.from_user.id, message.from_user.username, message.from_user.first_name,
             json.dumps(items, ensure_ascii=False), total, name, "", "", cust.get("comment", ""), ts),
        )
        await db.commit()
        order_id = cur.lastrowid

    receipt = format_receipt(order_id, message.from_user, items, total, cust)
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, receipt, reply_markup=order_actions(order_id))
        except Exception:
            log.exception("Не смог отправить чек админу %s", admin_id)

    await message.answer(
        f"🌟 <b>Спасибо за заказ!</b>\n\n"
        f"Ваш заказ <b>№{order_id}</b> на сумму <b>{fmt_price(total)}</b> успешно принят.\n"
        f"Мы уже начали его собирать. Ожидайте доставку <b>во вторник вечером</b>.\n\n"
        f"Если у вас есть вопросы, наш менеджер скоро свяжется с вами. Хорошего дня! 🐻",
        reply_markup=shop_keyboard(),
    )

@dp.callback_query(F.data.startswith("o:"))
async def on_order_action(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS: return
    _, action, raw_id = call.data.split(":")
    status = "accepted" if action == "accept" else "rejected"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, int(raw_id)))
        await db.commit()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.edit_text(call.message.html_text + f"\n\n<b>Статус:</b> {status}", parse_mode="HTML")
    await call.answer("Готово")

async def main() -> None:
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())