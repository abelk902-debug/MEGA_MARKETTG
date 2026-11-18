from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def inline(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu(is_seller=False, is_owner=False):
    rows = [
        [btn('🛍️ المتجر', 'store'), btn('📂 كل التصنيفات', 'categories')],
        [btn('🔍 بحث متقدم', 'search'), btn('🔥 الأكثر مبيعاً', 'best')],
        [btn('🆕 الأحدث', 'newest'), btn('💎 المميزة', 'featured')],
        [btn('🎁 التخفيضات', 'discounts'), btn('🛒 السلة', 'cart')],
        [btn('❤️ المفضلة', 'favorites'), btn('📦 طلباتي', 'orders')],
        [btn('🏪 متجري', 'seller_menu' if is_seller else 'apply_store'), btn('👤 حسابي', 'account')],
        [btn('💬 الدعم', 'support'), btn('📜 الشروط/الخصوصية', 'terms')],
    ]
    if is_owner:
        rows.append([btn('👑 لوحة المالك', 'owner')])
    return inline(rows)


def back_menu(cb='home'):
    return inline([[btn('⬅️ رجوع', cb)]])
