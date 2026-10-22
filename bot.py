import asyncio
import json
import logging
from datetime import datetime, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from config import ADMIN_IDS, BOT_TOKEN, DB_PATH, WEBAPP_URL
from database import init_db
from utils.receipt import format_receipt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

dp = Dispatcher()

# ---------- клавиатуры ----------

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

# ---------- /start ----------

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Здесь можно заказать товары из нашего каталога.\n"
        "Нажми кнопку ниже 👇",
        reply_markup=shop_keyboard(),
    )

# ---------- приём заказа из Web App ----------

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
    phone = (cust.get("phone") or "").strip()
    address = (cust.get("address") or "").strip()
    comment = (cust.get("comment") or "").strip()

    if not items or not name or not phone:
        await message.answer("⚠️ Заказ неполный: нет товаров, имени или телефона.")
        return

    total = sum(float(i["price"]) * int(i["quantity"]) for i in items)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders(user_id,username,first_name,items_json,total,
               customer_name,phone,address,comment,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
                json.dumps(items, ensure_ascii=False),
                total,
                name, phone, address, comment, ts,
            ),
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
        f"✅ <b>Заказ №{order_id}</b> принят!\n"
        f"Сумма: <b>{total:.0f} ₽</b>\n"
        f"Свяжемся с вами по номеру {phone}.",
        reply_markup=shop_keyboard(),
    )

# ---------- колбэки админа ----------

@dp.callback_query(F.data.startswith("o:"))
async def on_order_action(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Не для тебя", show_alert=False)
        return
    try:
        _, action, raw_id = call.data.split(":")
        order_id = int(raw_id)
    except ValueError:
        await call.answer("Битый callback")
        return

    status = "accepted" if action == "accept" else "rejected"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        await db.commit()

    await call.message.edit_reply_markup(reply_markup=None)
    suffix = f"\n\n<b>Статус:</b> {status}"
    await call.message.edit_text(call.message.html_text + suffix, parse_mode="HTML")
    await call.answer("Готово")

# ---------- main ----------

async def main() -> None:
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log.info("Bot started, admins=%s, webapp=%s", ADMIN_IDS, WEBAPP_URL)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())