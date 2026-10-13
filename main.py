import base64
import time
import uuid
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Orders Engineering API")

# Enable CORS so the browser / grader can verify requests directly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fixed catalog of T = 45 orders (IDs 1 through 45)
TOTAL_ORDERS = 45
ORDERS_CATALOG = [
    {"id": i, "item": f"Product Item #{i}", "amount": i * 12.50} 
    for i in range(1, TOTAL_ORDERS + 1)
]

# In-memory storage for idempotency and per-client rate limiting
idempotency_store: Dict[str, Dict[str, Any]] = {}
rate_limit_store: Dict[str, List[float]] = {}

RATE_LIMIT_MAX = 17       # R requests
RATE_LIMIT_WINDOW = 10.0  # per 10 seconds

class OrderCreateRequest(BaseModel):
    item_name: Optional[str] = "Standard Order"
    notes: Optional[str] = None

# Health check / ping endpoint required by grader
@app.get("/ping")
def ping():
    return {"email": "23f2003924@ds.study.iitm.ac.in"}

# Middleware for Per-Client Rate Limiting using X-Client-Id header
@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    # Allow health check /ping to bypass rate limiting
    if request.url.path == "/ping":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = time.time()

    if client_id not in rate_limit_store:
        rate_limit_store[client_id] = []

    # Clean up timestamps outside the 10-second window
    timestamps = [t for t in rate_limit_store[client_id] if now - t < RATE_LIMIT_WINDOW]

    if len(timestamps) >= RATE_LIMIT_MAX:
        rate_limit_store[client_id] = timestamps
        return Response(
            content='{"detail": "Rate limit exceeded."}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": "10"}
        )

    timestamps.append(now)
    rate_limit_store[client_id] = timestamps

    response = await call_next(request)
    return response

# 1. Idempotent Order Creation
@app.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    order_req: OrderCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if idempotency_key:
        if idempotency_key in idempotency_store:
            return idempotency_store[idempotency_key]

    order_id = str(uuid.uuid4())
    new_order = {
        "id": order_id,
        "item_name": order_req.item_name,
        "notes": order_req.notes,
        "status": "confirmed"
    }

    if idempotency_key:
        idempotency_store[idempotency_key] = new_order

    return new_order

# 2. Cursor-Based Pagination
@app.get("/orders")
def get_orders(
    limit: int = Query(10, ge=1, le=100),
    cursor: Optional[str] = None
):
    start_index = 0
    if cursor:
        try:
            decoded_bytes = base64.b64decode(cursor.encode("utf-8"))
            start_index = int(decoded_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format."
            )

    if start_index >= TOTAL_ORDERS:
        return {"items": [], "next_cursor": None}

    end_index = min(start_index + limit, TOTAL_ORDERS)
    items = ORDERS_CATALOG[start_index:end_index]

    next_cursor = None
    if end_index < TOTAL_ORDERS:
        cursor_str = str(end_index)
        next_cursor = base64.b64encode(cursor_str.encode("utf-8")).decode("utf-8")

    return {
        "items": items,
        "next_cursor": next_cursor
    }
