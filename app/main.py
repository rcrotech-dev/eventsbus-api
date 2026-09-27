import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.config import Settings, get_settings
from app.database import Base, engine, get_db
from app.models import Donation, DonationStatus, WebhookEvent
from app.paypal import PayPalClient, PayPalError, get_paypal
from app.schemas import CaptureResult, DonationCreate, DonationOut, OrderCreated, PublicConfig

log = logging.getLogger("donations")
STATIC_DIR = FsPath(__file__).resolve().parent.parent / "static"
ORDER_ID_PATTERN = r"^[A-Z0-9]{1,64}$"

CAPTURE_STATUS_MAP = {
    "COMPLETED": DonationStatus.COMPLETED,
    "PENDING": DonationStatus.PENDING,
    "DECLINED": DonationStatus.DENIED,
    "FAILED": DonationStatus.DENIED,
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(PayPalError)
def paypal_error_handler(_: Request, exc: PayPalError) -> JSONResponse:
    log.error("PayPal error %s: %s", exc.status_code, exc.detail)
    return JSONResponse(status_code=502, content={"detail": "Payment provider error"})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", response_model=PublicConfig)
def public_config(settings: Settings = Depends(get_settings)) -> PublicConfig:
    return PublicConfig(
        paypal_client_id=settings.paypal_client_id,
        paypal_mode=settings.paypal_mode,
        currency=settings.donation_currency,
        donation_min=settings.donation_min,
        donation_max=settings.donation_max,
    )


@app.post("/api/donations/orders", response_model=OrderCreated, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: DonationCreate,
    db: Session = Depends(get_db),
    paypal: PayPalClient = Depends(get_paypal),
    settings: Settings = Depends(get_settings),
) -> OrderCreated:
    if not settings.donation_min <= payload.amount <= settings.donation_max:
        raise HTTPException(
            422,
            f"Amount must be between {settings.donation_min} and {settings.donation_max}",
        )
    order = paypal.create_order(
        payload.amount, settings.donation_currency, "Donation", request_id=str(uuid.uuid4())
    )
    db.add(
        Donation(
            paypal_order_id=order["id"],
            amount=payload.amount,
            currency=settings.donation_currency,
            status=DonationStatus.CREATED.value,
            donor_name=payload.donor_name,
            donor_email=payload.donor_email,
            message=payload.message,
        )
    )
    db.commit()
    return OrderCreated(order_id=order["id"], status=order["status"])


@app.post("/api/donations/orders/{order_id}/capture", response_model=CaptureResult)
def capture_order(
    order_id: str = Path(pattern=ORDER_ID_PATTERN),
    db: Session = Depends(get_db),
    paypal: PayPalClient = Depends(get_paypal),
) -> CaptureResult:
    donation = db.scalar(select(Donation).where(Donation.paypal_order_id == order_id))
    if donation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if donation.status == DonationStatus.COMPLETED.value:
        return CaptureResult(
            order_id=order_id, status=donation.status, capture_id=donation.paypal_capture_id
        )

    result = paypal.capture_order(order_id, request_id=f"capture-{order_id}")
    captures = result.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
    if captures:
        capture = captures[0]
        donation.paypal_capture_id = capture["id"]
        donation.status = CAPTURE_STATUS_MAP.get(capture["status"], DonationStatus.PENDING).value
    db.commit()
    return CaptureResult(
        order_id=order_id, status=donation.status, capture_id=donation.paypal_capture_id
    )


def _find_by_capture(db: Session, capture_id: str | None) -> Donation | None:
    if not capture_id:
        return None
    return db.scalar(select(Donation).where(Donation.paypal_capture_id == capture_id))


def _capture_id_from_refund(resource: dict[str, Any]) -> str | None:
    for link in resource.get("links", []):
        if link.get("rel") == "up" and "/captures/" in link.get("href", ""):
            return link["href"].rstrip("/").rsplit("/", 1)[-1]
    return None


def _apply_event(db: Session, event_type: str, resource: dict[str, Any]) -> None:
    if event_type in ("PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.PENDING"):
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        donation = db.scalar(select(Donation).where(Donation.paypal_order_id == order_id))
        if donation is None:
            donation = _find_by_capture(db, resource.get("id"))
        if donation is not None:
            donation.paypal_capture_id = resource.get("id")
            donation.status = event_type.rsplit(".", 1)[-1]
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        donation = _find_by_capture(db, _capture_id_from_refund(resource))
        if donation is not None:
            donation.status = DonationStatus.REFUNDED.value
    elif event_type == "PAYMENT.CAPTURE.REVERSED":
        donation = _find_by_capture(db, resource.get("id"))
        if donation is not None:
            donation.status = DonationStatus.REVERSED.value
    else:
        log.info("Ignoring PayPal webhook event %s", event_type)


@app.post("/api/paypal/webhook")
def paypal_webhook(
    request: Request,
    event: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    paypal: PayPalClient = Depends(get_paypal),
) -> dict[str, str]:
    if not paypal.verify_webhook(request.headers, event):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    event_id = event.get("id")
    event_type = event.get("event_type", "")
    if not event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing event id")
    if db.get(WebhookEvent, event_id) is not None:
        return {"status": "duplicate"}

    _apply_event(db, event_type, event.get("resource", {}))
    db.add(WebhookEvent(id=event_id, event_type=event_type))
    db.commit()
    return {"status": "ok"}


@app.get("/api/donations", response_model=list[DonationOut])
def list_donations(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _claims: dict = Depends(require_admin),
) -> list[Donation]:
    return list(db.scalars(select(Donation).order_by(Donation.created_at.desc()).limit(limit)))
