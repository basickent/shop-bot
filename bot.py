import asyncio
import json
import logging
import os
import sys
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

async def background_update(push_to_git=True):
    try:
        log.info("⏳ Запуск фонового обновления каталога...")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "parser.py", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        if push_to_git:
            try:
                await asyncio.create_subprocess_exec("git", "add", ".", stdout=asyncio.subprocess.PIPE)
                await asyncio.create_subprocess_exec("git", "commit", "-m", "auto: update", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await asyncio.create_subprocess_exec("git", "push", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                log.info("✅ Отправлено на GitHub")
            except Exception as git_err:
                log.warning(f"⚠️ Git не сработал: {git_err}")
        log.info("✅ Фоновое обновление завершено")
    except Exception as e:
        log.error(f"❌ Ошибка фонового обновления: {e}")

async def auto_catalog_updater():
    while True:
        await asyncio.sleep(7200)
        await background_update(push_to_git=True)

# ============ АДМИН: ЦЕНЫ ============

class PriceState(StatesGroup):
    waiting_for_price = State()

async def get_root_categories_kb() -> InlineKeyboardMarkup:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM categories WHERE parent_id IS NULL OR parent_id = '' ORDER BY name")
        rows = await cur.fetchall()
    kb = [[InlineKeyboardButton(text=row[1], callback_data=f"admin_view:{row[0]}")] for row in rows]
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("Выберите категорию:", reply_markup=await get_root_categories_kb())

# Универсальный просмотр категории (любой уровень вложенности)
@dp.callback_query(F.data.startswith("admin_view:"))
async def admin_view_cat(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    cat_id = call.data.split(":", 1)[1]
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))
        row = await cur.fetchone()
        if not row: return
        cat_name = row[0]

        # Ищем дочерние категории (любой уровень)
        cur = await db.execute("SELECT id, name FROM categories WHERE parent_id = ? ORDER BY name", (cat_id,))
        subs = await cur.fetchall()

    kb = []
    if subs:
        # Если есть подкатегории — показываем их списком
        for s in subs:
            kb.append([InlineKeyboardButton(text=f"📁 {s[1]}", callback_data=f"admin_view:{s[0]}")])
            
    # Кнопка "Выбрать конкретный товар" (показывает товары, привязанные именно к этой папке)
    kb.append([InlineKeyboardButton(text="📦 Выбрать конкретный товар", callback_data=f"admin_items:{cat_id}")])
    # Кнопка "Изменить цену на всё здесь"
    kb.append([InlineKeyboardButton(text=f"📂 Изменить цену на ВСЁ в {cat_name}", callback_data=f"admin_set_price:cat:{cat_id}")])
    
    # Кнопка назад (если это корень — в главное меню, иначе - сложно, просто в главное)
    kb.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data="admin_back")])
    
    await call.message.edit_text(f"<b>{escape(cat_name)}</b>\nВыберите действие:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
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
        await call.answer("В этой папке нет прямых товаров. Выберите подкатегорию.", show_alert=True)
        return
    
    kb = [[InlineKeyboardButton(text=f"{r[1]} — {fmt_price(r[2])}", callback_data=f"admin_item:{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_view:{cat_id}")])
    await call.message.edit_text("Выберите товар:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data.startswith("admin_set_price:"))
async def admin_set_cat_price(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    mode, target_id = parts[1], parts[2]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM categories WHERE id = ?", (target_id,))
        row = await cur.fetchone()
    await state.update_data(target_id=target_id, target_name=row[0], mode=mode)
    await state.set_state(PriceState.waiting_for_price)
    await call.message.edit_text(f"Введите новую цену для всех товаров в <b>{escape(row[0])}</b> (в BYN):")
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
        await message.answer("⚠️ Введите число (например: 15.50)")
        return

    data = await state.get_data()
    mode, target_id, target_name = data['mode'], data['target_id'], data['target_name']
    
    prices_file = "prices.json"
    prices = {"items": {}, "categories": {}}
    if os.path.exists(prices_file):
        with open(prices_file, "r", encoding="utf-8") as f:
            try: 
                data = json.load(f)
                prices["items"] = data.get("items", {})
                prices["categories"] = data.get("categories", {})
            except: pass

    async with aiosqlite.connect(DB_PATH) as db:
        if mode == 'item':
            prices["items"][target_id] = new_price
            await db.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, target_id))
            msg = f"✅ Цена на <b>{escape(target_name)}</b> изменена на <b>{fmt_price(new_price)}</b>."
        
        elif mode == 'cat':
            prices["categories"][target_id] = new_price
            
            # Ищем все вложенные категории рекурсивно для обновления базы
            all_cats_to_update = [target_id]
            def collect_children(cid):
                cur = db.execute("SELECT id FROM categories WHERE parent_id = ?", (cid,))
                # Тут нельзя await внутри обычной функции, поэтому сделаем иначе
            # Просто обновим все товары, у которых category_id принадлежит этой ветке.
            # В SQLite это можно сделать через WITH RECURSIVE (если версия 3.8.3+)
            query = """
                WITH RECURSIVE subcats(id) AS (
                    SELECT id FROM categories WHERE id = ?
                    UNION ALL
                    SELECT c.id FROM categories c JOIN subcats s ON c.parent_id = s.id
                )
                UPDATE products SET price = ? WHERE category_id IN (SELECT id FROM subcats)
            """
            await db.execute(query, (target_id, new_price))
            msg = f"✅ Цена на <b>{escape(target_name)}</b> и все её подкатегории изменена на <b>{fmt_price(new_price)}</b>."
        await db.commit()

    with open(prices_file, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)

    await message.answer(f"{msg}\n\n⏳ <i>Обновляю сайт...</i>")
    asyncio.create_task(background_update(push_to_git=True))
    await state.clear()

# ============ КЛИЕНТСКИЕ КОМАНДЫ ============

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer("Привет! Нажми кнопку ниже 👇", reply_markup=shop_keyboard())

@dp.message(F.web_app_data)
async def on_webapp_data(message: Message) -> None:
    try: data = json.loads(message.web_app_data.data)
    except: return
    items = data.get("items") or []
    cust = data.get("customer") or {}
    name = (cust.get("name") or "").strip()
    if not items or not name: return

    total = sum(float(i["price"]) * int(i["quantity"]) for i in items)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders(user_id,username,first_name,items_json,total,customer_name,phone,address,comment,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (message.from_user.id, message.from_user.username, message.from_user.first_name,
             json.dumps(items, ensure_ascii=False), total, name, "", "", cust.get("comment", ""), ts),
        )
        await db.commit()
        order_id = cur.lastrowid

    receipt = format_receipt(order_id, message.from_user, items, total, cust)
    for admin_id in ADMIN_IDS:
        try: await message.bot.send_message(admin_id, receipt, reply_markup=order_actions(order_id))
        except Exception: log.exception("Ошибка отправки админу %s", admin_id)

       # Красивый ответ клиенту
    await message.answer(
        f"🌟 <b>Спасибо за заказ!</b>\n\n"
        f"Ваш заказ <b>№{order_id}</b> на сумму <b>{fmt_price(total)}</b> успешно принят.\n\n"
        f"⏳ Ожидайте свой заказ <b>после вторника</b>.\n\n"
        f"Если у вас есть вопросы, обращайтесь к нашему менеджеру: <b>@il0pka</b>\n"
        f"Хорошего дня! 🐻",
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
    asyncio.create_task(auto_catalog_updater())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())