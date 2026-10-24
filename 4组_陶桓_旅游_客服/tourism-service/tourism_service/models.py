from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tourism_service.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    membership: Mapped[str] = mapped_column(String(40), default="普通会员")
    risk_level: Mapped[str] = mapped_column(String(20), default="normal")


class TravelProduct(Base):
    """旅游产品：酒店 / 景区门票 / 机票火车票汽车票 / 接送服务。"""

    __tablename__ = "travel_products"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    type: Mapped[str] = mapped_column(String(20), index=True)  # hotel|scenic|ticket|transfer
    city: Mapped[str] = mapped_column(String(40), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active")
    description: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    travel_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    travel_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    trip: Mapped["TripInfo | None"] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("travel_products.id"))
    product_name: Mapped[str] = mapped_column(String(160))
    product_type: Mapped[str] = mapped_column(String(20), default="hotel")
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    order: Mapped[Order] = relationship(back_populates="items")


class TripInfo(Base):
    """订单出行信息（替代原物流）。"""

    __tablename__ = "trip_infos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    travel_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checkin_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checkout_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    departure_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    arrival_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transport_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    traveler_info: Mapped[list] = mapped_column(JSONB, default=list)
    meeting_point: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="upcoming")
    events: Mapped[list] = mapped_column(JSONB, default=list)
    order: Mapped[Order] = relationship(back_populates="trip")


class Refund(Base):
    """退款申请记录（替代原售后）。"""

    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # full|partial|reschedule
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Ticket(Base):
    """投诉 / 售后 / 退款工单。"""

    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True, nullable=True)
    ticket_type: Mapped[str] = mapped_column(String(20))  # after_sale|complaint|refund
    issue_description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
