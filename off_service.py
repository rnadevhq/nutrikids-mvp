import requests
import re
from typing import Optional, List
from models import ProductData
from database import get_products_by_category_from_db

CATEGORY_LABELS = {
    "jugos_bebidas": "🧃 Jugos y Bebidas",
    "galletas_snacks": "🍪 Galletas y Colaciones",
    "lacteos": "🥛 Lácteos y Postres Vegetales",
    "cereales": "🥣 Cereales de Desayuno",
    "compotas_frutas": "🍎 Compotas y Frutas",
    "general": "🛒 Alimento General",
}


def parse_serving_size(serving_str: str) -> float:
    if not serving_str:
        return 100.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:g|ml|gr)", serving_str.lower())
    return float(match.group(1)) if match else 100.0


def infer_category(name: str, categories_tags: List[str]) -> str:
    text = (name + " " + " ".join(categories_tags)).lower()
    if any(k in text for k in ["jugo", "nectar", "néctar", "bebida", "refresco", "agua", "isotonic", "juice"]):
        return "jugos_bebidas"
    if any(k in text for k in ["cereal", "avena", "quinoa", "quínoa", "granola", "hojuela", "flakes"]):
        return "cereales"
    if any(k in text for k in ["galleta", "barra", "snack", "biscuit", "cookie", "oblea"]):
        return "galletas_snacks"
    if any(k in text for k in ["yogur", "leche", "postre", "lacteo", "lácteo", "dairy", "milk", "yogurt", "queso"]):
        return "lacteos"
    if any(k in text for k in ["compota", "pure", "puré", "fruta", "pouch", "baby"]):
        return "compotas_frutas"
    return "general"


def get_candidate_products_by_category(category: str, exclude_barcode: str) -> List[ProductData]:
    """Devuelve únicamente productos verificados de la misma categoría para recomendar con seguridad."""
    return get_products_by_category_from_db(category, exclude_barcode, only_verified=True)


def fetch_product_by_barcode(barcode: str) -> Optional[ProductData]:
    """Consulta OpenFoodFacts y retorna el producto normalizado (el caché persistente lo gestiona main.py)."""
    clean_code = barcode.strip()
    url = f"https://world.openfoodfacts.org/api/v2/product/{clean_code}.json"
    headers = {"User-Agent": "IngrediKnowApp/2.1 (Chile)"}

    try:
        response = requests.get(url, headers=headers, timeout=6)
        if response.status_code != 200:
            return None

        data = response.json()
        if data.get("status") != 1:
            return None

        prod = data.get("product", {})
        nutriments = prod.get("nutriments", {})

        sodium_g = float(nutriments.get("sodium_100g", 0.0) or 0.0)
        sodium_mg = sodium_g * 1000.0

        prod_name = (
            prod.get("product_name_es")
            or prod.get("product_name")
            or f"Producto {clean_code}"
        ).strip()
        brand = (prod.get("brands") or "").strip() or "Marca no especificada"
        cat_tags = prod.get("categories_tags", [])

        return ProductData(
            barcode=clean_code,
            name=prod_name,
            brand=brand,
            category=infer_category(prod_name, cat_tags),
            ingredients_text=prod.get("ingredients_text_es") or prod.get("ingredients_text") or "",
            allergens_tags=prod.get("allergens_tags", []),
            sugars_100g=float(nutriments.get("sugars_100g", 0.0) or 0.0),
            sodium_mg_100g=sodium_mg,
            saturated_fat_100g=float(nutriments.get("saturated-fat_100g", 0.0) or 0.0),
            serving_size_g=parse_serving_size(prod.get("serving_size", "100g")),
            source="openfoodfacts",
            is_verified=False,
        )

    except Exception as e:
        print(f"Aviso: No se pudo conectar con Open Food Facts ({e})")
        return None