import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests
import urllib3

from config import DB_PATH, CATALOG_JSON_PATH
from database import init_db

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://5kuodgqfnjel.moysklad.shop/desktop-api/public/5kuodgqfnjel"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT = 30

def load_custom_prices() -> dict:
    if os.path.exists("prices.json"):
        try:
            with open("prices.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if "items" not in data and "categories" not in data:
                    return {"items": data, "categories": {}}
                return data
        except Exception as e:
            print(f"[parser] Ошибка чтения prices.json: {e}", file=sys.stderr)
    return {"items": {}, "categories": {}}

CUSTOM_PRICES = load_custom_prices()

def _get(path: str, **params) -> dict | list:
    url = f"{BASE}/{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, verify=False, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2: raise
            print(f"[retry {attempt+1}] {url}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return {}

def fetch_categories() -> list[dict]:
    data = _get("categories.json")
    return data if isinstance(data, list) else data.get("categories") or []

def fetch_all_products() -> list[dict]:
    items, offset = [], 0
    while True:
        data = _get("products.json", offset=offset, limit=500)
        batch = data.get("products") or []
        if not batch: break
        items.extend(batch)
        total = data.get("size") or 0
        offset += len(batch)
        print(f"[parser] fetched {offset}/{total}")
        if offset >= total: break
        time.sleep(0.3)
    return items

def _norm_product(p: dict, cat_map: dict) -> dict:
    pid = str(p.get("id") or "")
    cat_id = str(p.get("categoryId") or "") or None
    base_price = float(p.get("price") or 0)
    
    custom_items = CUSTOM_PRICES.get("items", {})
    custom_cats = CUSTOM_PRICES.get("categories", {})

    final_price = base_price

    # 1. Проверяем цену конкретного товара
    if pid in custom_items:
        final_price = float(custom_items[pid])
    else:
        # 2. Ищем цену по всей цепочке родителей (подкатегория -> категория -> и т.д.)
        current_cat = cat_id
        while current_cat:
            if current_cat in custom_cats:
                final_price = float(custom_cats[current_cat])
                break # Нашли цену на самом высоком доступном уровне
            # Идем к родителю
            current_cat = cat_map.get(current_cat, {}).get("parent_id")

    return {
        "id": pid,
        "name": (p.get("name") or "").strip(),
        "category_id": cat_id,
        "price": final_price,
        "description": (p.get("description") or "").strip(),
        "available": 1 if p.get("available") else 0,
        "stock": float(p.get("stock") or 0),
    }

def _norm_category(c: dict) -> dict:
    return {
        "id": str(c.get("id") or ""),
        "name": (c.get("name") or "").strip(),
        "parent_id": str(c.get("parentId") or "") or None,
        "path_name": c.get("pathName") or "",
    }

def save_to_db(categories: list[dict], products: list[dict]) -> None:
    init_db()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM categories")
        conn.executemany(
            "INSERT INTO categories(id,name,parent_id,sort_order,updated_at) VALUES(?,?,?,?,?)",
            [(c["id"], c["name"], c["parent_id"], 0, ts) for c in categories],
        )
        conn.executemany(
            "INSERT INTO products(id,name,category_id,price,image_url,description,available,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            [(p["id"], p["name"], p["category_id"], p["price"], "", 
              p["description"], p["available"], ts) for p in products],
        )
        conn.commit()

def export_catalog_json(categories: list[dict], products: list[dict]) -> None:
    CATALOG_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": categories,
        "products": [p for p in products if p["available"]],
    }
    with open(CATALOG_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[parser] catalog.json: {len(payload['products'])} товаров")

def run() -> None:
    print("[parser] fetch categories…")
    raw_categories = fetch_categories()
    
    filtered_raw_cats = [c for c in raw_categories if "акци" not in (c.get("name") or "").lower()]
    categories = [_norm_category(c) for c in filtered_raw_cats]
    valid_cat_ids = {c["id"] for c in categories}
    
    categories.append({"id": "fallback_other", "name": "Разное", "parent_id": None, "path_name": "Разное"})
    cat_map = {c["id"]: c for c in categories}

    print("[parser] fetch products…")
    raw_products = fetch_all_products()
    
    products = []
    for p in raw_products:
        norm_p = _norm_product(p, cat_map)
        if norm_p["category_id"] not in valid_cat_ids:
            norm_p["category_id"] = "fallback_other"
        products.append(norm_p)
        
    print(f"[parser] products: {len(products)}")

    save_to_db(categories, products)
    export_catalog_json(categories, products)
    print(f"[parser] OK")

if __name__ == "__main__":
    run()