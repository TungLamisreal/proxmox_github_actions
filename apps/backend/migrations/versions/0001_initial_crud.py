"""Create isolated MeowCloud CRUD schema v1.

Revision ID: 0001_initial_crud
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_crud"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("category_id", sa.BigInteger(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("price >= 0", name="ck_products_price_positive"),
        sa.CheckConstraint("stock >= 0", name="ck_products_stock_positive"),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_orders_unit_price_positive"),
        sa.CheckConstraint("status IN ('pending','paid','cancelled')", name="ck_orders_status_valid"),
    )
    # Seed only reference categories; never seed private customer/order data.
    op.execute(sa.text("""
        INSERT INTO categories (name) VALUES
          ('Điện tử'), ('Phụ kiện'), ('Gia dụng')
        ON CONFLICT (name) DO NOTHING
    """))


def downgrade():
    # Never drop tables or user data automatically in an infrastructure demo.
    raise RuntimeError("Destructive schema downgrade disabled; restore from verified backup if needed")
