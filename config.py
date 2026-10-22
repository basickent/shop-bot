import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан в .env")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
if not ADMIN_IDS:
    raise SystemExit("ADMIN_IDS не задан (через запятую)")

WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
if not WEBAPP_URL.startswith("https://"):
    raise SystemExit("WEBAPP_URL должен начинаться с https:// (требование Telegram)")

DB_PATH = Path(os.getenv("DB_PATH", "market.db"))
CATALOG_JSON_PATH = Path(os.getenv("CATALOG_JSON_PATH", "webapp/catalog.json"))
SHOP_BASE_URL = os.getenv("SHOP_BASE_URL", "https://5kuodgqfnjel.moysklad.shop").rstrip("/")