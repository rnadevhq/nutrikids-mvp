import os
import json
import sqlite3
from typing import List, Optional
from models import ChildProfile, ProductData, HistoryRecord

DB_NAME = "nutrikids.db"

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if RAW_DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

USE_POSTGRES = DATABASE_URL.startswith("postgresql://")

if USE_POSTGRES:
    import psycopg2


def get_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DB_NAME)


def _q(query: str) -> str:
    """Adapta placeholders '?' de SQLite a '%s' de PostgreSQL."""
    return query.replace("?", "%s") if USE_POSTGRES else query


def init_db():
    """Inicializa las 3 tablas relacionales sin datos ficticios."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        pk_auto = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"

        # 1. Tabla de Perfiles
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS profiles (
                id {pk_auto},
                name TEXT NOT NULL,
                age_years INTEGER NOT NULL,
                allergies TEXT NOT NULL,
                avoid_sweeteners BOOLEAN NOT NULL,
                avoid_artificial_colors BOOLEAN NOT NULL
            )
        """)

        # 2. Tabla de Catálogo de Productos
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

        # Migración segura si ya existía la tabla sin las columnas nuevas
        if USE_POSTGRES:
            cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'")
            cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            for col_sql in [
                "ALTER TABLE products ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
                "ALTER TABLE products ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT FALSE",
            ]:
                try:
                    cursor.execute(col_sql)
                except sqlite3.OperationalError:
                    pass

        # 3. Tabla de Historial de Escaneos / Evaluaciones
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS scan_history (
                id {pk_auto},
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

        conn.commit()
    finally:
        conn.close()


# --- CRUD DE PERFILES ---
def get_all_profiles() -> List[ChildProfile]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, age_years, allergies, avoid_sweeteners, avoid_artificial_colors
            FROM profiles ORDER BY id ASC
        """)
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
    finally:
        conn.close()


def save_profile(profile: ChildProfile) -> ChildProfile:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        allergies_json = json.dumps(profile.allergies)
        if profile.id is None:
            if USE_POSTGRES:
                cursor.execute(
                    """
                    INSERT INTO profiles (name, age_years, allergies, avoid_sweeteners, avoid_artificial_colors)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
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
                    INSERT INTO profiles (name, age_years, allergies, avoid_sweeteners, avoid_artificial_colors)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
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
                    WHERE id = ?
                """),
                (
                    profile.name,
                    profile.age_years,
                    allergies_json,
                    bool(profile.avoid_sweeteners),
                    bool(profile.avoid_artificial_colors),
                    profile.id,
                ),
            )
        conn.commit()
        return profile
    finally:
        conn.close()


def delete_profile(profile_id: int):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(_q("DELETE FROM profiles WHERE id = ?"), (profile_id,))
        conn.commit()
    finally:
        conn.close()


# --- CRUD DE PRODUCTOS Y CATÁLOGO ---
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
    """Guarda o actualiza un producto en la base de datos."""
    conn = get_connection()
    try:
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
        conn.commit()
    finally:
        conn.close()


def get_product_from_db(barcode: str) -> Optional[ProductData]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q(f"SELECT {PRODUCT_SELECT_COLS} FROM products WHERE barcode = ?"),
            (barcode,),
        )
        row = cursor.fetchone()
        return _row_to_product(row) if row else None
    finally:
        conn.close()


def get_all_products_from_db() -> List[ProductData]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT {PRODUCT_SELECT_COLS} FROM products ORDER BY category, name ASC")
        rows = cursor.fetchall()
        return [_row_to_product(r) for r in rows]
    finally:
        conn.close()


def search_products_in_db(query: str) -> List[ProductData]:
    """Busca productos por coincidencia insensible a mayúsculas en nombre, marca o EAN."""
    conn = get_connection()
    try:
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
    finally:
        conn.close()


def get_products_by_category_from_db(
    category: str,
    exclude_barcode: str,
    only_verified: bool = True,
) -> List[ProductData]:
    """Obtiene candidatos de la misma categoría; por defecto solo verificados para recomendaciones seguras."""
    conn = get_connection()
    try:
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
    finally:
        conn.close()


# --- CRUD DE HISTORIAL DE EVALUACIONES ---
def add_scan_history(profile: ChildProfile, product: ProductData, traffic_light: str, summary_reason: str):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                INSERT INTO scan_history (profile_id, profile_name, barcode, product_name, brand, traffic_light, summary_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """),
            (
                profile.id or 0,
                profile.name,
                product.barcode,
                product.name,
                product.brand or "",
                traffic_light,
                summary_reason,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_scan_history(profile_id: Optional[int] = None, limit: int = 30) -> List[HistoryRecord]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if profile_id is not None:
            cursor.execute(
                _q("""
                    SELECT id, profile_id, profile_name, barcode, product_name, brand, traffic_light, summary_reason, scanned_at
                    FROM scan_history WHERE profile_id = ? ORDER BY id DESC LIMIT ?
                """),
                (profile_id, limit),
            )
        else:
            cursor.execute(
                _q("""
                    SELECT id, profile_id, profile_name, barcode, product_name, brand, traffic_light, summary_reason, scanned_at
                    FROM scan_history ORDER BY id DESC LIMIT ?
                """),
                (limit,),
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
    finally:
        conn.close()


def clear_scan_history(profile_id: Optional[int] = None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if profile_id is not None:
            cursor.execute(_q("DELETE FROM scan_history WHERE profile_id = ?"), (profile_id,))
        else:
            cursor.execute("DELETE FROM scan_history")
        conn.commit()
    finally:
        conn.close()