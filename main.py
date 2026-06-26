from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Numeric, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from pydantic import BaseModel, ConfigDict, Field
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import os
import requests
import urllib.parse

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

MERCADOPAGO_API_URL = "https://api.mercadopago.com"
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")

MERCADO_LIVRE_API_URL = "https://api.mercadolibre.com"
MERCADO_LIVRE_AUTH_URL = "https://auth.mercadolibre.com/authorization"
MERCADO_LIVRE_CLIENT_ID = os.getenv("MERCADO_LIVRE_CLIENT_ID")
MERCADO_LIVRE_CLIENT_SECRET = os.getenv("MERCADO_LIVRE_CLIENT_SECRET")
MERCADO_LIVRE_REDIRECT_URI = os.getenv("MERCADO_LIVRE_REDIRECT_URI")
MERCADO_LIVRE_BILLING_API_URL = os.getenv("MERCADO_LIVRE_BILLING_API_URL")

app = FastAPI(title="E-commerce Admin API", version="1.0.0")


# -----------------------------------------------------------------------------
# Database models
# -----------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    quantity = Column(Integer, default=0, nullable=False)
    category = Column(String, nullable=True)
    ml_item_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    total = Column(Numeric(10, 2), nullable=False)
    status = Column(String, default="pending", nullable=False)
    payment_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    product = relationship("Product")


class MlAuthToken(Base):
    __tablename__ = "ml_auth_tokens"

    id = Column(Integer, primary_key=True, index=True)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)


# -----------------------------------------------------------------------------
# Pydantic schemas
# -----------------------------------------------------------------------------
class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    quantity: int = Field(default=0, ge=0)
    category: Optional[str] = None
    ml_item_id: Optional[str] = None


class ProductCreate(ProductBase):
    pass


class ProductUpdateFull(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    quantity: Optional[int] = Field(default=None, ge=0)
    category: Optional[str] = None
    ml_item_id: Optional[str] = None


class ProductResponse(ProductBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)
    payer_email: str


class OrderResponse(BaseModel):
    id: int
    product_id: int
    quantity: int
    total: float
    status: str
    payment_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    order_id: int
    preference_id: str
    init_point: str
    sandbox_init_point: Optional[str] = None


class MlBillingCreateRequest(BaseModel):
    order_id: int
    buyer: Dict[str, Any]


class MlBillingResponse(BaseModel):
    success: bool
    data: Dict[str, Any]


# -----------------------------------------------------------------------------
# Dependency
# -----------------------------------------------------------------------------
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Mercado Pago helpers
# -----------------------------------------------------------------------------
def _mp_headers() -> Dict[str, str]:
    token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mercado Pago access token not configured",
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_mercadopago_preference(
    order: Order, payer_email: str
) -> Dict[str, Any]:
    product = order.product
    payload = {
        "items": [
            {
                "id": str(product.id),
                "title": product.name,
                "description": product.description or "",
                "quantity": order.quantity,
                "unit_price": float(product.price),
                "currency_id": "BRL",
            }
        ],
        "payer": {"email": payer_email},
        "external_reference": str(order.id),
        "back_urls": {
            "success": os.getenv("MP_BACK_SUCCESS_URL", "https://example.com/success"),
            "failure": os.getenv("MP_BACK_FAILURE_URL", "https://example.com/failure"),
            "pending": os.getenv("MP_BACK_PENDING_URL", "https://example.com/pending"),
        },
        "auto_return": "approved",
    }

    url = f"{MERCADOPAGO_API_URL}/v1/preferences"
    response = requests.post(url, headers=_mp_headers(), json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return {
        "preference_id": data["id"],
        "init_point": data["init_point"],
        "sandbox_init_point": data.get("sandbox_init_point"),
    }


# -----------------------------------------------------------------------------
# Mercado Livre helpers
# -----------------------------------------------------------------------------
def _ml_token_row(db: Session) -> Optional[MlAuthToken]:
    return db.query(MlAuthToken).order_by(MlAuthToken.id.desc()).first()


def _request_ml_token(payload: Dict[str, str]) -> Dict[str, Any]:
    client_id = os.getenv("MERCADO_LIVRE_CLIENT_ID")
    client_secret = os.getenv("MERCADO_LIVRE_CLIENT_SECRET")
    redirect_uri = os.getenv("MERCADO_LIVRE_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mercado Livre OAuth credentials not configured",
        )

    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    body.update(payload)

    url = f"{MERCADO_LIVRE_API_URL}/oauth/token"
    response = requests.post(url, data=body, timeout=30)
    response.raise_for_status()
    return response.json()


def store_ml_token(db: Session, token_data: Dict[str, Any]) -> MlAuthToken:
    expires_in = token_data.get("expires_in", 21600)
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    ml_token = MlAuthToken(
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        expires_at=expires_at,
    )
    db.add(ml_token)
    db.commit()
    db.refresh(ml_token)
    return ml_token


def refresh_ml_token(db: Session) -> MlAuthToken:
    current = _ml_token_row(db)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No Mercado Livre token found. Authorize first.",
        )

    token_data = _request_ml_token({"grant_type": "refresh_token", "refresh_token": current.refresh_token})
    return store_ml_token(db, token_data)


def get_valid_ml_token(db: Session) -> MlAuthToken:
    token = _ml_token_row(db)
    if not token or token.expires_at <= datetime.utcnow():
        token = refresh_ml_token(db)
    return token


def create_mercado_livre_billing_invoice(
    db: Session, order: Order, buyer: Dict[str, Any]
) -> Dict[str, Any]:
    if not MERCADO_LIVRE_BILLING_API_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mercado Livre billing API URL not configured",
        )

    token = get_valid_ml_token(db)
    unit_price = float(order.total) / order.quantity if order.quantity else float(order.total)

    payload = {
        "order_id": str(order.id),
        "items": [
            {
                "title": order.product.name,
                "quantity": order.quantity,
                "unit_price": round(unit_price, 2),
            }
        ],
        "buyer": buyer,
        "total": float(order.total),
    }

    url = f"{MERCADO_LIVRE_BILLING_API_URL}/invoices"
    headers = {
        "Authorization": f"Bearer {token.access_token}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


# -----------------------------------------------------------------------------
# Startup
# -----------------------------------------------------------------------------
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


# -----------------------------------------------------------------------------
# Product routes
# -----------------------------------------------------------------------------
@app.get("/products", response_model=List[ProductResponse])
def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    products = db.query(Product).offset(skip).limit(limit).all()
    return products


@app.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@app.put("/update-product/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    payload: ProductUpdateFull,
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@app.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(product)
    db.commit()
    return None


# -----------------------------------------------------------------------------
# Order routes
# -----------------------------------------------------------------------------
@app.post("/orders", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == payload.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.quantity < payload.quantity:
        raise HTTPException(status_code=400, detail="Insufficient product quantity")

    total = Decimal(str(product.price)) * payload.quantity
    order = Order(
        product_id=product.id,
        quantity=payload.quantity,
        total=total,
        status="pending",
    )
    product.quantity -= payload.quantity

    db.add(order)
    db.commit()
    db.refresh(order)

    preference = create_mercadopago_preference(order, payload.payer_email)
    order.payment_id = preference["preference_id"]
    db.commit()

    return PaymentResponse(
        order_id=order.id,
        preference_id=preference["preference_id"],
        init_point=preference["init_point"],
        sandbox_init_point=preference.get("sandbox_init_point"),
    )


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# -----------------------------------------------------------------------------
# Mercado Pago webhook
# -----------------------------------------------------------------------------
@app.post("/mercado-pago/webhook")
def mercado_pago_webhook(body: Dict[str, Any], db: Session = Depends(get_db)):
    external_reference = body.get("external_reference")
    payment_id = body.get("data", {}).get("id") or body.get("id")
    payment_status = body.get("status") or body.get("action")

    if external_reference:
        try:
            order_id = int(external_reference)
        except (ValueError, TypeError):
            order_id = None

        if order_id:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order:
                order.status = payment_status or "paid"
                if payment_id:
                    order.payment_id = str(payment_id)
                db.commit()

    return {"status": "received"}


# -----------------------------------------------------------------------------
# Mercado Livre routes
# -----------------------------------------------------------------------------
@app.get("/ml/auth")
def ml_auth():
    client_id = os.getenv("MERCADO_LIVRE_CLIENT_ID")
    redirect_uri = os.getenv("MERCADO_LIVRE_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mercado Livre OAuth credentials not configured",
        )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    auth_url = f"{MERCADO_LIVRE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": auth_url}


@app.get("/ml/callback")
def ml_callback(code: str, db: Session = Depends(get_db)):
    token_data = _request_ml_token({"grant_type": "authorization_code", "code": code})
    ml_token = store_ml_token(db, token_data)
    return {
        "access_token": ml_token.access_token,
        "expires_at": ml_token.expires_at,
    }


@app.post("/ml/refresh")
def ml_refresh(db: Session = Depends(get_db)):
    ml_token = refresh_ml_token(db)
    return {
        "access_token": ml_token.access_token,
        "expires_at": ml_token.expires_at,
    }


@app.post("/ml/billing/nfe", response_model=MlBillingResponse)
def create_ml_billing_invoice(
    payload: MlBillingCreateRequest, db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == payload.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    data = create_mercado_livre_billing_invoice(db, order, payload.buyer)
    return MlBillingResponse(success=True, data=data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))