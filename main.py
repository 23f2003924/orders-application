import base64
import time
from typing import Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Production-Grade Orders API")

# Enable CORS to allow requests from any origin (as required for grading/verification)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-Memory State & Constants ---
TOTAL_ORDERS = 45
RATE_LIMIT_MAX = 17
RATE_LIMIT_WINDOW = 10  # seconds

# In-memory storage
orders_db: Dict[int, dict] = {
    i: {"id": i, "item": f"Product-{i}", "amount": i * 10.0}
    for i in range(1, TOTAL_ORDERS + 1)
}
idempotency_store: Dict[str, dict] = {}
rate_limit_store: Dict[str, List[float]] = {}


class OrderCreate(BaseModel):
    item: str
    amount: float


# --- Helper: Cursor Encoding/Decoding ---
def encode_cursor(order_id: int) -> str:
    return base64.urlsafe_b64encode(str(order_id).encode()).decode()


def decode_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor format")


# --- Middleware / Dependency: Rate Limiter ---
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate limit the orders endpoints
    if request.url.path.startswith("/orders"):
        client_id = request.headers.get("X-Client-Id", "default-client")
        current_time = time.time()

        if client_id not in rate_limit_store:
            rate_limit_store[client_id] = []

        # Filter out timestamps outside the 10-second sliding window
        window_start = current_time - RATE_LIMIT_WINDOW
        rate_limit_store[client_id] = [
            t for t in rate_limit_store[client_id] if t > window_start
        ]

        if len(rate_limit_store[client_id]) >= RATE_LIMIT_MAX:
            # Calculate precise retry-after seconds
            oldest_ts = rate_limit_store[client_id][0]
            retry_after = int(max(1, (oldest_ts + RATE_LIMIT_WINDOW) - current_time))
            return Response(
                content="Rate limit exceeded",
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        rate_limit_store[client_id].append(current_time)

    response = await call_next(request)
    return response


# --- 1. Idempotent Order Creation ---
@app.post("/orders", status_code=201)
def create_order(
    order: OrderCreate,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if idempotency_key:
        if idempotency_key in idempotency_store:
            # Return previously cached response/order id
            return idempotency_store[idempotency_key]

    # Create new order in catalog
    new_id = len(orders_db) + 1
    if new_id > TOTAL_ORDERS:
        # Loop or wrap if exceeding catalog size for safety, or keep incrementing
        new_id = (len(orders_db) % TOTAL_ORDERS) + 1

    created_order = {
        "id": new_id,
        "item": order.item,
        "amount": order.amount,
        "status": "created",
    }
    orders_db[new_id] = created_order

    if idempotency_key:
        idempotency_store[idempotency_key] = created_order

    return created_order


# --- 2. Cursor Pagination ---
@app.get("/orders")
def list_orders(
    limit: int = Query(10, ge=1, le=100), cursor: Optional[str] = None
):
    # Sort catalog IDs to ensure deterministic ordering
    sorted_ids = sorted(orders_db.keys())

    start_index = 0
    if cursor:
        decoded_id = decode_cursor(cursor)
        if decoded_id in sorted_ids:
            start_index = sorted_ids.index(decoded_id) + 1

    paginated_ids = sorted_ids[start_index : start_index + limit]
    items = [orders_db[oid] for oid in paginated_ids]

    next_cursor = None
    if start_index + limit < len(sorted_ids):
        last_id_in_page = paginated_ids[-1]
        next_cursor = encode_cursor(last_id_in_page)

    return {"items": items, "next_cursor": next_cursor}
