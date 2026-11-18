"""Public storefront keyboards (buyer side). All callback_data are <= 64 bytes."""
from bot.keyboards.main import btn, inline


def store_page_kb(page, store_id: int):
    rows = [[btn(f"🛒 {p.name[:32]} — {p.price} {p.currency}", f"product:{p.id}")] for p in page.items]
    nav = []
    if page.page > 0:
        nav.append(btn("⬅️ السابق", f"shop:{store_id}:{page.page - 1}"))
    if page.page < page.pages - 1:
        nav.append(btn("التالي ➡️", f"shop:{store_id}:{page.page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("⬅️ رجوع", "store")])
    return inline(rows)


def product_page_kb(product, image_count: int):
    rows = [[btn("🛒 أضف للسلة", f"add:{product.id}"), btn("❤️ المفضلة", f"fav:{product.id}")]]
    if image_count > 1:
        rows.append([btn(f"🖼 كل الصور ({image_count})", f"pimg:{product.id}")])
    rows.append([btn("🏪 زيارة المتجر", f"shop:{product.store_id}:0")])
    rows.append([btn("⬅️ رجوع", "store")])
    return inline(rows)
