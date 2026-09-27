import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DonationStatus(str, enum.Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    REFUNDED = "REFUNDED"
    REVERSED = "REVERSED"


class Donation(Base):
    __tablename__ = "donations"

    id: Mapped[int] = mapped_column(primary_key=True)
    paypal_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default=DonationStatus.CREATED.value)
    donor_name: Mapped[str | None] = mapped_column(String(200))
    donor_email: Mapped[str | None] = mapped_column(String(320))
    message: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
