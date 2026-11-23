from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from models import ChildProfile, ProductData, EvaluationResponse, HistoryRecord
from off_service import fetch_product_by_barcode
from rules_engine import evaluate_product
from database import (
    USE_POSTGRES,
    init_db,
    get_all_profiles,
    save_profile,
    delete_profile,
    get_product_from_db,
    get_all_products_from_db,
    search_products_in_db,
    save_product_to_db,
    add_scan_history,
    get_scan_history,
    clear_scan_history,
)

init_db()

app = FastAPI(
    title="IngrediKnow API",
    description="Backend para evaluación nutricional pediátrica, catálogo persistente e historial.",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    barcode: str
    profile: ChildProfile


class ManualEvaluationRequest(BaseModel):
    product: ProductData
    profile: ChildProfile
    save_to_catalog: bool = False


def _is_valid_for_cache(product: ProductData) -> bool:
    """Evita contaminar la BD con fichas vacías de OpenFoodFacts."""
    has_name = bool(product.name and product.name.strip() and product.name.strip().lower() != "producto desconocido")
    has_data = bool(
        product.ingredients_text.strip()
        or product.sugars_100g > 0
        or product.sodium_mg_100g > 0
        or product.saturated_fat_100g > 0
    )
    return has_name and has_data


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "database": "postgresql" if USE_POSTGRES else "sqlite",
    }


@app.get("/profiles", response_model=List[ChildProfile])
def api_get_profiles():
    return get_all_profiles()


@app.post("/profiles", response_model=ChildProfile)
def api_save_profile(profile: ChildProfile):
    return save_profile(profile)


@app.delete("/profiles/{profile_id}")
def api_delete_profile(profile_id: int):
    delete_profile(profile_id)
    return {"status": "deleted", "profile_id": profile_id}


@app.get("/products", response_model=List[ProductData])
def api_list_products(q: Optional[str] = None):
    if q:
        return search_products_in_db(q)
    return get_all_products_from_db()


@app.post("/products", response_model=ProductData)
def api_add_product(product: ProductData):
    save_product_to_db(product)
    return product


@app.get("/history", response_model=List[HistoryRecord])
def api_get_history(profile_id: Optional[int] = None):
    return get_scan_history(profile_id)


@app.delete("/history")
def api_clear_history(profile_id: Optional[int] = None):
    clear_scan_history(profile_id)
    return {"status": "cleared", "profile_id": profile_id}


@app.post("/evaluate/barcode", response_model=EvaluationResponse)
def evaluate_by_barcode(request: ScanRequest):
    barcode = request.barcode.strip()
    product = get_product_from_db(barcode)

    if not product:
        product = fetch_product_by_barcode(barcode)
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        if _is_valid_for_cache(product):
            product.source = "openfoodfacts"
            product.is_verified = False
            try:
                save_product_to_db(product)
            except Exception as e:
                print(f"[AutoCache Warning] No se pudo guardar {barcode}: {e}")

    result = evaluate_product(product, request.profile)
    add_scan_history(request.profile, product, result.traffic_light, result.summary_reason)
    return result


@app.post("/evaluate/manual", response_model=EvaluationResponse)
def evaluate_manually(request: ManualEvaluationRequest):
    if request.save_to_catalog:
        request.product.source = request.product.source or "manual"
        save_product_to_db(request.product)
    result = evaluate_product(request.product, request.profile)
    add_scan_history(request.profile, request.product, result.traffic_light, result.summary_reason)
    return result