import sqlite3
import secrets
import hmac
import hashlib
import base64
import httpx
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, parse_qs, urlparse
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

DATABASE = "unified.db"
APP_ID = "YOUR_MELI_APP_ID"
APP_SECRET = "YOUR_MELI_APP_SECRET"
REDIRECT_URI = "https://yourdomain.com/oauth/meli/callback"
IUGU_API_TOKEN = "YOUR_IUGU_API_TOKEN"
IUGU_WEBHOOK_SECRET = "test_secret_iugu" # <-- Alinhado com o simulador

def init_db():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meli_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id TEXT UNIQUE,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meli_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            seller_id TEXT,
            status TEXT,
            payload TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS iugu_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id TEXT UNIQUE,
            subscription_id TEXT,
            status TEXT,
            company_id TEXT,
            payload TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT UNIQUE,
            active INTEGER DEFAULT 1,
            blocked_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    
    # =======================================================================
    # ADICIONE ESTAS DUAS TABELAS ABAIXO (Controle de Usuários e Estoque)
    # =======================================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER DEFAULT 0,
            meli_item_id TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    # =======================================================================
    
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

# Habilita CORS para permitir conexões do Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# ENDPOINTS DE LEITURA PARA O DASHBOARD (API)
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
def get_dashboard_data():
    """Retorna as métricas consolidadas e os últimos registros para o painel."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        # Métricas de Empresas (Tenants)
        total_companies = cur.execute("SELECT COUNT(*) FROM companies").fetchone()[0] or 0
        active_companies = cur.execute("SELECT COUNT(*) FROM companies WHERE active = 1").fetchone()[0] or 0
        blocked_companies = cur.execute("SELECT COUNT(*) FROM companies WHERE active = 0").fetchone()[0] or 0

        # Métricas de Faturas Iugu
        total_invoices = cur.execute("SELECT COUNT(*) FROM iugu_invoices").fetchone()[0] or 0
        paid_invoices = cur.execute("SELECT COUNT(*) FROM iugu_invoices WHERE LOWER(status) = 'paid'").fetchone()[0] or 0
        pending_invoices = cur.execute("SELECT COUNT(*) FROM iugu_invoices WHERE LOWER(status) = 'pending'").fetchone()[0] or 0

        # Métricas de Pedidos Mercado Livre
        total_orders = cur.execute("SELECT COUNT(*) FROM meli_orders").fetchone()[0] or 0
        approved_orders = cur.execute("SELECT COUNT(*) FROM meli_orders WHERE LOWER(status) = 'paid' OR LOWER(status) = 'approved'").fetchone()[0] or 0
        canceled_orders = cur.execute("SELECT COUNT(*) FROM meli_orders WHERE LOWER(status) = 'cancelled' OR LOWER(status) = 'canceled'").fetchone()[0] or 0

        # Últimas 5 Faturas da Iugu
        invoices_rows = cur.execute("SELECT invoice_id, subscription_id, status, updated_at FROM iugu_invoices ORDER BY updated_at DESC LIMIT 5").fetchall()
        invoices = [dict(row) for row in invoices_rows]

        # Últimos 5 Pedidos do Mercado Livre
        orders_rows = cur.execute("SELECT order_id, seller_id, status, updated_at FROM meli_orders ORDER BY updated_at DESC LIMIT 5").fetchall()
        orders = [dict(row) for row in orders_rows]

        return {
            "companies": {
                "total": total_companies,
                "active": active_companies,
                "blocked": blocked_companies
            },
            "invoices": {
                "total": total_invoices,
                "paid": paid_invoices,
                "pending": pending_invoices
            },
            "orders": {
                "total": total_orders,
                "approved": approved_orders,
                "canceled": canceled_orders
            },
            "latest_invoices": invoices,
            "latest_orders": orders
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# ROTA DO DASHBOARD VISUAL (HTML/CSS/JS)
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard_page():
    """Retorna a interface visual do ERP integrada em tempo real."""
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MILI ERP - Gestão Integrada</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Inter', sans-serif; }
        </style>
    </head>
    <body class="bg-slate-50 text-slate-800 min-h-screen">
        <!-- Header -->
        <header class="bg-slate-900 text-white shadow-md px-6 py-4 flex justify-between items-center">
            <div>
                <h1 class="text-xl font-bold tracking-tight">MILI ERP</h1>
                <p class="text-xs text-slate-400">Gestão Integrada de Tenants e Webhooks</p>
            </div>
            <div class="flex items-center gap-2 bg-slate-800 px-3 py-1.5 rounded-lg text-xs font-medium text-emerald-400">
                <span class="w-2 h-2 bg-emerald-400 rounded-full animate-pulse"></span>
                Atualização automática: Ativa (5s)
            </div>
        </header>

        <!-- Main Content -->
        <main class="max-w-7xl mx-auto p-6 space-y-6">
            <!-- Cards de Métricas -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <!-- Card Tenants -->
                <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-100 flex items-start justify-between">
                    <div class="space-y-2">
                        <p class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Empresas (Tenants)</p>
                        <h3 class="text-2xl font-bold" id="companies-total">0 Cadastradas</h3>
                        <div class="flex gap-3 text-xs font-medium">
                            <span class="text-emerald-600" id="companies-active">● 0 Ativas</span>
                            <span class="text-rose-600" id="companies-blocked">● 0 Bloqueadas</span>
                        </div>
                    </div>
                    <div class="bg-slate-100 p-3 rounded-lg text-slate-700">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path></svg>
                    </div>
                </div>

                <!-- Card Iugu -->
                <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-100 flex items-start justify-between">
                    <div class="space-y-2">
                        <p class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Faturamento Iugu</p>
                        <h3 class="text-2xl font-bold" id="invoices-total">0 Faturas</h3>
                        <div class="flex gap-3 text-xs font-medium">
                            <span class="text-emerald-600" id="invoices-paid">● 0 Pagas</span>
                            <span class="text-amber-500" id="invoices-pending">● 0 Pendentes</span>
                        </div>
                    </div>
                    <div class="bg-slate-100 p-3 rounded-lg text-slate-700">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    </div>
                </div>

                <!-- Card Mercado Livre -->
                <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-100 flex items-start justify-between">
                    <div class="space-y-2">
                        <p class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Vendas Mercado Livre</p>
                        <h3 class="text-2xl font-bold" id="orders-total">0 Pedidos</h3>
                        <div class="flex gap-3 text-xs font-medium">
                            <span class="text-emerald-600" id="orders-approved">● 0 Aprovados</span>
                            <span class="text-rose-600" id="orders-canceled">● 0 Cancelados</span>
                        </div>
                    </div>
                    <div class="bg-slate-100 p-3 rounded-lg text-slate-700">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 11m8 4V11m0 4a2 2 0 110-4m0 4v5"></path></svg>
                    </div>
                </div>
            </div>

            <!-- Tabelas de Dados -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Tabela Iugu -->
                <div class="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
                    <div class="px-6 py-4 border-b border-slate-100 flex justify-between items-center">
                        <h4 class="font-bold text-slate-700">Últimas Faturas (Iugu)</h4>
                        <span class="text-xs bg-slate-100 text-slate-600 px-2.5 py-1 rounded-full font-medium">Sincronizado</span>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50 text-slate-500 text-xs font-semibold uppercase border-b border-slate-100">
                                    <th class="px-6 py-3">Fatura ID</th>
                                    <th class="px-6 py-3">Assinatura ID</th>
                                    <th class="px-6 py-3">Status</th>
                                    <th class="px-6 py-3">Atualizado em</th>
                                </tr>
                            </thead>
                            <tbody id="table-invoices" class="divide-y divide-slate-100 text-sm">
                                <tr>
                                    <td colspan="4" class="px-6 py-8 text-center text-slate-400">Nenhuma fatura recebida ainda.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Tabela Mercado Livre -->
                <div class="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden">
                    <div class="px-6 py-4 border-b border-slate-100 flex justify-between items-center">
                        <h4 class="font-bold text-slate-700">Últimos Pedidos (Mercado Livre)</h4>
                        <span class="text-xs bg-slate-100 text-slate-600 px-2.5 py-1 rounded-full font-medium">Sincronizado</span>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50 text-slate-500 text-xs font-semibold uppercase border-b border-slate-100">
                                    <th class="px-6 py-3">Pedido ID</th>
                                    <th class="px-6 py-3">Vendedor ID</th>
                                    <th class="px-6 py-3">Status</th>
                                    <th class="px-6 py-3">Atualizado em</th>
                                </tr>
                            </thead>
                            <tbody id="table-orders" class="divide-y divide-slate-100 text-sm">
                                <tr>
                                    <td colspan="4" class="px-6 py-8 text-center text-slate-400">Nenhum pedido recebido ainda.</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </main>

        <script>
            async function updateDashboard() {
                try {
                    const response = await fetch('/api/dashboard');
                    if (!response.ok) throw new Error('Erro ao buscar dados');
                    const data = await response.json();

                    // Atualiza Cards
                    document.getElementById('companies-total').innerText = `${data.companies.total} Cadastradas`;
                    document.getElementById('companies-active').innerText = `● ${data.companies.active} Ativas`;
                    document.getElementById('companies-blocked').innerText = `● ${data.companies.blocked} Bloqueadas`;

                    document.getElementById('invoices-total').innerText = `${data.invoices.total} Faturas`;
                    document.getElementById('invoices-paid').innerText = `● ${data.invoices.paid} Pagas`;
                    document.getElementById('invoices-pending').innerText = `● ${data.invoices.pending} Pendentes`;

                    document.getElementById('orders-total').innerText = `${data.orders.total} Pedidos`;
                    document.getElementById('orders-approved').innerText = `● ${data.orders.approved} Aprovados`;
                    document.getElementById('orders-canceled').innerText = `● ${data.orders.canceled} Cancelados`;

                    // Atualiza Tabela Iugu
                    const tInvoices = document.getElementById('table-invoices');
                    if (data.latest_invoices.length > 0) {
                        tInvoices.innerHTML = data.latest_invoices.map(inv => {
                            let badgeClass = "bg-amber-50 text-amber-700 border-amber-100";
                            if (inv.status.toLowerCase() === 'paid') badgeClass = "bg-emerald-50 text-emerald-700 border-emerald-100";
                            if (inv.status.toLowerCase() === 'canceled' || inv.status.toLowerCase() === 'canceled') badgeClass = "bg-rose-50 text-rose-700 border-rose-100";
                            
                            return `
                                <tr class="hover:bg-slate-50 transition-colors">
                                    <td class="px-6 py-4 font-mono text-xs font-semibold text-slate-600">${inv.invoice_id}</td>
                                    <td class="px-6 py-4 text-slate-500">${inv.subscription_id || 'N/A'}</td>
                                    <td class="px-6 py-4">
                                        <span class="px-2.5 py-1 rounded-full text-xs font-semibold border ${badgeClass}">${inv.status.toUpperCase()}</span>
                                    </td>
                                    <td class="px-6 py-4 text-slate-400 text-xs">${new Date(inv.updated_at).toLocaleString('pt-BR')}</td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        tInvoices.innerHTML = `<tr><td colspan="4" class="px-6 py-8 text-center text-slate-400">Nenhuma fatura recebida ainda.</td></tr>`;
                    }

                    // Atualiza Tabela Mercado Livre
                    const tOrders = document.getElementById('table-orders');
                    if (data.latest_orders.length > 0) {
                        tOrders.innerHTML = data.latest_orders.map(ord => {
                            let badgeClass = "bg-slate-50 text-slate-700 border-slate-100";
                            if (ord.status.toLowerCase() === 'paid' || ord.status.toLowerCase() === 'approved') badgeClass = "bg-emerald-50 text-emerald-700 border-emerald-100";
                            if (ord.status.toLowerCase() === 'cancelled' || ord.status.toLowerCase() === 'canceled') badgeClass = "bg-rose-50 text-rose-700 border-rose-100";

                            return `
                                <tr class="hover:bg-slate-50 transition-colors">
                                    <td class="px-6 py-4 font-mono text-xs font-semibold text-slate-600">${ord.order_id}</td>
                                    <td class="px-6 py-4 text-slate-500">${ord.seller_id || 'N/A'}</td>
                                    <td class="px-6 py-4">
                                        <span class="px-2.5 py-1 rounded-full text-xs font-semibold border ${badgeClass}">${ord.status.toUpperCase()}</span>
                                    </td>
                                    <td class="px-6 py-4 text-slate-400 text-xs">${new Date(ord.updated_at).toLocaleString('pt-BR')}</td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        tOrders.innerHTML = `<tr><td colspan="4" class="px-6 py-8 text-center text-slate-400">Nenhum pedido recebido ainda.</td></tr>`;
                    }

                } catch (error) {
                    console.error('Erro na atualização do painel:', error);
                }
            }

            // Atualiza imediatamente e define o intervalo de 5 segundos
            updateDashboard();
            setInterval(updateDashboard, 5000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# ---------------------------------------------------------------------------
# Mercado Livre OAuth + Stock
# ---------------------------------------------------------------------------
@app.get("/oauth/meli")
def meli_oauth():
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    url = f"https://auth.mercadolivre.com.br/authorization?{urlencode(params)}"
    return RedirectResponse(url)

@app.get("/oauth/meli/callback")
async def meli_oauth_callback(code: str, state: str):
    token_url = "https://api.mercadolibre.com/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
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
    cur.execute("""
        INSERT INTO meli_tokens (seller_id, access_token, refresh_token, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(seller_id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at,
            updated_at=excluded.updated_at
    """, (seller_id, access_token, refresh_token, expires_at, now_utc(), now_utc()))
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
    payload = {
        "grant_type": "refresh_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "refresh_token": refresh_token,
    }
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
    cur.execute("""
        UPDATE meli_tokens SET access_token=?, refresh_token=?, expires_at=?, updated_at=?
        WHERE seller_id=?
    """, (new_access, new_refresh, expires_at, now_utc(), seller_id))
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
    url = f"https://api.mercadolivre.com/items/{item_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"available_quantity": quantity}
    async with httpx.AsyncClient() as client:
        resp = await client.put(url, json=body, headers=headers)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()

class MeliWebhookPayload(BaseModel):
    resource: str | None = None
    topic: str | None = None
    user_id: int | None = None
    application_id: int | None = None
    sent: str | None = None

@app.post("/webhooks/meli")
async def meli_webhook(request: Request):
    body = await request.body()
    try:
        payload = MeliWebhookPayload.parse_raw(body)
    except Exception:
        payload = None
    resource = payload.resource if payload else None
    topic = payload.topic if payload else None
    seller_id = str(payload.user_id) if payload and payload.user_id else None
    if topic == "orders_v2" and resource:
        order_id = resource.split("/")[-1]
        await fetch_and_store_meli_order(order_id, seller_id)
    return {"status": "ok"}

async def fetch_and_store_meli_order(order_id: str, seller_id: str | None):
    if not seller_id:
        return
    token = await get_meli_token(seller_id)
    url = f"https://api.mercadolibre.com/orders/{order_id}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        return
    data = resp.json()
    status = data.get("status", "")
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO meli_orders (order_id, seller_id, status, payload, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            status=excluded.status,
            payload=excluded.payload,
            updated_at=excluded.updated_at
    """, (order_id, seller_id, status, resp.text, now_utc(), now_utc()))
    conn.commit()
    conn.close()

# --------------------------
# Iugu Webhook + HMAC
# --------------------------
def verify_iugu_signature(payload: bytes, signature: str) -> bool:
    expected = hmac.new(
        IUGU_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

class IuguWebhookPayload(BaseModel):
    event: str | None = None
    data: dict | None = None

@app.post("/webhooks/iugu")
async def iugu_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("x-iugu-signature", "")
    if not verify_iugu_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = IuguWebhookPayload.parse_raw(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event = payload.event or ""
    data = payload.data or {}
    invoice_id = str(data.get("id", "")) if data else ""
    subscription_id = str(data.get("subscription_id", "")) if data else ""
    status = str(data.get("status", "")) if data else ""
    company_id = subscription_id
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO companies (company_id, active, created_at, updated_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(company_id) DO UPDATE SET
            updated_at=excluded.updated_at
    """, (company_id, now_utc(), now_utc()))
    cur.execute("""
        INSERT INTO iugu_invoices (invoice_id, subscription_id, status, company_id, payload, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(invoice_id) DO UPDATE SET
            status=excluded.status,
            payload=excluded.payload,
            updated_at=excluded.updated_at
    """, (invoice_id, subscription_id, status, company_id, body.decode(), now_utc(), now_utc()))
    if event.lower() in ("invoice.payment_failed", "invoice.canceled", "subscription.suspended"):
        cur.execute("""
            UPDATE companies SET active=0, blocked_at=?, updated_at=?
            WHERE company_id=?
        """, (now_utc(), now_utc(), company_id))
    elif event.lower() in ("invoice.paid", "subscription.activated", "subscription.renewed"):
        cur.execute("""
            UPDATE companies SET active=1, blocked_at=NULL, updated_at=?
            WHERE company_id=?
        """, (now_utc(), company_id))
    conn.commit()
    conn.close()
    return {"status": "processed"}

@app.get("/health")
def health():
    return {"status": "ok", "time": now_utc()}

# =======================================================================
# NOVOS MODELOS PYDANTIC PARA O ERP DO CLIENTE
# =======================================================================
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

# =======================================================================
# =======================================================================
# NOVAS ROTAS: AUTENTICAÇÃO DO CLIENTE FINAL
# =======================================================================
@app.post("/auth/register")
def register(user: UserAuth):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)",
            (user.email, user.password, user.name, now_utc())
        )
        conn.commit()
        return {"message": "Usuário registrado com sucesso!"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="E-mail já cadastrado.")
    finally:
        conn.close()

@app.post("/auth/login")
def login(user: UserAuth):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    row = cur.execute("SELECT id, email, name FROM users WHERE email=? AND password=?", (user.email, user.password)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")
    return {"id": row[0], "email": row[1], "name": row[2]}

# =======================================================================
# NOVAS ROTAS: CONTROLE DE ESTOQUE E SINCRONIZAÇÃO MERCADO LIVRE
# =======================================================================
@app.get("/products")
def list_products():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/products")
def create_product(product: ProductSchema):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO products (sku, name, price, stock, meli_item_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (product.sku, product.name, product.price, product.stock, product.meli_item_id, now_utc(), now_utc())
        )
        conn.commit()
        return {"message": "Produto cadastrado com sucesso!"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="SKU já cadastrado.")
    finally:
        conn.close()

@app.put("/products/{sku}/stock")
async def update_product_stock(sku: str, payload: StockUpdateSchema):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    product = cur.execute("SELECT meli_item_id FROM products WHERE sku=?", (sku,)).fetchone()
    
    if not product:
        conn.close()
        raise HTTPException(status_code=404, detail="Produto não encontrado.")
    
    # Atualiza o estoque local no banco de dados
    cur.execute("UPDATE products SET stock=?, updated_at=? WHERE sku=?", (payload.quantity, now_utc(), sku))
    conn.commit()
    conn.close()
    
    # Se o produto possuir um ID de anúncio do Mercado Livre vinculado, sincroniza em tempo real!
    meli_item_id = product[0]
    if meli_item_id:
        try:
            await update_meli_stock(meli_item_id, payload.seller_id, payload.quantity)
            return {"status": "success", "message": "Estoque atualizado localmente e sincronizado com o Mercado Livre!"}
        except Exception as e:
            return {"status": "partial", "message": f"Estoque atualizado localmente, mas falhou ao sincronizar com o ML: {str(e)}"}
            
    return {"status": "success", "message": "Estoque local atualizado com sucesso!"}
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)