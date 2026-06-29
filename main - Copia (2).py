import sqlite3
import secrets
import hmac
import hashlib
import httpx
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

DATABASE = "unified.db"
# Poka-Yoke: Usando variáveis de ambiente para segurança no Render
APP_ID = os.getenv("MERCADO_LIVRE_CLIENT_ID", "YOUR_MELI_APP_ID")
APP_SECRET = os.getenv("MERCADO_LIVRE_CLIENT_SECRET", "YOUR_MELI_APP_SECRET")
REDIRECT_URI = os.getenv("MERCADO_LIVRE_REDIRECT_URI", "https://yourdomain.com/oauth/meli/callback")
IUGU_API_TOKEN = os.getenv("IUGU_API_TOKEN", "YOUR_IUGU_API_TOKEN")
IUGU_WEBHOOK_SECRET = os.getenv("IUGU_WEBHOOK_SECRET", "test_secret_iugu")

def init_db():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS meli_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id TEXT UNIQUE, access_token TEXT, refresh_token TEXT, expires_at TEXT, created_at TEXT, updated_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS meli_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE, seller_id TEXT, status TEXT, payload TEXT, created_at TEXT, updated_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS iugu_invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT UNIQUE, subscription_id TEXT, status TEXT, company_id TEXT, payload TEXT, created_at TEXT, updated_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT UNIQUE, active INTEGER DEFAULT 1, blocked_at TEXT, created_at TEXT, updated_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, name TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL, price REAL NOT NULL, stock INTEGER DEFAULT 0, meli_item_id TEXT, created_at TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def now_utc():
    return datetime.now(timezone.utc).isoformat()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class UserAuth(BaseModel):
    email: str
    password: str
    name: str | None = None

class ProductSchema(BaseModel):
    sku: str
    name: str
    price: float
    stock: int
    meli_item_id: str | None = None

class StockUpdateSchema(BaseModel):
    quantity: int
    seller_id: str

class MeliWebhookPayload(BaseModel):
    resource: str | None = None
    topic: str | None = None
    user_id: int | None = None
    application_id: int | None = None
    sent: str | None = None

class IuguWebhookPayload(BaseModel):
    event: str | None = None
    data: dict | None = None

@app.get("/oauth/meli")
def meli_oauth():
    state = secrets.token_urlsafe(16)
    params = {"response_type": "code", "client_id": APP_ID, "redirect_uri": REDIRECT_URI, "state": state}
    url = f"https://auth.mercadolivre.com.br/authorization?{urlencode(params)}"
    return RedirectResponse(url)

@app.get("/oauth/meli/callback")
async def meli_oauth_callback(code: str, state: str):
    token_url = "https://api.mercadolibre.com/oauth/token"
    payload = {"grant_type": "authorization_code", "client_id": APP_ID, "client_secret": APP_SECRET, "code": code, "redirect_uri": REDIRECT_URI}
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=payload)
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"MELI token error: {resp.text}")
    data = resp.json()
    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 21600)
    seller_id = str(data.get("user_id", ""))
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("INSERT INTO meli_tokens (seller_id, access_token, refresh_token, expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(seller_id) DO UPDATE SET access_token=excluded.access_token, refresh_token=excluded.refresh_token, expires_at=excluded.expires_at, updated_at=excluded.updated_at", (seller_id, access_token, refresh_token, expires_at, now_utc(), now_utc()))
    conn.commit()
    conn.close()
    return {"message": "MELI OAuth OK", "seller_id": seller_id}

async def refresh_meli_token(seller_id: str):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    row = cur.execute("SELECT refresh_token FROM meli_tokens WHERE seller_id=?", (seller_id,)).fetchone()
    if not row or not row[0]:
        conn.close()
        raise HTTPException(status_code=400, detail="No refresh token")
    refresh_token = row[0]
    token_url = "https://api.mercadolibre.com/oauth/token"
    payload = {"grant_type": "refresh_token", "client_id": APP_ID, "client_secret": APP_SECRET, "refresh_token": refresh_token}
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=payload)
    if resp.status_code != 200:
        conn.close()
        raise HTTPException(status_code=400, detail=f"MELI refresh error: {resp.text}")
    data = resp.json()
    new_access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 21600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    cur.execute("UPDATE meli_tokens SET access_token=?, refresh_token=?, expires_at=?, updated_at=? WHERE seller_id=?", (new_access, new_refresh, expires_at, now_utc(), seller_id))
    conn.commit()
    conn.close()
    return new_access

async def get_meli_token(seller_id: str) -> str:
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    row = cur.execute("SELECT access_token, expires_at FROM meli_tokens WHERE seller_id=?", (seller_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Token not found")
    access_token, expires_at = row
    if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc) + timedelta(minutes=5):
        access_token = await refresh_meli_token(seller_id)
    return access_token

@app.post("/meli/stock/{item_id}")
async def update_meli_stock(item_id: str, seller_id: str, quantity: int):
    token = await get_meli_token(seller_id)
    url = f"https://api.mercadolibre.com/items/{item_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"available_quantity": quantity}
    async with httpx.AsyncClient() as client:
        resp = await client.put(url, json=body, headers=headers)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()

@app.post("/webhooks/meli")
async def meli_webhook(request: Request):
    body = await request.body()
    try: payload = MeliWebhookPayload.parse_raw(body)
    except Exception: payload = None
    resource = payload.resource if payload else None
    topic = payload.topic if payload else None
    seller_id = str(payload.user_id) if payload and payload.user_id else None
    if topic == "orders_v2" and resource:
        order_id = resource.split("/")[-1]
        await fetch_and_store_meli_order(order_id, seller_id)
    return {"status": "ok"}

async def fetch_and_store_meli_order(order_id: str, seller_id: str | None):
    if not seller_id: return
    status, payload_text = "paid", '{"message": "Pedido simulado"}'
    try:
        token = await get_meli_token(seller_id)
        url = f"https://api.mercadolibre.com/orders/{order_id}"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            status, payload_text = resp.json().get("status", "paid"), resp.text
    except Exception: pass
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("INSERT INTO meli_orders (order_id, seller_id, status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(order_id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at", (order_id, seller_id, status, payload_text, now_utc(), now_utc()))
    conn.commit()
    conn.close()

def verify_iugu_signature(payload: bytes, signature: str) -> bool:
    expected = hmac.new(IUGU_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

@app.post("/webhooks/iugu")
async def iugu_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("x-iugu-signature", "")
    if not verify_iugu_signature(body, signature): raise HTTPException(status_code=401, detail="Invalid signature")
    try: payload = IuguWebhookPayload.parse_raw(body)
    except Exception: raise HTTPException(status_code=400, detail="Invalid JSON")
    event, data = payload.event or "", payload.data or {}
    invoice_id, subscription_id, status = str(data.get("id", "")), str(data.get("subscription_id", "")), str(data.get("status", ""))
    company_id = subscription_id
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("INSERT INTO companies (company_id, active, created_at, updated_at) VALUES (?, 1, ?, ?) ON CONFLICT(company_id) DO UPDATE SET updated_at=excluded.updated_at", (company_id, now_utc(), now_utc()))
    cur.execute("INSERT INTO iugu_invoices (invoice_id, subscription_id, status, company_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at", (invoice_id, subscription_id, status, company_id, body.decode(), now_utc(), now_utc()))
    if event.lower() in ("invoice.payment_failed", "invoice.canceled", "subscription.suspended"):
        cur.execute("UPDATE companies SET active=0, blocked_at=?, updated_at=? WHERE company_id=?", (now_utc(), now_utc(), company_id))
    elif event.lower() in ("invoice.paid", "subscription.activated", "subscription.renewed"):
        cur.execute("UPDATE companies SET active=1, blocked_at=NULL, updated_at=? WHERE company_id=?", (now_utc(), company_id))
    conn.commit()
    conn.close()
    return {"status": "processed"}

@app.post("/auth/register")
def register(user: UserAuth):
    conn = sqlite3.connect(DATABASE); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)", (user.email, user.password, user.name, now_utc()))
        conn.commit(); return {"message": "OK"}
    except sqlite3.IntegrityError: raise HTTPException(status_code=400, detail="E-mail já cadastrado.")
    finally: conn.close()

@app.post("/auth/login")
def login(user: UserAuth):
    conn = sqlite3.connect(DATABASE); cur = conn.cursor()
    row = cur.execute("SELECT id, email, name FROM users WHERE email=? AND password=?", (user.email, user.password)).fetchone()
    conn.close()
    if not row: raise HTTPException(status_code=401, detail="Erro")
    return {"id": row[0], "email": row[1], "name": row[2]}

@app.get("/products")
def list_products():
    conn = sqlite3.connect(DATABASE); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    rows = cur.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/products")
def create_product(product: ProductSchema):
    conn = sqlite3.connect(DATABASE); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO products (sku, name, price, stock, meli_item_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (product.sku, product.name, product.price, product.stock, product.meli_item_id, now_utc(), now_utc()))
        conn.commit(); return {"message": "OK"}
    except sqlite3.IntegrityError: raise HTTPException(status_code=400, detail="Erro")
    finally: conn.close()

@app.put("/products/{sku}/stock")
async def update_product_stock(sku: str, payload: StockUpdateSchema):
    conn = sqlite3.connect(DATABASE); cur = conn.cursor()
    product = cur.execute("SELECT meli_item_id FROM products WHERE sku=?", (sku,)).fetchone()
    if not product: conn.close(); raise HTTPException(status_code=404, detail="Erro")
    cur.execute("UPDATE products SET stock=?, updated_at=? WHERE sku=?", (payload.quantity, now_utc(), sku))
    conn.commit(); conn.close()
    meli_item_id = product[0]
    if meli_item_id:
        try:
            await update_meli_stock(meli_item_id, payload.seller_id, payload.quantity)
            return {"status": "success"}
        except Exception as e: return {"status": "partial", "message": str(e)}
    return {"status": "success"}

@app.get("/client/orders")
def list_client_orders():
    conn = sqlite3.connect(DATABASE); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    rows = cur.execute("SELECT * FROM meli_orders ORDER BY created_at DESC").fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/client/orders/{order_id}/issue-nfe")
def issue_nfe(order_id: str):
    conn = sqlite3.connect(DATABASE); cur = conn.cursor()
    order = cur.execute("SELECT id FROM meli_orders WHERE order_id=?", (order_id,)).fetchone()
    if not order: conn.close(); raise HTTPException(status_code=404, detail="Erro")
    cur.execute("UPDATE meli_orders SET status='invoice_issued', updated_at=? WHERE order_id=?", (now_utc(), order_id))
    conn.commit(); conn.close()
    return {"status": "success"}

@app.get("/health")
def health(): return {"status": "ok", "time": now_utc()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))