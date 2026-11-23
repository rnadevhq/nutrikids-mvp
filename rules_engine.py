import re
from typing import List
from models import ChildProfile, ProductData, RiskFactor, EvaluationResponse, AlternativeProduct
from off_service import get_candidate_products_by_category

SWEETENERS_DB = {
    "sucralosa": {"codes": ["955"], "desc": "Edulcorante artificial (INS 955). Desaconsejado en niños porque altera el umbral de dulzor y la microbiota intestinal."},
    "aspartamo": {"codes": ["951"], "desc": "Edulcorante artificial (INS 951). No recomendado en dieta pediátrica."},
    "acesulfamo": {"codes": ["950"], "desc": "Edulcorante sintético (INS 950). Desaconsejado en menores."},
    "stevia": {"codes": ["960", "960a", "960b"], "desc": "Glicósidos de esteviol (INS 960). Evitar en la infancia temprana para no elevar el umbral de dulzor."},
    "sacarina": {"codes": ["954"], "desc": "Edulcorante sintético (INS 954). Evitar en niños."},
    "ciclamato": {"codes": ["952"], "desc": "Edulcorante artificial (INS 952) restringido en población infantil."}
}

ARTIFICIAL_COLORS_DB = {
    "tartrazina": {"codes": ["102"], "desc": "Colorante azoico (Amarillo 5 / INS 102). Vinculado a hiperactividad infantil y cuadros alérgicos."},
    "amarillo crepusculo": {"codes": ["110"], "desc": "Colorante sintético (Amarillo 6 / INS 110). Asociado a hiperactividad en niños."},
    "rojo allura": {"codes": ["129"], "desc": "Colorante sintético (Rojo 40 / INS 129). Asociado a déficit de atención e hiperactividad."},
    "rojo 40": {"codes": ["129"], "desc": "Colorante sintético (INS 129). Se recomienda evitar en niños."},
    "azul brillante": {"codes": ["133"], "desc": "Colorante sintético (Azul 1 / INS 133). Sin valor nutricional."},
    "ponceau": {"codes": ["124"], "desc": "Colorante sintético rojo (INS 124). Desaconsejado en niños."},
    "carmoisina": {"codes": ["122"], "desc": "Colorante sintético (Azorrubina / INS 122). Asociado a hiperactividad."}
}

OTHER_ADDITIVES_DB = {
    "jarabe de maiz de alta fructosa": {"codes": [], "sev": "ALTO", "desc": "Azúcar industrial ultraprocesado asociado a resistencia a la insulina e hígado graso infantil."},
    "bht": {"codes": ["321"], "sev": "MEDIO", "desc": "Antioxidante sintético (Hidroxitolueno butilado / INS 321). Se aconseja limitar en productos infantiles."},
    "benzoato de sodio": {"codes": ["211"], "sev": "MEDIO", "desc": "Conservante sintético (INS 211). En combinación con colorantes artificiales se asocia a hiperactividad."}
}

STIMULANTS_DB = {
    "cafeina": "Estimulante del sistema nervioso central. Contraindicado en niños.",
    "guarana": "Fuente concentrada de cafeína. No apto para menores.",
    "taurina": "Compuesto estimulante de bebidas energéticas. No apto para menores."
}

ALLERGEN_KEYWORDS = {
    "leche": ["leche", "lactosa", "suero de leche", "caseina", "caseinato", "mantequilla", "crema de leche", "milk", "whey"],
    "gluten": ["trigo", "cebada", "centeno", "gluten", "avena", "wheat", "malt"],
    "soya": ["soya", "soja", "lecitina de soya", "proteina de soya", "soy"],
    "mani": ["mani", "cacahuete", "peanut", "pasta de mani"]
}

CATEGORY_BUYING_TIPS = {
    "jugos_bebidas": [
        "Prefiere agua pura o infusiones de fruta natural sin azúcar ni edulcorantes (sucralosa, stevia o acesulfamo).",
        "Evita bebidas con cafeína o colorantes sintéticos (INS 102, 110, 129, 133)."
    ],
    "galletas_snacks": [
        "Busca listas cortas de ingredientes (avena, arroz inflado o harina integral como primer ingrediente).",
        "Elige opciones con menos de 5g de azúcar por porción y sin jarabe de maíz ni colorantes artificiales."
    ],
    "lacteos": [
        "Prefiere yogur natural sin endulzar (solo leche y fermentos) y endúlzalo en casa con fruta fresca.",
        "Revisa que no contenga edulcorantes artificiales ocultos en versiones 'light' o 'sin azúcar añadida'."
    ],
    "cereales": [
        "Elige cereales integrales inflados (avena, quínoa, arroz o maíz natural) sin azúcar ni miel añadida.",
        "Evita cereales de colores llamativos: suelen combinar exceso de azúcar, sodio y colorantes sintéticos."
    ],
    "compotas_frutas": [
        "Busca compotas con 100% fruta natural y vitamina C como único antioxidante.",
        "Descarta pouches que agreguen azúcar, almidones modificados o edulcorantes como stevia/sucralosa."
    ],
    "general": [
        "Prioriza alimentos sin sellos de advertencia y con listas de ingredientes cortas y reconocibles.",
        "Evita productos con jarabe de maíz de alta fructosa, edulcorantes artificiales o colorantes sintéticos."
    ],
}


def get_daily_limits(age: int) -> dict:
    if age < 2:
        return {"max_sugar_g": 3.0, "max_sodium_mg": 800.0, "max_sat_fat_g": 8.0}
    elif 2 <= age <= 5:
        return {"max_sugar_g": 15.0, "max_sodium_mg": 1200.0, "max_sat_fat_g": 12.0}
    elif 6 <= age <= 11:
        return {"max_sugar_g": 20.0, "max_sodium_mg": 1500.0, "max_sat_fat_g": 16.0}
    else:
        return {"max_sugar_g": 25.0, "max_sodium_mg": 2000.0, "max_sat_fat_g": 20.0}


def normalize_text(text: str) -> str:
    replacements = str.maketrans("áéíóúü", "aeiouu")
    return text.lower().translate(replacements).replace("-", " ")


def check_additive_presence(ingredients_text: str, name: str, codes: List[str]) -> bool:
    patterns = [rf"\b{re.escape(name)}\b"]
    for code in codes:
        patterns.append(rf"\b(?:ins|e|sin)\s*{code}\b")
    combined_pattern = "|".join(patterns)
    return bool(re.search(combined_pattern, ingredients_text))


def calculate_product_score(
    profile: ChildProfile,
    risks: List[RiskFactor],
    traffic_light: str,
    sugar_per_serving: float,
    sugar_pct: float,
    sodium_pct: float,
    sat_fat_pct: float,
) -> int:
    """Calcula un índice nutricional transparente de 0 a 100 coherente con el semáforo."""
    # Veto absoluto por alérgeno declarado en el perfil
    if any(r.category == "Alérgeno Crítico" for r in risks):
        return 0

    score = 100.0

    # Penalización nutricional proporcional al límite diario por edad
    if profile.age_years < 2 and sugar_per_serving > 3.5:
        score -= 35.0
    else:
        score -= min(35.0, sugar_pct * 0.55)

    score -= min(20.0, sodium_pct * 0.45)
    score -= min(15.0, sat_fat_pct * 0.30)

    # Penalización por estimulantes y aditivos críticos
    for r in risks:
        if r.category == "Estimulante":
            score -= 35.0
        elif r.category == "Edulcorante No Calórico":
            score -= 20.0 if r.severity == "ALTO" else 12.0
        elif r.category == "Colorante Sintético":
            score -= 20.0 if r.severity == "ALTO" else 10.0
        elif r.category == "Aditivo Industrial":
            score -= 25.0 if r.severity == "ALTO" else 10.0

    raw_score = int(round(max(0.0, min(100.0, score))))

    # Garantizar coherencia estricta entre puntaje y color del semáforo
    if traffic_light == "VERDE":
        return max(75, min(100, raw_score))
    elif traffic_light == "AMARILLO":
        return max(45, min(74, raw_score))
    else:
        return max(5, min(44, raw_score))


def find_healthy_alternatives(
    original_product: ProductData,
    profile: ChildProfile,
    original_score: int,
) -> List[AlternativeProduct]:
    candidates = get_candidate_products_by_category(original_product.category, original_product.barcode)
    recommended = []

    for candidate in candidates:
        eval_res = evaluate_product(candidate, profile, include_alternatives=False)
        if eval_res.traffic_light == "VERDE":
            reasons = ["Sin aditivos críticos ni alérgenos para su perfil"]
            if eval_res.daily_sugar_percentage < 20:
                reasons.append(f"bajo aporte de azúcar ({eval_res.daily_sugar_percentage}% del límite diario)")

            recommended.append(AlternativeProduct(
                barcode=candidate.barcode,
                name=candidate.name,
                brand=candidate.brand or "Marca genérica",
                serving_size_g=candidate.serving_size_g,
                daily_sugar_percentage=eval_res.daily_sugar_percentage,
                daily_sodium_percentage=eval_res.daily_sodium_percentage,
                score=eval_res.score,
                score_diff=max(0, eval_res.score - original_score),
                why_is_better=", ".join(reasons) + "."
            ))

    recommended.sort(key=lambda x: (-x.score, x.daily_sugar_percentage))
    return recommended[:3]


def build_shareable_report(
    product: ProductData,
    profile: ChildProfile,
    traffic_light: str,
    score: int,
    summary_title: str,
    summary_reason: str,
    risks: List[RiskFactor],
    alternatives: List[AlternativeProduct]
) -> str:
    """Construye un resumen formateado listo para enviar por WhatsApp o correo."""
    icon = {"ROJO": "🔴", "AMARILLO": "🟡", "VERDE": "🟢"}.get(traffic_light, "⚪")
    lines = [
        f"🍏 *Reporte IngrediKnow para {profile.name} ({profile.age_years} años)*",
        f"📦 *Producto:* {product.name} ({product.brand})",
        f"🚦 *Resultado:* {icon} *{summary_title.upper()}* — ⭐ *{score}/100 pts*",
        f"📝 *Motivo:* {summary_reason}"
    ]
    if risks:
        lines.append("\n⚠️ *Ingredientes/Nutrientes de Riesgo:*")
        for r in risks:
            lines.append(f"• *{r.item_detected}* ({r.category}): {r.explanation}")
    if alternatives:
        lines.append("\n💡 *Alternativas Saludables (🟢 Verde):*")
        for alt in alternatives:
            lines.append(f"• {alt.name} ({alt.brand}) — ⭐ {alt.score}/100 (+{alt.score_diff} pts) | Azúcar: {alt.daily_sugar_percentage}% cuota diaria.")
    return "\n".join(lines)


def evaluate_product(product: ProductData, profile: ChildProfile, include_alternatives: bool = True) -> EvaluationResponse:
    risks = []
    clean_ingredients = normalize_text(product.ingredients_text)

    # 1. FILTRO DE ALÉRGENOS
    for allergy in profile.allergies:
        allergy_norm = normalize_text(allergy)
        keywords = ALLERGEN_KEYWORDS.get(allergy_norm, [allergy_norm])
        for kw in keywords:
            if re.search(rf"\b{kw}\b", clean_ingredients) or any(kw in normalize_text(tag) for tag in product.allergens_tags):
                risks.append(RiskFactor(
                    category="Alérgeno Crítico",
                    item_detected=allergy.capitalize(),
                    severity="ALTO",
                    explanation=f"Contiene o declara trazas de {allergy} (restringido en el perfil de {profile.name})."
                ))
                break

    # 2. ESTIMULANTES Y ADITIVOS CRÍTICOS (Nombres + Códigos INS/E)
    for stim, desc in STIMULANTS_DB.items():
        if stim in clean_ingredients:
            risks.append(RiskFactor(category="Estimulante", item_detected=stim.capitalize(), severity="ALTO", explanation=desc))

    for sweet, data in SWEETENERS_DB.items():
        if check_additive_presence(clean_ingredients, sweet, data["codes"]):
            sev = "ALTO" if profile.age_years <= 3 or profile.avoid_sweeteners else "MEDIO"
            item_name = f"{sweet.capitalize()} (INS {data['codes'][0]})"
            risks.append(RiskFactor(category="Edulcorante No Calórico", item_detected=item_name, severity=sev, explanation=data["desc"]))

    seen_colors = set()
    for color, data in ARTIFICIAL_COLORS_DB.items():
        code_id = data["codes"][0]
        if code_id not in seen_colors and check_additive_presence(clean_ingredients, color, data["codes"]):
            seen_colors.add(code_id)
            sev = "ALTO" if profile.avoid_artificial_colors else "MEDIO"
            item_name = f"{color.capitalize()} (INS {code_id})"
            risks.append(RiskFactor(category="Colorante Sintético", item_detected=item_name, severity=sev, explanation=data["desc"]))

    for add_name, data in OTHER_ADDITIVES_DB.items():
        if check_additive_presence(clean_ingredients, add_name, data["codes"]):
            code_str = f" (INS {data['codes'][0]})" if data["codes"] else ""
            risks.append(RiskFactor(
                category="Aditivo Industrial",
                item_detected=f"{add_name.capitalize()}{code_str}",
                severity=data["sev"],
                explanation=data["desc"]
            ))

    # 3. UMBRALES CUANTITATIVOS POR PORCIÓN
    limits = get_daily_limits(profile.age_years)
    factor_porcion = product.serving_size_g / 100.0
    sugar_per_serving = round(product.sugars_100g * factor_porcion, 1)
    sodium_per_serving = round(product.sodium_mg_100g * factor_porcion, 1)
    sat_fat_per_serving = round(product.saturated_fat_100g * factor_porcion, 1)

    sugar_pct = round((sugar_per_serving / limits["max_sugar_g"]) * 100, 1)
    sodium_pct = round((sodium_per_serving / limits["max_sodium_mg"]) * 100, 1)
    sat_fat_pct = round((sat_fat_per_serving / limits["max_sat_fat_g"]) * 100, 1)

    # Azúcares
    if profile.age_years < 2 and sugar_per_serving > 3.5:
        risks.append(RiskFactor(category="Exceso Nutricional", item_detected=f"Azúcares ({sugar_per_serving}g)", severity="ALTO", explanation="Menores de 2 años deben evitar azúcares libres o concentrados."))
    elif sugar_pct >= 50:
        risks.append(RiskFactor(category="Exceso Nutricional", item_detected=f"Azúcares ({sugar_per_serving}g por porción)", severity="ALTO", explanation=f"Cubre el {sugar_pct}% del límite diario recomendado para {profile.age_years} años ({limits['max_sugar_g']}g máx)."))
    elif sugar_pct >= 25:
        risks.append(RiskFactor(category="Alerta Nutricional", item_detected=f"Azúcares ({sugar_per_serving}g por porción)", severity="MEDIO", explanation=f"Aporta el {sugar_pct}% del máximo diario recomendado."))

    # Sodio
    if sodium_pct >= 40:
        risks.append(RiskFactor(category="Exceso Nutricional", item_detected=f"Sodio ({sodium_per_serving}mg por porción)", severity="ALTO", explanation=f"Cubre el {sodium_pct}% del sodio máximo diario para su edad."))
    elif sodium_pct >= 20:
        risks.append(RiskFactor(category="Alerta Nutricional", item_detected=f"Sodio ({sodium_per_serving}mg por porción)", severity="MEDIO", explanation=f"Aporte moderado-alto de sodio ({sodium_pct}% del límite diario)."))

    # Grasas Saturadas
    if sat_fat_pct >= 40:
        risks.append(RiskFactor(category="Exceso Nutricional", item_detected=f"Grasas Saturadas ({sat_fat_per_serving}g)", severity="ALTO", explanation=f"Cubre el {sat_fat_pct}% del límite diario de grasas saturadas."))

    # 4. SEMÁFORO GENERAL Y PUNTAJE (0-100)
    high_risks = [r for r in risks if r.severity == "ALTO"]
    med_risks = [r for r in risks if r.severity == "MEDIO"]

    if high_risks:
        traffic_light, summary_title = "ROJO", "No Recomendado"
        summary_reason = f"Riesgo elevado por presencia de: {', '.join([r.item_detected for r in high_risks[:2]])}."
    elif med_risks:
        traffic_light, summary_title = "AMARILLO", "Consumo Ocasional"
        summary_reason = f"Moderar consumo. Se detectó: {', '.join([r.item_detected for r in med_risks[:2]])}."
    else:
        traffic_light, summary_title = "VERDE", "Producto Apto"
        summary_reason = f"Sin aditivos críticos ni excesos nutricionales para la edad de {profile.name}."

    score = calculate_product_score(
        profile=profile,
        risks=risks,
        traffic_light=traffic_light,
        sugar_per_serving=sugar_per_serving,
        sugar_pct=sugar_pct,
        sodium_pct=sodium_pct,
        sat_fat_pct=sat_fat_pct,
    )

    alternatives = []
    category_tips = []
    if include_alternatives and traffic_light in ["ROJO", "AMARILLO"]:
        alternatives = find_healthy_alternatives(product, profile, original_score=score)
        if not alternatives:
            category_tips = CATEGORY_BUYING_TIPS.get(product.category, CATEGORY_BUYING_TIPS["general"])

    share_text = ""
    if include_alternatives:
        share_text = build_shareable_report(
            product, profile, traffic_light, score, summary_title, summary_reason, risks, alternatives
        )

    return EvaluationResponse(
        barcode=product.barcode,
        product_name=product.name,
        brand=product.brand or "Marca genérica",
        category=product.category,
        traffic_light=traffic_light,
        score=score,
        summary_title=summary_title,
        summary_reason=summary_reason,
        risks=risks,
        daily_sugar_percentage=min(sugar_pct, 100.0),
        daily_sodium_percentage=min(sodium_pct, 100.0),
        saturated_fat_per_serving=sat_fat_per_serving,
        alternatives=alternatives,
        category_tips=category_tips,
        shareable_text=share_text
    )