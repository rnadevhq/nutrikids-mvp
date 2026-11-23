import os
import json
import sqlite3
from typing import List, Optional, Callable, Any
from models import ChildProfile, ProductData, HistoryRecord

DB_NAME = "nutrikids.db"

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

USE_POSTGRES = DATABASE_URL.startswith("postgresql://")

_pg_pool = None

if USE_POSTGRES:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool


def _get_pool():
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
    return _pg_pool


def _q(query: str) -> str:
    """Adapta placeholders '?' de SQLite a '%s' de PostgreSQL."""
    return query.replace("?", "%s") if USE_POSTGRES else query


def _execute_with_retry(operation: Callable[[Any], Any]) -> Any:
    """Reutiliza conexiones del pool y reintenta si Neon cerró una conexión inactiva."""
    if not USE_POSTGRES:
        conn = sqlite3.connect(DB_NAME)
        try:
            result = operation(conn)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    pool = _get_pool()
    last_error = None
    for attempt in range(2):
        conn = pool.getconn()
        broken = False
        try:
            if conn.closed:
                broken = True
                raise psycopg2.InterfaceError("Conexión cerrada por el servidor")
            result = operation(conn)
            conn.commit()
            return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            broken = True
            last_error = e
            if attempt == 0:
                continue
            raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                pool.putconn(conn, close=broken or bool(conn.closed))
            except Exception:
                pass
    raise last_error


def init_db():
    """Inicializa tablas, columnas de aislamiento por dispositivo e índices."""
    def _op(conn):
        cursor = conn.cursor()
        pk_auto = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"

        # 1. Tabla de Perfiles (aislada por device_id)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS profiles (
                id {pk_auto},
                device_id TEXT NOT NULL DEFAULT 'legacy',
                name TEXT NOT NULL,
                age_years INTEGER NOT NULL,
                allergies TEXT NOT NULL,
                avoid_sweeteners BOOLEAN NOT NULL,
                avoid_artificial_colors BOOLEAN NOT NULL
            )
        """)

        # 2. Tabla de Catálogo de Productos (compartida globalmente)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS products (
                barcode TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brand TEXT NOT NULL,
                category TEXT NOT NULL,
                ingredients_text TEXT NOT NULL,
                allergens_tags TEXT NOT NULL,
                sugars_100g REAL NOT NULL,
                sodium_mg_100g REAL NOT NULL,
                saturated_fat_100g REAL NOT NULL,
                serving_size_g REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 3. Tabla de Historial (aislada por device_id)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS scan_history (
                id {pk_auto},
                device_id TEXT NOT NULL DEFAULT 'legacy',
                profile_id INTEGER NOT NULL,
                profile_name TEXT NOT NULL,
                barcode TEXT NOT NULL,
                product_name TEXT NOT NULL,
                brand TEXT NOT NULL,
                traffic_light TEXT NOT NULL,
                summary_reason TEXT NOT NULL,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migraciones seguras sobre tablas existentes
        if USE_POSTGRES:
            cursor.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS device_id TEXT NOT NULL DEFAULT 'legacy'")
            cursor.execute("ALTER TABLE scan_history ADD COLUMN IF NOT EXISTS device_id TEXT NOT NULL DEFAULT 'legacy'")
            cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'")
            cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            for col_sql in [
                "ALTER TABLE profiles ADD COLUMN device_id TEXT NOT NULL DEFAULT 'legacy'",
                "ALTER TABLE scan_history ADD COLUMN device_id TEXT NOT NULL DEFAULT 'legacy'",
                "ALTER TABLE products ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
                "ALTER TABLE products ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT FALSE",
            ]:
                try:
                    cursor.execute(col_sql)
                except sqlite3.OperationalError:
                    pass

        # Índices para consultas rápidas al escalar
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_profiles_device_id ON profiles (device_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_device_profile ON scan_history (device_id, profile_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_cat_verified ON products (category, is_verified)")

    _execute_with_retry(_op)


# --- CRUD DE PERFILES (AISLADO POR DEVICE_ID) ---
def get_all_profiles(device_id: str) -> List[ChildProfile]:
    def _op(conn):
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                SELECT id, name, age_years, allergies, avoid_sweeteners, avoid_artificial_colors
                FROM profiles
                WHERE device_id = ?
                ORDER BY id ASC
            """),
            (device_id,),
        )
        rows = cursor.fetchall()
        return [
            ChildProfile(
                id=r[0],
                name=r[1],
                age_years=r[2],
                allergies=json.loads(r[3]),
                avoid_sweeteners=bool(r[4]),
                avoid_artificial_colors=bool(r[5]),
            )
            for r in rows
        ]

    return _execute_with_retry(_op)


def save_profile(profile: ChildProfile, device_id: str) -> ChildProfile:
    def _op(conn):
        cursor = conn.cursor()
        allergies_json = json.dumps(profile.allergies)
        if profile.id is None:
            if USE_POSTGRES:
                cursor.execute(
                    """
                    INSERT INTO profiles (device_id, name, age_years, allergies, avoid_sweeteners, avoid_artificial_colors)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        device_id,
                        profile.name,
                        profile.age_years,
                        allergies_json,
                        bool(profile.avoid_sweeteners),
                        bool(profile.avoid_artificial_colors),
                    ),
                )
                profile.id = cursor.fetchone()[0]
            else:
                cursor.execute(
                    """
                    INSERT INTO profiles (device_id, name, age_years, allergies, avoid_sweeteners, avoid_artificial_colors)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        profile.name,
                        profile.age_years,
                        allergies_json,
                        bool(profile.avoid_sweeteners),
                        bool(profile.avoid_artificial_colors),
                    ),
                )
                profile.id = cursor.lastrowid
        else:
            cursor.execute(
                _q("""
                    UPDATE profiles
                    SET name = ?, age_years = ?, allergies = ?, avoid_sweeteners = ?, avoid_artificial_colors = ?
                    WHERE id = ? AND device_id = ?
                """),
                (
                    profile.name,
                    profile.age_years,
                    allergies_json,
                    bool(profile.avoid_sweeteners),
                    bool(profile.avoid_artificial_colors),
                    profile.id,
                    device_id,
                ),
            )
        return profile

    return _execute_with_retry(_op)


def delete_profile(profile_id: int, device_id: str):
    def _op(conn):
        cursor = conn.cursor()
        cursor.execute(
            _q("DELETE FROM scan_history WHERE profile_id = ? AND device_id = ?"),
            (profile_id, device_id),
        )
        cursor.execute(
            _q("DELETE FROM profiles WHERE id = ? AND device_id = ?"),
            (profile_id, device_id),
        )

    _execute_with_retry(_op)


# --- CRUD DE PRODUCTOS Y CATÁLOGO (GLOBAL) ---
PRODUCT_SELECT_COLS = """
    barcode, name, brand, category, ingredients_text, allergens_tags,
    sugars_100g, sodium_mg_100g, saturated_fat_100g, serving_size_g,
    source, is_verified
"""


def _row_to_product(r) -> ProductData:
    return ProductData(
        barcode=r[0],
        name=r[1],
        brand=r[2],
        category=r[3],
        ingredients_text=r[4],
        allergens_tags=json.loads(r[5]),
        sugars_100g=float(r[6]),
        sodium_mg_100g=float(r[7]),
        saturated_fat_100g=float(r[8]),
        serving_size_g=float(r[9]),
        source=r[10] if len(r) > 10 and r[10] else "manual",
        is_verified=bool(r[11]) if len(r) > 11 else False,
    )


def save_product_to_db(product: ProductData):
    def _op(conn):
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                INSERT INTO products (
                    barcode, name, brand, category, ingredients_text, allergens_tags,
                    sugars_100g, sodium_mg_100g, saturated_fat_100g, serving_size_g,
                    source, is_verified, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (barcode) DO UPDATE SET
                    name = EXCLUDED.name,
                    brand = EXCLUDED.brand,
                    category = EXCLUDED.category,
                    ingredients_text = EXCLUDED.ingredients_text,
                    allergens_tags = EXCLUDED.allergens_tags,
                    sugars_100g = EXCLUDED.sugars_100g,
                    sodium_mg_100g = EXCLUDED.sodium_mg_100g,
                    saturated_fat_100g = EXCLUDED.saturated_fat_100g,
                    serving_size_g = EXCLUDED.serving_size_g,
                    source = EXCLUDED.source,
                    is_verified = EXCLUDED.is_verified,
                    updated_at = CURRENT_TIMESTAMP
            """),
            (
                product.barcode,
                product.name,
                product.brand or "Marca genérica",
                product.category,
                product.ingredients_text,
                json.dumps(product.allergens_tags),
                product.sugars_100g,
                product.sodium_mg_100g,
                product.saturated_fat_100g,
                product.serving_size_g,
                product.source or "manual",
                bool(product.is_verified),
            ),
        )

    _execute_with_retry(_op)


def get_product_from_db(barcode: str) -> Optional[ProductData]:
    def _op(conn):
        cursor = conn.cursor()
        cursor.execute(
            _q(f"SELECT {PRODUCT_SELECT_COLS} FROM products WHERE barcode = ?"),
            (barcode,),
        )
        row = cursor.fetchone()
        return _row_to_product(row) if row else None

    return _execute_with_retry(_op)


def get_all_products_from_db() -> List[ProductData]:
    def _op(conn):
        cursor = conn.cursor()
        cursor.execute(f"SELECT {PRODUCT_SELECT_COLS} FROM products ORDER BY category, name ASC")
        rows = cursor.fetchall()
        return [_row_to_product(r) for r in rows]

    return _execute_with_retry(_op)


def search_products_in_db(query: str) -> List[ProductData]:
    def _op(conn):
        cursor = conn.cursor()
        q = f"%{query.strip()}%"
        cursor.execute(
            _q(f"""
                SELECT {PRODUCT_SELECT_COLS}
                FROM products
                WHERE LOWER(name) LIKE LOWER(?)
                   OR LOWER(brand) LIKE LOWER(?)
                   OR LOWER(barcode) LIKE LOWER(?)
                ORDER BY is_verified DESC, name ASC
            """),
            (q, q, q),
        )
        rows = cursor.fetchall()
        return [_row_to_product(r) for r in rows]

    return _execute_with_retry(_op)


def get_products_by_category_from_db(
    category: str,
    exclude_barcode: str,
    only_verified: bool = True,
) -> List[ProductData]:
    def _op(conn):
        cursor = conn.cursor()
        if only_verified:
            cursor.execute(
                _q(f"""
                    SELECT {PRODUCT_SELECT_COLS}
                    FROM products
                    WHERE category = ? AND barcode != ? AND is_verified = ?
                """),
                (category, exclude_barcode, True),
            )
        else:
            cursor.execute(
                _q(f"""
                    SELECT {PRODUCT_SELECT_COLS}
                    FROM products
                    WHERE category = ? AND barcode != ?
                """),
                (category, exclude_barcode),
            )
        rows = cursor.fetchall()
        return [_row_to_product(r) for r in rows]

    return _execute_with_retry(_op)


# --- CRUD DE HISTORIAL (AISLADO POR DEVICE_ID) ---
def add_scan_history(
    profile: ChildProfile,
    product: ProductData,
    traffic_light: str,
    summary_reason: str,
    device_id: str,
):
    def _op(conn):
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                INSERT INTO scan_history (
                    device_id, profile_id, profile_name, barcode, product_name, brand, traffic_light, summary_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """),
            (
                device_id,
                profile.id or 0,
                profile.name,
                product.barcode,
                product.name,
                product.brand or "",
                traffic_light,
                summary_reason,
            ),
        )

    _execute_with_retry(_op)


def get_scan_history(
    device_id: str,
    profile_id: Optional[int] = None,
    limit: int = 30,
) -> List[HistoryRecord]:
    def _op(conn):
        cursor = conn.cursor()
        if profile_id is not None:
            cursor.execute(
                _q("""
                    SELECT id, profile_id, profile_name, barcode, product_name, brand, traffic_light, summary_reason, scanned_at
                    FROM scan_history
                    WHERE device_id = ? AND profile_id = ?
                    ORDER BY id DESC LIMIT ?
                """),
                (device_id, profile_id, limit),
            )
        else:
            cursor.execute(
                _q("""
                    SELECT id, profile_id, profile_name, barcode, product_name, brand, traffic_light, summary_reason, scanned_at
                    FROM scan_history
                    WHERE device_id = ?
                    ORDER BY id DESC LIMIT ?
                """),
                (device_id, limit),
            )
        rows = cursor.fetchall()
        return [
            HistoryRecord(
                id=r[0],
                profile_id=r[1],
                profile_name=r[2],
                barcode=r[3],
                product_name=r[4],
                brand=r[5],
                traffic_light=r[6],
                summary_reason=r[7],
                scanned_at=r[8].isoformat() if hasattr(r[8], "isoformat") else str(r[8]),
            )
            for r in rows
        ]

    return _execute_with_retry(_op)


def clear_scan_history(device_id: str, profile_id: Optional[int] = None):
    def _op(conn):
        cursor = conn.cursor()
        if profile_id is not None:
            cursor.execute(
                _q("DELETE FROM scan_history WHERE device_id = ? AND profile_id = ?"),
                (device_id, profile_id),
            )
        else:
            cursor.execute(
                _q("DELETE FROM scan_history WHERE device_id = ?"),
                (device_id,),
            )

    _execute_with_retry(_op)