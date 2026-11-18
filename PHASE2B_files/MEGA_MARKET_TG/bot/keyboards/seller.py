"""Inline keyboards of the seller panel. All callback_data are <= 64 bytes."""
from bot.keyboards.main import btn, inline

STATUS_ICON = {"active": "✅", "hidden": "🙈", "draft": "📝"}


def store_panel_kb(store_id: int):
    return inline([
        [btn("📦 منتجاتي", f"sp:list:{store_id}:0"), btn("➕ إضافة منتج", f"sp:add:{store_id}")],
        [btn("⬅️ رجوع", "seller_menu")],
    ])


def products_list_kb(items, store_id: int, page: int, pages: int):
    rows = [[btn(f"{STATUS_ICON.get(p.status, '•')} {p.name[:30]} — {p.price}", f"sp:view:{p.id}")] for p in items]
    nav = []
    if page > 0:
        nav.append(btn("⬅️ السابق", f"sp:list:{store_id}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("التالي ➡️", f"sp:list:{store_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("➕ إضافة منتج", f"sp:add:{store_id}")])
    rows.append([btn("⬅️ رجوع", f"mystore:{store_id}")])
    return inline(rows)


def product_card_kb(product, has_images: bool):
    pid = product.id
    rows = [
        [btn("✏️ تعديل", f"sp:edit:{pid}"), btn("📦 المخزون", f"sp:stkmenu:{pid}")],
        [btn("🎁 الخصم", f"sp:disc:{pid}"),
         btn("🙈 إخفاء" if product.status == "active" else "👁 إظهار", f"sp:vis:{pid}")],
    ]
    if has_images:
        rows.append([btn("🖼 عرض الصور", f"sp:photos:{pid}")])
    rows.append([btn("🗑 حذف", f"sp:del:{pid}")])
    rows.append([btn("⬅️ المنتجات", f"sp:list:{product.store_id}:0")])
    return inline(rows)


def edit_menu_kb(pid: int):
    return inline([
        [btn("✏️ الاسم", f"sp:ef:{pid}:name"), btn("📝 الوصف", f"sp:ef:{pid}:description")],
        [btn("💰 السعر", f"sp:ef:{pid}:price"), btn("📂 التصنيف", f"sp:ecat:{pid}")],
        [btn("🖼 الصور", f"sp:eimg:{pid}")],
        [btn("⬅️ رجوع", f"sp:view:{pid}")],
    ])


def stock_kb(pid: int):
    return inline([
        [btn(f"+{n}", f"sp:stk:{pid}:+{n}") for n in (1, 5, 10)],
        [btn(f"-{n}", f"sp:stk:{pid}:-{n}") for n in (1, 5, 10)],
        [btn("✍️ تحديد كمية دقيقة", f"sp:ef:{pid}:stock")],
        [btn("⬅️ رجوع", f"sp:view:{pid}")],
    ])


def discount_kb(pid: int, has_discount: bool):
    rows = [[btn(f"{n}%", f"sp:discset:{pid}:{n}") for n in (10, 20, 30, 50)],
            [btn("✍️ نسبة أخرى", f"sp:ef:{pid}:discount")]]
    if has_discount:
        rows.append([btn("❌ إلغاء الخصم", f"sp:discrm:{pid}")])
    rows.append([btn("⬅️ رجوع", f"sp:view:{pid}")])
    return inline(rows)


def categories_kb(categories, make_cb, none_cb: str, back_cb: str):
    rows = [[btn(f"📂 {c.name}", make_cb(c.id))] for c in categories]
    rows.append([btn("بدون تصنيف", none_cb)])
    rows.append([btn("⬅️ رجوع" if back_cb != "spx:cancel" else "❌ إلغاء", back_cb)])
    return inline(rows)


def cancel_kb():
    return inline([[btn("❌ إلغاء", "spx:cancel")]])


def add_photos_kb():
    return inline([[btn("✅ تم (متابعة)", "spa:photodone")], [btn("❌ إلغاء", "spx:cancel")]])


def edit_photos_kb():
    return inline([[btn("✅ تم", "spe:imgdone")], [btn("🗑 حذف كل الصور", "spe:imgclear")],
                   [btn("❌ إلغاء", "spx:cancel")]])


def confirm_add_kb():
    return inline([[btn("✅ نشر المنتج", "spa:save")], [btn("❌ إلغاء", "spx:cancel")]])


def delete_confirm_kb(pid: int):
    return inline([[btn("✅ نعم، احذف", f"sp:delok:{pid}")], [btn("⬅️ لا، رجوع", f"sp:view:{pid}")]])
