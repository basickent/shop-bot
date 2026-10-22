from html import escape

def format_receipt(order_id: int, user, items: list[dict], total: float, customer: dict) -> str:
    lines = [f"<b>🛒 Заказ №{order_id}</b>", ""]
    lines.append(f"<b>Клиент:</b> {escape(customer.get('name', ''))}")
    lines.append(f"<b>Телефон:</b> {escape(customer.get('phone', ''))}")
    if customer.get("address"):
        lines.append(f"<b>Адрес:</b> {escape(customer['address'])}")
    if customer.get("comment"):
        lines.append(f"<b>Комментарий:</b> {escape(customer['comment'])}")
    lines += ["", "<b>Состав:</b>"]
    for i in items:
        subtotal = float(i["price"]) * int(i["quantity"])
        lines.append(f"• {escape(i['name'])} × {i['quantity']} — {subtotal:.0f} ₽")
    lines += ["", f"<b>Итого: {total:.0f} ₽</b>", ""]
    username = f"@{user.username}" if user.username else user.first_name
    lines.append(f"<i>от {escape(username)} (id {user.id})</i>")
    return "\n".join(lines)