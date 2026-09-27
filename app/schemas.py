from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DonationCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    donor_name: str | None = Field(default=None, max_length=200)
    donor_email: EmailStr | None = None
    message: str | None = Field(default=None, max_length=1000)


class OrderCreated(BaseModel):
    order_id: str
    status: str


class CaptureResult(BaseModel):
    order_id: str
    status: str
    capture_id: str | None = None


class DonationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    paypal_order_id: str
    paypal_capture_id: str | None
    amount: Decimal
    currency: str
    status: str
    donor_name: str | None
    donor_email: str | None
    message: str | None
    created_at: datetime
    updated_at: datetime


class PublicConfig(BaseModel):
    paypal_client_id: str
    paypal_mode: str
    currency: str
    donation_min: Decimal
    donation_max: Decimal
