from html import escape

def fmt_price(amount: float) -> str:
    s = f"{amount:.2f}".replace(".00", "")
    return f"{s} Br"

def format_receipt(order_id: int, user, items: list[dict], total: float, customer: dict) -> str:
    lines = [f"<b>🛒 Заказ №{order_id}</b>", ""]
    
    username = f"@{user.username}" if user.username else "нет юзернейма"
    lines.append(f"<b>Клиент:</b> {escape(customer.get('name', ''))}")
    lines.append(f"<b>Telegram:</b> {escape(username)}")
    
    d_type = customer.get("delivery_type", "")
    if d_type == "общага":
        lines.append(f"<b>Доставка:</b> Общага, комн. {escape(customer.get('room', ''))}")
    elif d_type == "шарага":
        lines.append(f"<b>Доставка:</b> Шарага, место: {escape(customer.get('place', ''))}")
    elif d_type == "другое":
        lines.append(f"<b>Доставка:</b> {escape(customer.get('other_text', ''))}")
    
    if customer.get("comment"):
        lines.append(f"<b>Комментарий:</b> {escape(customer['comment'])}")
    
    lines += ["", "<b>Состав:</b>"]
    for i in items:
        subtotal = float(i["price"]) * int(i["quantity"])
        lines.append(f"• {escape(i['name'])} × {i['quantity']} — {fmt_price(subtotal)}")
    
    lines += ["", f"<b>Итого: {fmt_price(total)}</b>"]
    return "\n".join(lines)