"""MeowCloud CRUD demo API (PostgreSQL). Preserve /health and /version contracts."""
import hmac
import os
import socket
from decimal import Decimal, InvalidOperation
from functools import wraps
from contextlib import closing

import psycopg2
from psycopg2 import errors
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32768
VERSION = os.getenv('APP_VERSION', 'dev')
EDITION = 'crud-v2'
DB_HOST = os.getenv('DB_HOST', '')
DB_NAME = os.getenv('DB_NAME', '')
DB_USER = os.getenv('DB_USER', '')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DEMO_TOKEN = os.getenv('DEMO_TOKEN', '')

# Identifiers and allowed fields are entirely server-defined, never user-controlled.
TABLES = {
    'categories': {'fields': ('name',), 'required': ('name',)},
    'products': {'fields': ('category_id', 'sku', 'name', 'price', 'stock'),
                 'required': ('category_id', 'sku', 'name', 'price', 'stock')},
    'customers': {'fields': ('name', 'email', 'phone'), 'required': ('name', 'email')},
    'orders': {'fields': ('customer_id', 'product_id', 'quantity', 'status'),
               'required': ('customer_id', 'product_id', 'quantity', 'status')},
}


def db_connection():
    if not all((DB_HOST, DB_NAME, DB_USER, DB_PASSWORD)):
        raise RuntimeError('Database not configured')
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD,
                            connect_timeout=3, application_name='meowcloud-crud')


def require_demo_token(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        provided = request.headers.get('X-Demo-Token', '')
        if not DEMO_TOKEN or not hmac.compare_digest(provided, DEMO_TOKEN):
            return jsonify(error='Mã truy cập không hợp lệ'), 401
        return fn(*args, **kwargs)
    return wrapped


def body_for(table):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Body phải là JSON object')
    fields = TABLES[table]['fields']
    if set(data) - set(fields):
        raise ValueError('Có trường dữ liệu không được hỗ trợ')
    if any(field not in data for field in TABLES[table]['required']):
        raise ValueError('Thiếu trường bắt buộc')
    result = {}
    for key in fields:
        if key not in data:
            continue
        value = data[key]
        if key in ('name', 'sku', 'email', 'phone', 'status'):
            if value is None and key == 'phone':
                value = ''
            if not isinstance(value, str) or (key != 'phone' and not value.strip()):
                raise ValueError(f'{key} phải là chuỗi hợp lệ')
            value = value.strip()
            if len(value) > (255 if key in ('name', 'email') else 64):
                raise ValueError(f'{key} quá dài')
            if key == 'status' and value not in ('pending', 'paid', 'cancelled'):
                raise ValueError('status phải là pending, paid hoặc cancelled')
            if key == 'email' and ('@' not in value or len(value) < 5):
                raise ValueError('Email không hợp lệ')
        elif key in ('category_id', 'customer_id', 'product_id', 'stock', 'quantity'):
            if type(value) is not int or value < (0 if key == 'stock' else 1) or value > 1_000_000_000:
                raise ValueError(f'{key} phải là số nguyên hợp lệ')
        elif key == 'price':
            try:
                value = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError('Giá không hợp lệ') from None
            if not value.is_finite() or value < 0 or value > 999999999 or value.as_tuple().exponent < -2:
                raise ValueError('Giá phải từ 0 tới 999999999, tối đa 2 chữ số thập phân')
        result[key] = value
    if table == 'customers':
        result.setdefault('phone', '')
    return result


def query_rows(sql, params=(), *, one=False):
    with closing(db_connection()) as conn, conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return dict(cur.fetchone()) if one and cur.rowcount else (
                [dict(row) for row in cur.fetchall()] if not one else None)


@app.get('/')
@app.get('/health')
def health():
    # Intentionally independent of DB, so load balancer and autoscaling stay healthy.
    return jsonify(service='MeowCloud Backend API (Docker)', node=socket.gethostname(),
                   db_primary='192.168.150.71', status='UP')


@app.get('/version')
def version():
    return jsonify(service='MeowCloud Backend API (Docker)',
                   node=socket.gethostname(), version=VERSION, app_edition=EDITION)


@app.get('/db-health')
def db_health():
    try:
        row = query_rows('SELECT 1 AS ok', one=True)
        if row and row['ok'] == 1:
            return jsonify(status='UP', node=socket.gethostname())
    except (RuntimeError, psycopg2.Error):
        pass
    return jsonify(status='DOWN'), 503


@app.get('/stats')
@require_demo_token
def stats():
    with closing(db_connection()) as conn, conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT (SELECT count(*) FROM categories) AS categories, '
                        '(SELECT count(*) FROM products) AS products, '
                        '(SELECT count(*) FROM customers) AS customers, '
                        '(SELECT count(*) FROM orders) AS orders')
            return jsonify(dict(cur.fetchone()))


@app.get('/reports/low-stock')
@require_demo_token
def low_stock_report():
    """V2 inventory insight with no changes to the existing database schema."""
    raw = request.args.get('threshold', '5')
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 4:
        raise ValueError('Threshold must be an integer in range 0 to 1000')
    threshold = int(raw)
    if threshold > 1000:
        raise ValueError('Threshold must be an integer in range 0 to 1000')
    with closing(db_connection()) as conn, conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT count(*) AS total FROM products WHERE stock <= %s', (threshold,))
            total = cur.fetchone()['total']
            cur.execute(
                'SELECT p.id, p.sku, p.name, p.price, p.stock, c.name AS category_name '
                'FROM products p JOIN categories c ON c.id = p.category_id '
                'WHERE p.stock <= %s ORDER BY p.stock ASC, p.id DESC LIMIT 100',
                (threshold,),
            )
            return jsonify(threshold=threshold, total=total,
                           items=[dict(row) for row in cur.fetchall()])


@app.get('/<table>')
@require_demo_token
def list_records(table):
    if table not in TABLES:
        return jsonify(error='Bảng không tồn tại'), 404
    # Fixed table names from TABLES; user input never interpolates into SQL identifiers.
    return jsonify(query_rows(f'SELECT * FROM {table} ORDER BY id DESC LIMIT 200'))


@app.post('/<table>')
@require_demo_token
def create_record(table):
    if table not in TABLES:
        return jsonify(error='Bảng không tồn tại'), 404
    fields = body_for(table)
    with closing(db_connection()) as conn, conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if table == 'orders':
                cur.execute('SELECT price FROM products WHERE id = %s', (fields['product_id'],))
                product = cur.fetchone()
                if not product:
                    return jsonify(error='Sản phẩm không tồn tại'), 404
                fields['unit_price'] = product['price']
            columns = ', '.join(fields)
            placeholders = ', '.join(['%s'] * len(fields))
            cur.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING *',
                        tuple(fields.values()))
            return jsonify(dict(cur.fetchone())), 201


@app.put('/<table>/<int:record_id>')
@require_demo_token
def update_record(table, record_id):
    if table not in TABLES:
        return jsonify(error='Bảng không tồn tại'), 404
    fields = body_for(table)
    with closing(db_connection()) as conn, conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if table == 'orders':
                cur.execute('SELECT price FROM products WHERE id = %s', (fields['product_id'],))
                product = cur.fetchone()
                if not product:
                    return jsonify(error='Sản phẩm không tồn tại'), 404
                fields['unit_price'] = product['price']
            if table == 'products':
                fields['updated_at'] = 'now()'
            setters = ', '.join(f'{key} = %s' for key in fields if key != 'updated_at')
            if table == 'products':
                setters += ', updated_at = now()'
            values = [value for key, value in fields.items() if key != 'updated_at']
            cur.execute(f'UPDATE {table} SET {setters} WHERE id = %s RETURNING *',
                        (*values, record_id))
            record = cur.fetchone()
            if not record:
                return jsonify(error='Không tìm thấy bản ghi'), 404
            return jsonify(dict(record))


@app.delete('/<table>/<int:record_id>')
@require_demo_token
def delete_record(table, record_id):
    if table not in TABLES:
        return jsonify(error='Bảng không tồn tại'), 404
    with closing(db_connection()) as conn, conn:
        with conn.cursor() as cur:
            cur.execute(f'DELETE FROM {table} WHERE id = %s', (record_id,))
            if not cur.rowcount:
                return jsonify(error='Không tìm thấy bản ghi'), 404
    return ('', 204)


@app.errorhandler(ValueError)
def input_error(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(psycopg2.IntegrityError)
def integrity_error(exc):
    if isinstance(exc, errors.UniqueViolation):
        return jsonify(error='Dữ liệu bị trùng (SKU, email hoặc tên danh mục)'), 409
    if isinstance(exc, errors.ForeignKeyViolation):
        return jsonify(error='Bản ghi đang được tham chiếu hoặc khóa ngoại không tồn tại'), 409
    return jsonify(error='Dữ liệu vi phạm ràng buộc'), 400


@app.errorhandler(psycopg2.OperationalError)
@app.errorhandler(RuntimeError)
def database_unavailable(_):
    return jsonify(error='Database chưa sẵn sàng'), 503


@app.errorhandler(psycopg2.Error)
def unexpected_database_error(exc):
    app.logger.error('PostgreSQL error: %s', type(exc).__name__)
    return jsonify(error='Lỗi truy vấn cơ sở dữ liệu'), 500
