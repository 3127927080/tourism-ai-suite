from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tourism_service.common.auth import (
    Principal,
    create_access_token,
    current_principal,
    require_roles,
)
from tourism_service.database import Base, SessionLocal, engine, get_db
from tourism_service.models import (
    IdempotencyRecord,
    Order,
    Refund,
    Ticket,
    TravelProduct,
    TripInfo,
    User,
)
from tourism_service.schemas import (
    DemoLoginRequest,
    RefundCreate,
    TicketCreate,
    TravelerUpdate,
    money,
)
from tourism_service.seed import seed_demo_data

ORDER_STATUS_LABELS = {
    "pending_payment": "待支付",
    "paid": "已支付",
    "upcoming": "待出行",
    "in_progress": "出行中",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunding": "退款中",
    "refunded": "已退款",
    "ticketed": "已出票",
    "ticket_refunded": "已退票",
    "checked_in": "已入住",
    "checked_out": "已离店",
}

# 允许取消的订单状态
CANCELLABLE_STATUSES = {"pending_payment", "paid", "upcoming"}


def cancellation_policy(order: Order) -> tuple[bool, str]:
    if order.status in CANCELLABLE_STATUSES:
        return True, ""
    if order.status in {"in_progress", "checked_in"}:
        return False, "订单已开始出行，不能直接取消；请申请退款或联系人工。"
    if order.status == "cancelled":
        return False, "订单已经取消。"
    if order.status in {"completed", "checked_out"}:
        return False, "订单已完成，不能取消。"
    return False, "订单当前状态不能直接取消。"


def _iso(value):
    return value.isoformat() if value is not None else None


def product_payload(product: TravelProduct) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "type": product.type,
        "city": product.city,
        "price": money(product.price),
        "stock": product.stock,
        "status": product.status,
        "description": product.description,
        "attributes": product.attributes,
    }


def order_payload(order: Order) -> dict:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "status": order.status,
        "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
        "total_amount": money(order.total_amount),
        "created_at": order.created_at.isoformat(),
        "travel_start_date": _iso(order.travel_start_date),
        "travel_end_date": _iso(order.travel_end_date),
        "items": [
            {
                "product_id": item.product_id,
                "product_name": item.product_name,
                "product_type": item.product_type,
                "quantity": item.quantity,
                "unit_price": money(item.unit_price),
            }
            for item in order.items
        ],
    }


def trip_payload(trip: TripInfo) -> dict:
    return {
        "order_id": trip.order_id,
        "travel_date": _iso(trip.travel_date),
        "checkin_date": _iso(trip.checkin_date),
        "checkout_date": _iso(trip.checkout_date),
        "departure_time": trip.departure_time,
        "arrival_time": trip.arrival_time,
        "transport_no": trip.transport_no,
        "traveler_info": trip.traveler_info,
        "meeting_point": trip.meeting_point,
        "status": trip.status,
        "events": trip.events,
    }


def refund_payload(refund: Refund) -> dict:
    return {
        "id": refund.id,
        "order_id": refund.order_id,
        "kind": refund.kind,
        "reason": refund.reason,
        "status": refund.status,
        "amount": money(refund.amount) if refund.amount is not None else None,
        "created_at": refund.created_at.isoformat(),
    }


def ticket_payload(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "order_id": ticket.order_id,
        "ticket_type": ticket.ticket_type,
        "issue_description": ticket.issue_description,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat(),
    }


def tool_result(
    success: bool,
    code: str,
    message: str,
    data: dict | list | None = None,
    retryable: bool = False,
):
    return {
        "success": success,
        "code": code,
        "message": message,
        "data": data,
        "retryable": retryable,
    }


def cached_operation(
    db: Session,
    key: str | None,
    principal: Principal,
    operation: str,
) -> dict | None:
    if not key:
        return None
    record = db.get(IdempotencyRecord, key)
    if not record:
        return None
    if record.user_id != principal.user_id or record.operation != operation:
        raise HTTPException(
            status_code=409, detail="Idempotency key was used for another operation"
        )
    return record.response


def store_operation(
    db: Session,
    key: str | None,
    principal: Principal,
    operation: str,
    response: dict,
) -> dict:
    if key:
        db.add(
            IdempotencyRecord(
                key=key,
                user_id=principal.user_id,
                operation=operation,
                response=response,
            )
        )
    db.commit()
    return response


def owned_order(db: Session, order_id: str, principal: Principal) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if principal.role == "customer" and order.user_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Order does not belong to current user")
    return order


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo_data(db)
    yield


app = FastAPI(title="Tourism Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "tourism-service"}


@app.post("/api/v1/auth/demo-token")
def demo_token(request: DemoLoginRequest, db: Annotated[Session, Depends(get_db)]):
    user = db.get(User, request.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Demo user not found")
    expected_role = (
        "admin"
        if user.id.startswith("admin_")
        else "agent"
        if user.id.startswith("agent_")
        else "customer"
    )
    if request.role != expected_role:
        raise HTTPException(status_code=403, detail="Role does not match demo account")
    principal = Principal(user_id=user.id, role=request.role)
    return {
        "access_token": create_access_token(principal),
        "token_type": "bearer",
        "principal": principal.model_dump(),
        "display_name": user.name,
    }


@app.get("/api/v1/demo/users")
def demo_users(db: Annotated[Session, Depends(get_db)]):
    users = db.scalars(select(User)).all()
    return [
        {"id": user.id, "name": user.name, "membership": user.membership}
        for user in users
        if not user.id.startswith(("agent_", "admin_"))
    ]


@app.get("/api/v1/products")
def search_travel_products(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[Principal, Depends(current_principal)],
    query: str = Query(default="", max_length=100),
    type: str | None = Query(default=None, description="hotel|scenic|ticket|transfer"),
):
    statement = select(TravelProduct).where(TravelProduct.status == "active")
    if type:
        statement = statement.where(TravelProduct.type == type)
    if query:
        like = f"%{query}%"
        statement = statement.where(
            or_(
                TravelProduct.name.ilike(like),
                TravelProduct.description.ilike(like),
                TravelProduct.city.ilike(like),
            )
        )
    products = db.scalars(statement.order_by(TravelProduct.id).limit(20)).all()
    return tool_result(
        True, "OK", f"找到 {len(products)} 个旅游产品", [product_payload(p) for p in products]
    )


@app.get("/api/v1/products/{product_id}")
def get_travel_product(
    product_id: str,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[Principal, Depends(current_principal)],
):
    product = db.get(TravelProduct, product_id)
    if not product:
        return tool_result(False, "PRODUCT_NOT_FOUND", "产品不存在")
    return tool_result(True, "OK", "产品查询成功", product_payload(product))


@app.get("/api/v1/products/{product_id}/availability")
def get_product_availability(
    product_id: str,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[Principal, Depends(current_principal)],
):
    product = db.get(TravelProduct, product_id)
    if not product:
        return tool_result(False, "PRODUCT_NOT_FOUND", "产品不存在")
    available = product.stock > 0 and product.status == "active"
    attributes = product.attributes or {}
    return tool_result(
        True,
        "OK",
        "余量查询成功",
        {
            "product_id": product.id,
            "available_count": product.stock,
            "availability_status": "available" if available else "sold_out",
            "date_range": attributes.get("date_range"),
        },
    )


@app.get("/api/v1/orders")
def list_orders(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
):
    statement = select(Order)
    if principal.role == "customer":
        statement = statement.where(Order.user_id == principal.user_id)
    orders = db.scalars(statement.order_by(Order.created_at.desc())).unique().all()
    return tool_result(True, "OK", f"找到 {len(orders)} 个订单", [order_payload(o) for o in orders])


@app.get("/api/v1/orders/{order_id}")
def get_order(
    order_id: str,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
):
    try:
        order = owned_order(db, order_id, principal)
    except HTTPException as exc:
        code = "ORDER_FORBIDDEN" if exc.status_code == 403 else "ORDER_NOT_FOUND"
        return tool_result(False, code, str(exc.detail))
    return tool_result(True, "OK", "订单查询成功", order_payload(order))


@app.get("/api/v1/orders/{order_id}/trip")
def get_trip_info(
    order_id: str,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
):
    try:
        order = owned_order(db, order_id, principal)
    except HTTPException as exc:
        return tool_result(False, "ORDER_NOT_FOUND", str(exc.detail))
    if not order.trip:
        return tool_result(False, "TRIP_NOT_AVAILABLE", "该订单暂无出行信息")
    return tool_result(True, "OK", "出行信息查询成功", trip_payload(order.trip))


@app.post("/api/v1/orders/{order_id}/cancel")
def cancel_order(
    order_id: str,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if cached := cached_operation(db, idempotency_key, principal, "cancel_booking"):
        return cached
    try:
        order = owned_order(db, order_id, principal)
    except HTTPException as exc:
        return tool_result(False, "ORDER_NOT_FOUND", str(exc.detail))
    if order.status == "cancelled":
        result = tool_result(True, "ALREADY_CANCELLED", "订单已经取消", order_payload(order))
        return store_operation(db, idempotency_key, principal, "cancel_booking", result)
    can_cancel, cancel_reason = cancellation_policy(order)
    if not can_cancel:
        return tool_result(False, "ORDER_NOT_CANCELLABLE", cancel_reason)
    order.status = "cancelled"
    result = tool_result(True, "OK", "订单取消成功", order_payload(order))
    return store_operation(db, idempotency_key, principal, "cancel_booking", result)


@app.patch("/api/v1/orders/{order_id}/travelers")
def update_travelers(
    order_id: str,
    request: TravelerUpdate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if cached := cached_operation(db, idempotency_key, principal, "update_travelers"):
        return cached
    try:
        order = owned_order(db, order_id, principal)
    except HTTPException as exc:
        return tool_result(False, "ORDER_NOT_FOUND", str(exc.detail))
    if order.status not in {"pending_payment", "paid", "upcoming"}:
        return tool_result(False, "TRAVELER_NOT_EDITABLE", "订单当前状态不能修改出行人")
    if not order.trip:
        order.trip = TripInfo(order_id=order.id, traveler_info=request.traveler_info)
    else:
        order.trip.traveler_info = request.traveler_info
    result = tool_result(
        True,
        "OK",
        "出行人修改成功",
        {"order_id": order.id, "traveler_info": request.traveler_info},
    )
    return store_operation(db, idempotency_key, principal, "update_travelers", result)


@app.post("/api/v1/orders/{order_id}/refunds")
def create_refund(
    order_id: str,
    request: RefundCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if cached := cached_operation(db, idempotency_key, principal, "create_refund"):
        return cached
    try:
        order = owned_order(db, order_id, principal)
    except HTTPException as exc:
        return tool_result(False, "ORDER_NOT_FOUND", str(exc.detail))
    if order.status in {"cancelled", "refunded"}:
        return tool_result(False, "REFUND_NOT_ALLOWED", "该订单不能发起退款")
    existing = db.scalar(
        select(Refund).where(
            Refund.order_id == order.id,
            Refund.status.in_(["pending", "approved", "processing"]),
        )
    )
    if existing:
        result = tool_result(
            True,
            "ALREADY_EXISTS",
            "该订单已有进行中的退款申请",
            {"refund_id": existing.id, "status": existing.status},
        )
        return store_operation(db, idempotency_key, principal, "create_refund", result)
    record = Refund(
        id=f"REFUND_{uuid4().hex[:10].upper()}",
        user_id=order.user_id,
        order_id=order.id,
        kind=request.kind,
        reason=request.reason,
        amount=order.total_amount if request.kind == "full" else None,
    )
    db.add(record)
    db.flush()
    result = tool_result(
        True,
        "OK",
        "退款申请创建成功",
        {"refund_id": record.id, "status": record.status},
    )
    return store_operation(db, idempotency_key, principal, "create_refund", result)


@app.post("/api/v1/orders/{order_id}/tickets")
def create_ticket(
    order_id: str,
    request: TicketCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if cached := cached_operation(db, idempotency_key, principal, "create_ticket"):
        return cached
    try:
        order = owned_order(db, order_id, principal)
    except HTTPException as exc:
        return tool_result(False, "ORDER_NOT_FOUND", str(exc.detail))
    record = Ticket(
        id=f"TK_{uuid4().hex[:10].upper()}",
        user_id=order.user_id,
        order_id=order.id,
        ticket_type=request.ticket_type,
        issue_description=request.issue_description,
    )
    db.add(record)
    db.flush()
    result = tool_result(
        True,
        "OK",
        "工单创建成功",
        {"ticket_id": record.id, "status": record.status},
    )
    return store_operation(db, idempotency_key, principal, "create_ticket", result)


@app.get("/api/v1/refunds")
def list_refunds(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
    order_id: str | None = None,
):
    statement = select(Refund)
    if principal.role == "customer":
        statement = statement.where(Refund.user_id == principal.user_id)
    if order_id:
        statement = statement.where(Refund.order_id == order_id)
    records = db.scalars(statement.order_by(Refund.created_at.desc())).all()
    return tool_result(
        True,
        "OK",
        f"找到 {len(records)} 条退款记录",
        [refund_payload(item) for item in records],
    )


@app.get("/api/v1/tickets")
def list_tickets(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(current_principal)],
    order_id: str | None = None,
):
    statement = select(Ticket)
    if principal.role == "customer":
        statement = statement.where(Ticket.user_id == principal.user_id)
    if order_id:
        statement = statement.where(Ticket.order_id == order_id)
    records = db.scalars(statement.order_by(Ticket.created_at.desc())).all()
    return tool_result(
        True,
        "OK",
        f"找到 {len(records)} 条工单记录",
        [ticket_payload(item) for item in records],
    )


@app.get("/api/v1/admin/summary")
def admin_summary(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[Principal, Depends(require_roles("agent", "admin"))],
):
    return {
        "products": len(db.scalars(select(TravelProduct)).all()),
        "orders": len(db.scalars(select(Order)).all()),
        "refunds": len(db.scalars(select(Refund)).all()),
        "tickets": len(db.scalars(select(Ticket)).all()),
        "generated_at": datetime.now(UTC).isoformat(),
    }
