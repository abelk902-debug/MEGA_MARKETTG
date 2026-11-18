# MEGA MARKET TG — سوق متعدد البائعين على تيليجرام

> الحالة: **المرحلة 1 (إصلاح الأساس) مكتملة برمجياً**. الاختبارات مكتوبة وتحتاج تشغيلها على جهازك (انظر أدناه).

## التشغيل على Windows 11 (CMD)

```bat
cd /d C:\Users\Admin\Desktop\MEGA_MARKET_TG
run.bat
```

`run.bat` يستخدم البيئة `C:\Users\Admin\Desktop\venv` تلقائياً إن وُجدت (وإلا ينشئ `.venv` داخل المشروع)، ويثبّت المكتبات عند الحاجة فقط.

أول مرة: يُنشأ `.env` من `.env.example` ويتوقف. ضع `BOT_TOKEN` ثم شغّل مرة ثانية.

## تشغيل الاختبارات

```bat
cd /d C:\Users\Admin\Desktop\MEGA_MARKET_TG
run_tests.bat
```
أو يدوياً:
```bat
call C:\Users\Admin\Desktop\venv\Scripts\activate.bat
python -m pip install -r requirements-dev.txt
python -m pytest -v
```
النتيجة تُحفظ في `test_results.txt`. أرسلها لي كاملة.

## لوحة/واجهة Admin API

1. ولّد مفتاحاً قوياً وضعه في `.env` كقيمة `ADMIN_API_KEY` (16 حرفاً فأكثر). القيمة `change-me` **تعطّل** الـ API.
2. شغّل `run_admin.bat` ثم أرسل الترويسة `X-API-Key` مع كل طلب إلى `/stats` و`/users`.

## قاعدة البيانات

- تُنشأ تلقائياً عند أول تشغيل في `database\mega_market.db` (المسار ثابت بالنسبة لمجلد المشروع مهما كان مكان التشغيل).
- قاعدة قديمة: تعمل كما هي، وتُحوَّل حالات الطلبات القديمة (`awaiting_seller`…) إلى الصيغة الرسمية تلقائياً.
- ملفات `*.db-wal` و`*.db-shm` طبيعية (وضع WAL).

## بنية المشروع

```
main.py                 تشغيل البوت وربط الـ routers والـ middleware
config/settings.py      الإعدادات (.env)
database/               الاتصال + النماذج
bot/middlewares/user.py تسجيل المستخدم + حظر المحظورين + db_user
bot/services/           منطق العمل (orders, cart, stores, catalog, users, permissions, audit)
bot/handlers/           start, menu, catalog, cart, seller, admin, errors
admin_panel/app.py      Admin API (FastAPI)
tests/                  اختبارات pytest
```

## ملاحظات

- الدفع الحالي: عند الاستلام فقط. لا تُخزَّن بيانات بطاقات إطلاقاً.
- زر "الدعم" يوضح أن نظام التذاكر غير مفعّل بعد (المرحلة 7).
