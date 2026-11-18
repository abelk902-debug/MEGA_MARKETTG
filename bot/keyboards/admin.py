"""Owner-side category management keyboards. All callback_data are <= 64 bytes."""
from bot.keyboards.main import btn, inline


def categories_list_kb(categories):
    rows = [[btn(f"{'✅' if c.is_active else '🙈'} {c.name[:30]}", f"admin:cat:{c.id}")] for c in categories]
    rows.append([btn("➕ إضافة تصنيف", "admin:catadd")])
    rows.append([btn("⬅️ لوحة المالك", "owner")])
    return inline(rows)


def category_actions_kb(category):
    return inline([
        [btn("✏️ إعادة تسمية", f"admin:catren:{category.id}")],
        [btn("🙈 إخفاء" if category.is_active else "👁 إظهار", f"admin:cattog:{category.id}")],
        [btn("🗑 حذف", f"admin:catdel:{category.id}")],
        [btn("⬅️ التصنيفات", "admin:cats")],
    ])


def category_delete_kb(category_id: int):
    return inline([
        [btn("✅ نعم، احذف", f"admin:catdelok:{category_id}")],
        [btn("⬅️ لا، رجوع", f"admin:cat:{category_id}")],
    ])


def category_cancel_kb():
    return inline([[btn("❌ إلغاء", "admin:cats")]])
