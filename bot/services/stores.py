from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update

from bot.services.audit import log_action
from bot.services.errors import StoreError
from database.models import Product, Store, StoreApplication, User

MAX_NAME = 120
MAX_DESC = 1000


@dataclass(frozen=True)
class ApplicationDecision:
    application_id: int
    store_id: int | None
    store_name: str
    applicant_telegram_id: int


async def submit_application(session_factory, user_id: int, name: str, description: str | None) -> StoreApplication:
    name = (name or "").strip()
    description = (description or "").strip() or None
    if not 2 <= len(name) <= MAX_NAME:
        raise StoreError("bad_name", f"اسم المتجر يجب أن يكون بين 2 و{MAX_NAME} حرفاً")
    if description and len(description) > MAX_DESC:
        raise StoreError("bad_desc", f"الوصف طويل جداً (الحد {MAX_DESC} حرف)")
    async with session_factory() as s:
        pending = await s.scalar(select(func.count(StoreApplication.id)).where(
            StoreApplication.user_id == user_id, StoreApplication.status == "pending"))
        if pending:
            raise StoreError("pending_exists", "لديك طلب متجر قيد المراجعة بالفعل")
        app = StoreApplication(user_id=user_id, store_name=name, description=description)
        s.add(app)
        await s.commit()
        return app


async def list_pending_applications(session):
    return (await session.execute(
        select(StoreApplication).where(StoreApplication.status == "pending").order_by(StoreApplication.created_at)
    )).scalars().all()


async def get_application(session, application_id: int):
    return await session.get(StoreApplication, application_id)


async def _claim(s, application_id: int, new_status: str, reviewer_tg_id: int, note: str | None):
    """Atomically move pending -> approved/rejected. Only one caller can win."""
    res = await s.execute(
        update(StoreApplication)
        .where(StoreApplication.id == application_id, StoreApplication.status == "pending")
        .values(status=new_status, reviewed_by=reviewer_tg_id, review_note=note)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise StoreError("not_pending", "الطلب غير موجود أو تمت معالجته مسبقاً")
    return await s.get(StoreApplication, application_id)


async def approve_application(session_factory, application_id: int, reviewer_tg_id: int) -> ApplicationDecision:
    async with session_factory() as s:
        app = await _claim(s, application_id, "approved", reviewer_tg_id, None)
        applicant = await s.get(User, app.user_id)
        store = Store(owner_id=applicant.id, name=app.store_name, description=app.description, status="approved")
        s.add(store)
        await s.flush()
        # promote ONLY plain customers: never demote owner/admin/moderator
        await s.execute(update(User).where(User.id == applicant.id, User.role == "customer")
                        .values(role="seller").execution_options(synchronize_session=False))
        log_action(s, reviewer_tg_id, "store_application.approve", "store_application", application_id,
                   f"store_id={store.id}")
        decision = ApplicationDecision(application_id, store.id, store.name, applicant.telegram_id)
        await s.commit()
        return decision


async def reject_application(session_factory, application_id: int, reviewer_tg_id: int,
                             note: str | None = None) -> ApplicationDecision:
    async with session_factory() as s:
        app = await _claim(s, application_id, "rejected", reviewer_tg_id, note)
        applicant = await s.get(User, app.user_id)
        log_action(s, reviewer_tg_id, "store_application.reject", "store_application", application_id, note)
        decision = ApplicationDecision(application_id, None, app.store_name, applicant.telegram_id)
        await s.commit()
        return decision


async def list_user_stores(session, user_id: int):
    return (await session.execute(select(Store).where(Store.owner_id == user_id).order_by(Store.id))).scalars().all()


async def get_owned_store(session, store_id: int, user_id: int):
    """Store only if it belongs to this DB user id (fixes the old Telegram-ID comparison bug)."""
    return (await session.execute(
        select(Store).where(Store.id == store_id, Store.owner_id == user_id))).scalar_one_or_none()


async def count_store_products(session, store_id: int) -> int:
    return await session.scalar(select(func.count(Product.id)).where(Product.store_id == store_id)) or 0
