from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
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
    description="Backend para evaluación nutricional pediátrica, catálogo persistente e historial aislado.",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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


def _clean_device_id(x_device_id: Optional[str]) -> str:
    if x_device_id and x_device_id.strip():
        return x_device_id.strip()[:120]
    return "legacy"


def _is_valid_for_cache(product: ProductData) -> bool:
    """Evita contaminar la BD con fichas vacías de OpenFoodFacts."""
    has_name = bool(
        product.name
        and product.name.strip()
        and product.name.strip().lower() != "producto desconocido"
    )
    has_data = bool(
        product.ingredients_text.strip()
        or product.sugars_100g > 0
        or product.sodium_mg_100g > 0
        or product.saturated_fat_100g > 0
    )
    return has_name and has_data


def _safe_save_product(product: ProductData):
    try:
        save_product_to_db(product)
    except Exception as e:
        print(f"[Background Cache Warning] No se pudo guardar {product.barcode}: {e}")


def _safe_add_history(
    profile: ChildProfile,
    product: ProductData,
    traffic_light: str,
    summary_reason: str,
    device_id: str,
):
    try:
        add_scan_history(profile, product, traffic_light, summary_reason, device_id)
    except Exception as e:
        print(f"[Background History Warning] No se pudo registrar historial: {e}")


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "database": "postgresql" if USE_POSTGRES else "sqlite",
    }


@app.get("/profiles", response_model=List[ChildProfile])
def api_get_profiles(x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID")):
    return get_all_profiles(_clean_device_id(x_device_id))


@app.post("/profiles", response_model=ChildProfile)
def api_save_profile(
    profile: ChildProfile,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
):
    return save_profile(profile, _clean_device_id(x_device_id))


@app.delete("/profiles/{profile_id}")
def api_delete_profile(
    profile_id: int,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
):
    delete_profile(profile_id, _clean_device_id(x_device_id))
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
def api_get_history(
    profile_id: Optional[int] = None,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
):
    return get_scan_history(_clean_device_id(x_device_id), profile_id)


@app.delete("/history")
def api_clear_history(
    profile_id: Optional[int] = None,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
):
    clear_scan_history(_clean_device_id(x_device_id), profile_id)
    return {"status": "cleared", "profile_id": profile_id}


@app.post("/evaluate/barcode", response_model=EvaluationResponse)
def evaluate_by_barcode(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
):
    device_id = _clean_device_id(x_device_id)
    barcode = request.barcode.strip()
    product = get_product_from_db(barcode)

    if not product:
        product = fetch_product_by_barcode(barcode)
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        if _is_valid_for_cache(product):
            product.source = "openfoodfacts"
            product.is_verified = False
            background_tasks.add_task(_safe_save_product, product)

    result = evaluate_product(product, request.profile)
    background_tasks.add_task(
        _safe_add_history,
        request.profile,
        product,
        result.traffic_light,
        result.summary_reason,
        device_id,
    )
    return result


@app.post("/evaluate/manual", response_model=EvaluationResponse)
def evaluate_manually(
    request: ManualEvaluationRequest,
    background_tasks: BackgroundTasks,
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
):
    device_id = _clean_device_id(x_device_id)
    if request.save_to_catalog:
        request.product.source = request.product.source or "manual"
        background_tasks.add_task(_safe_save_product, request.product)

    result = evaluate_product(request.product, request.profile)
    background_tasks.add_task(
        _safe_add_history,
        request.profile,
        request.product,
        result.traffic_light,
        result.summary_reason,
        device_id,
    )
    return result