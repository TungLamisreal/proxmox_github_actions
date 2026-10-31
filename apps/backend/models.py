"""SQLAlchemy source of truth for the versioned MeowCloud PostgreSQL schema."""
from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Integer, MetaData,
    Numeric, String, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    metadata = MetaData()

class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_price_positive"),
        CheckConstraint("stock >= 0", name="ck_products_stock_positive"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price = mapped_column(Numeric(12, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_orders_unit_price_positive"),
        CheckConstraint("status IN ('pending','paid','cancelled')", name="ck_orders_status_valid"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
