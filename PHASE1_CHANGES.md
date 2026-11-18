# المرحلة 1 — ما تم تعديله

| # | المشكلة | الإصلاح | اختبار |
|---|---------|---------|--------|
| 1 | `mystore` يقارن `owner_id` (رقم DB) مع Telegram ID | استعلام الملكية بمعرّف المستخدم في DB داخل `get_owned_store` | test_stores, test_handlers, test_permissions |
| 2 | موافقة المالك على متجره تنزّل دوره إلى seller | الترقية فقط لمن دوره `customer` | test_stores |
| 3 | الموافقة المكررة تنشئ متجرين | `UPDATE ... WHERE status='pending'` ذري + فحص rowcount | test_stores (بما فيه تزامن) |
| 4 | المخزون غير آمن | قفل كتابة أولاً + `UPDATE ... WHERE stock>=qty` + `CHECK stock>=0` + rollback كامل | test_checkout |
| 5 | طلبات مكررة | المطالبة بالسلة داخل المعاملة + قفل لكل مستخدم | test_checkout |
| 6 | شراء من متجر موقوف/منتج مخفي/منتجك | فحوص داخل المعاملة وفي السلة والكتالوج | test_checkout, test_catalog |
| 7 | `scalar_one()` ينهار لمن لم يضغط /start | `UserMiddleware` ينشئ المستخدم ويحقن `db_user` | test_handlers |
| 8 | المحظور يستمر بالأزرار | الحظر في الـ middleware لكل رسالة/ضغطة | test_handlers |
| 9 | البحث: نص غير نصي + wildcards | تحقق + `escape_like` | test_catalog, test_handlers |
| 10 | حالات الطلب لا تطابق المواصفات | Enum رسمي بحروف كبيرة + تحويل القديم تلقائياً | test_models, test_db |
| 11 | أزرار تدّعي وظائف غير موجودة | نصوص الدعم/الشروط صادقة | test_handlers |
| 12 | Admin API يقبل `change-me` ومقارنة غير آمنة | `compare_digest` + تعطيل (503) مع مفتاح ضعيف | test_admin_api |
| 13 | `/stats` يحسب الملغي | استثناء CANCELLED/REJECTED (وكذلك لوحة المالك) | test_admin_api |
| 14 | مسار DB نسبي لمجلد التشغيل | مسار مطلق من جذر المشروع | test_settings |
| 15 | SQLite بلا foreign keys/WAL/timeout | PRAGMAs عند كل اتصال | test_db |
| 16 | لا معالج أخطاء عام، `answer()` ناقص، تعديل رسائل | `on_error` + `safe_edit` + `c.answer()` في كل handler | test_handlers |
| 17 | owner قابل للتغيير بالدور | المالك بمعرّف تيليجرام من السيرفر فقط + audit log | test_users, test_handlers |
| 18 | run.bat: بيئة خاطئة، pip كل مرة | يكشف `Desktop\venv`، pip عند الحاجة، `pause` عند الخطأ | — (يدوي) |

الاحتفاظ بكل الوظائف السابقة: جميع callback_data القديمة ما زالت موجودة (اختبار يتحقق أن كل زر في القائمة الرئيسية له handler).
