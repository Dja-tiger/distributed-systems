import os
from typing import Dict, List

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class PaymentRequest(BaseModel):
    order_id: str = Field(..., description="Unique order identifier")
    amount: float = Field(..., gt=0)
    force_fail: bool = Field(False, description="Simulate payment failure for testing")


class ReservationRequest(BaseModel):
    order_id: str
    sku: str | None = None
    quantity: int | None = Field(None, gt=0)
    slot: str | None = None
    force_fail: bool = Field(False, description="Simulate failure for testing")


class OrderRequest(BaseModel):
    order_id: str
    amount: float = Field(..., gt=0)
    sku: str
    quantity: int = Field(..., gt=0)
    slot: str
    force_payment_failure: bool = False
    force_inventory_failure: bool = False
    force_delivery_failure: bool = False


class OrderResponse(BaseModel):
    status: str
    steps: List[str]


ROLE = os.getenv("ROLE", "order").lower()
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://payment-service:8000")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory-service:8000")
DELIVERY_URL = os.getenv("DELIVERY_URL", "http://delivery-service:8000")

payment_reservations: Dict[str, PaymentRequest] = {}
inventory_reservations: Dict[str, ReservationRequest] = {}
delivery_reservations: Dict[str, ReservationRequest] = {}
orders: Dict[str, OrderResponse] = {}


async def call_service(url: str, payload: dict, action: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
    if response.status_code >= 400:
        detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
        raise HTTPException(status_code=502, detail=f"{action} failed: {detail}")
    return response.json()


def create_app() -> FastAPI:
    app = FastAPI(title="Distributed transaction demo", version="0.1.0")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "role": ROLE}

    if ROLE == "payment":
        register_payment_routes(app)
    elif ROLE == "inventory":
        register_inventory_routes(app)
    elif ROLE == "delivery":
        register_delivery_routes(app)
    else:
        register_order_routes(app)

    return app


def register_payment_routes(app: FastAPI) -> None:
    @app.post("/payment/reserve")
    async def reserve_payment(request: PaymentRequest) -> dict:
        if request.force_fail:
            raise HTTPException(status_code=409, detail="Payment gateway rejected transaction")
        payment_reservations[request.order_id] = request
        return {"status": "reserved", "order_id": request.order_id}

    @app.post("/payment/cancel")
    async def cancel_payment(request: ReservationRequest) -> dict:
        payment_reservations.pop(request.order_id, None)
        return {"status": "cancelled", "order_id": request.order_id}


def register_inventory_routes(app: FastAPI) -> None:
    @app.post("/inventory/reserve")
    async def reserve_inventory(request: ReservationRequest) -> dict:
        if request.force_fail:
            raise HTTPException(status_code=409, detail="Stock reservation failed")
        if request.quantity is None:
            raise HTTPException(status_code=400, detail="Quantity is required")
        inventory_reservations[request.order_id] = request
        return {"status": "reserved", "order_id": request.order_id}

    @app.post("/inventory/cancel")
    async def cancel_inventory(request: ReservationRequest) -> dict:
        inventory_reservations.pop(request.order_id, None)
        return {"status": "cancelled", "order_id": request.order_id}


def register_delivery_routes(app: FastAPI) -> None:
    @app.post("/delivery/reserve")
    async def reserve_delivery(request: ReservationRequest) -> dict:
        if request.force_fail:
            raise HTTPException(status_code=409, detail="Delivery slot unavailable")
        if not request.slot:
            raise HTTPException(status_code=400, detail="Slot is required")
        delivery_reservations[request.order_id] = request
        return {"status": "reserved", "order_id": request.order_id}

    @app.post("/delivery/cancel")
    async def cancel_delivery(request: ReservationRequest) -> dict:
        delivery_reservations.pop(request.order_id, None)
        return {"status": "cancelled", "order_id": request.order_id}


def register_order_routes(app: FastAPI) -> None:
    @app.post("/orders", response_model=OrderResponse)
    async def create_order(request: OrderRequest) -> OrderResponse:
        saga_steps: List[str] = []
        compensations: List[tuple[str, dict, str]] = []

        try:
            await call_service(
                f"{PAYMENT_URL}/payment/reserve",
                {"order_id": request.order_id, "amount": request.amount, "force_fail": request.force_payment_failure},
                "Payment reservation",
            )
            saga_steps.append("payment reserved")
            compensations.append((f"{PAYMENT_URL}/payment/cancel", {"order_id": request.order_id}, "payment cancel"))

            await call_service(
                f"{INVENTORY_URL}/inventory/reserve",
                {
                    "order_id": request.order_id,
                    "sku": request.sku,
                    "quantity": request.quantity,
                    "force_fail": request.force_inventory_failure,
                },
                "Inventory reservation",
            )
            saga_steps.append("inventory reserved")
            compensations.append((f"{INVENTORY_URL}/inventory/cancel", {"order_id": request.order_id}, "inventory cancel"))

            await call_service(
                f"{DELIVERY_URL}/delivery/reserve",
                {
                    "order_id": request.order_id,
                    "slot": request.slot,
                    "force_fail": request.force_delivery_failure,
                },
                "Delivery reservation",
            )
            saga_steps.append("delivery reserved")
            compensations.append((f"{DELIVERY_URL}/delivery/cancel", {"order_id": request.order_id}, "delivery cancel"))

            orders[request.order_id] = OrderResponse(status="confirmed", steps=saga_steps.copy())
            return orders[request.order_id]
        except HTTPException as http_error:
            await run_compensations(compensations)
            raise http_error
        except Exception as exc:  # pragma: no cover - unexpected error path
            await run_compensations(compensations)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/orders/{order_id}", response_model=OrderResponse)
    async def get_order(order_id: str) -> OrderResponse:
        if order_id not in orders:
            raise HTTPException(status_code=404, detail="Order not found")
        return orders[order_id]


async def run_compensations(compensations: List[tuple[str, dict, str]]) -> None:
    for url, payload, action in reversed(compensations):
        try:
            await call_service(url, payload, action)
        except HTTPException:
            # Best-effort compensation; in a real system we would persist and retry.
            pass


app = create_app()
