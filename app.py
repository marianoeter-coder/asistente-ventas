import os
import re
import json
import streamlit as st
import requests

# --------------------------
# CONFIG GENERAL
# --------------------------
st.set_page_config(page_title="Asistente de Ventas Big Dipper", layout="centered")

API_BASE = "https://www2.bigdipper.com.ar/api"  # según tu DevTools
TIMEOUT = 12

# --------------------------
# GEMINI (con fallback prolijo)
# --------------------------
def get_gemini_key():
    # 1) Streamlit secrets
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    # 2) variable de entorno (por si lo corrés local)
    return os.getenv("GEMINI_API_KEY", "")

GEMINI_API_KEY = get_gemini_key()

def call_gemini(prompt: str) -> str:
    """
    Llama a Gemini vía google-generativeai SOLO si hay API key.
    Si no hay key, devuelve un mensaje para configurar secrets.
    """
    if not GEMINI_API_KEY:
        return (
            "⚠️ No está configurada la API Key de Gemini.\n\n"
            "En Streamlit Cloud: **Manage app → Settings → Secrets** y agregá:\n"
            '`GEMINI_API_KEY = "TU_API_KEY"`'
        )

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        return model.generate_content(prompt).text
    except Exception as e:
        return f"⚠️ Error llamando a Gemini: {e}"

# --------------------------
# UTILIDADES
# --------------------------
MODEL_REGEX = r"[A-Z0-9]{2,}(?:[-_][A-Z0-9]{2,}){1,}"  # ej IPC-4M-FA-ZERO / XVR-AHD-410
VIEW_URL_REGEX = r"bigdipper\.com\.ar/products/view/(\d+)"

def extract_models(text: str):
    return list(dict.fromkeys(re.findall(MODEL_REGEX, text.upper())))

def extract_product_id_from_url(text: str):
    m = re.search(VIEW_URL_REGEX, text)
    return int(m.group(1)) if m else None

def safe_get_json(url, method="GET", **kwargs):
    try:
        if method == "POST":
            r = requests.post(url, timeout=TIMEOUT, **kwargs)
        else:
            r = requests.get(url, timeout=TIMEOUT, **kwargs)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

# --------------------------
# API BIG DIPPER
# --------------------------
def api_products_view(product_id: int):
    """
    Según DevTools:
    POST https://www2.bigdipper.com.ar/api/Products/View
    payload: {"ProductId": 6964}
    """
    url = f"{API_BASE}/Products/View"
    payload = {"ProductId": int(product_id)}
    return safe_get_json(url, method="POST", json=payload)

def api_try_search_code(code: str):
    """
    NO tenemos documentación pública del endpoint Search.
    Entonces probamos variantes comunes y nos quedamos con la primera que funcione.
    Si tu backend no expone alguno, simplemente falla y probamos el siguiente.
    """
    code = code.strip().upper()

    attempts = [
        # 1) GET con query param
        ("GET", f"{API_BASE}/Products/Search", {"params": {"search": code}}),
        ("GET", f"{API_BASE}/Products/Search", {"params": {"q": code}}),

        # 2) POST con payload simple
        ("POST", f"{API_BASE}/Products/Search", {"json": {"Search": code}}),
        ("POST", f"{API_BASE}/Products/Search", {"json": {"search": code}}),
        ("POST", f"{API_BASE}/Products/Search", {"json": {"query": code}}),

        # 3) endpoints alternativos típicos
        ("GET", f"{API_BASE}/Products/Find", {"params": {"code": code}}),
        ("POST", f"{API_BASE}/Products/Find", {"json": {"Code": code}}),
        ("GET", f"{API_BASE}/Products/GetByCode", {"params": {"code": code}}),
        ("POST", f"{API_BASE}/Products/GetByCode", {"json": {"code": code}}),
    ]

    for method, url, kwargs in attempts:
        data = safe_get_json(url, method=method, **kwargs)
        if not data:
            continue

        # Normalizamos posibles formatos:
        # - lista de productos
        # - dict con "Items"
        # - dict que ya es un producto
        if isinstance(data, list) and len(data) > 0:
            return data
        if isinstance(data, dict):
            if "Items" in data and isinstance(data["Items"], list) and data["Items"]:
                return data["Items"]
            # si parece producto
            if "Code" in data and "DescriptionLong" in data:
                return [data]

    return []

def choose_best_match(code: str, candidates: list):
    """
    Elegimos el candidato cuyo Code matchee más fuerte con el modelo pedido.
    """
    if not candidates:
        return None

    want = code.replace("_", "-").upper()

    # match exacto por Code
    for c in candidates:
        if str(c.get("Code", "")).upper() == want:
            return c

    # match normalizado (sin guiones)
    want_n = re.sub(r"[^A-Z0-9]", "", want)
    best = None
    best_score = -1

    for c in candidates:
        got = str(c.get("Code", "")).upper()
        got_n = re.sub(r"[^A-Z0-9]", "", got)
        score = 0
        if want_n in got_n or got_n in want_n:
            score += 10
        # bonus por tokens
        for token in re.findall(r"[A-Z0-9]{3,}", want_n):
            if token in got_n:
                score += 1
        if score > best_score:
            best_score = score
            best = c

    return best if best_score > 0 else (candidates[0] if candidates else None)

def resolve_product_from_model(code: str):
    """
    Intenta resolver un modelo (Code) a un producto completo.
    1) Busca candidatos por search
    2) elige el mejor
    3) si trae ProductId, hace View para traer ficha completa
    """
    candidates = api_try_search_code(code)
    best = choose_best_match(code, candidates)
    if not best:
        return None

    # Si el search devuelve ProductId o Id, traemos ficha completa
    pid = best.get("ProductId") or best.get("Id") or best.get("productId") or best.get("id")
    if pid:
        full = api_products_view(int(pid))
        return full if full else best

    # Si ya trae DescriptionLong y DataSheet, lo damos por válido
    if "DescriptionLong" in best and "DataSheet" in best:
        return best

    return best

# --------------------------
# PROMPT "MODO VENTAS"
# --------------------------
def build_sales_prompt(products: list, question: str) -> str:
    """
    Producto(s) vienen de API.
    La IA puede razonar compatibilidades pero SIEMPRE:
    - separar lo que está en ficha vs inferencia comercial
    - no inventar specs duras (voltaje exacto, estándares, etc.) si no está
    """
    ctx = []
    for p in products:
        ctx.append({
            "Code": p.get("Code"),
            "DescriptionShort": p.get("DescriptionShort"),
            "DescriptionLong": p.get("DescriptionLong"),
            "Stock": p.get("Stock"),
            "DataSheet": p.get("DataSheet"),
            "Links": p.get("Links", []),
        })

    return f"""
Actuás como ASESOR TÉCNICO COMERCIAL de Big Dipper (modo ventas).
Tu objetivo: ayudar al vendedor a responder rápido y bien.

REGLAS:
1) Prioridad total a la info de la ficha oficial (DescriptionLong / datasheet).
2) Si el cliente pregunta algo que no figura textual, podés INFERIR con criterio técnico comercial,
   pero marcá la inferencia con "👉 Interpretación comercial:".
3) No inventes especificaciones exactas si no están (ej: consumo exacto, tensión exacta, normas).
4) Si falta un dato crítico para asegurar compatibilidad, pedí UNA repregunta puntual.
5) Respondé en español argentino, directo, estilo vendedor técnico.

DATOS DISPONIBLES (JSON):
{json.dumps(ctx, ensure_ascii=False, indent=2)}

CONSULTA DEL VENDEDOR:
{question}

FORMATO DE RESPUESTA:
- Respuesta corta (2–4 líneas)
- Detalle (viñetas)
- Si aplica: Compatibilidad / recomendaciones
- Si aplica: 1 repregunta clave (máximo 1)
"""

# --------------------------
# UI CHAT (sin cartel azul)
# --------------------------
if "chat" not in st.session_state:
    st.session_state.chat = []

st.title("🤖 Asistente de Ventas Big Dipper")

# Mensaje inicial mínimo, no invasivo
st.caption("Escribí tu consulta como la haría un vendedor (incluí modelos si los tenés).")

# Mostrar historial
for role, msg in st.session_state.chat:
    st.chat_message(role).write(msg)

q = st.chat_input("Ej: La IPC-4M-FA-ZERO sirve para exterior y con qué XVR funciona?")

if q:
    st.session_state.chat.append(("user", q))
    st.chat_message("user").write(q)

    # 1) resolver por URL /products/view/<id> si viene
    pid = extract_product_id_from_url(q)
    products = []

    if pid:
        p = api_products_view(pid)
        if p:
            products.append(p)

    # 2) resolver por modelos en texto
    models = extract_models(q)

    # si viene un modelo que ya resolvimos por URL, igual lo dejamos
    for m in models:
        prod = resolve_product_from_model(m)
        if prod:
            # dedupe por Code
            code = str(prod.get("Code", "")).upper()
            if code and all(str(x.get("Code", "")).upper() != code for x in products):
                products.append(prod)

    # Si el usuario escribió "hola" sin modelos, contestamos humano y pedimos modelo
    if not products:
        low = q.strip().lower()
        if low in ["hola", "buenas", "buen día", "buen dia", "buenas!", "hola!"]:
            ans = "¡Buenas! Pasame el **modelo** (tal cual aparece en Big Dipper) y qué necesitás saber (compatibilidad, exterior, alimentación, etc.)."
            st.session_state.chat.append(("assistant", ans))
            st.chat_message("assistant").write(ans)
        else:
            ans = (
                "No pude identificar productos en tu consulta.\n\n"
                "👉 Pegame el/los **modelos exactos** (ej: `IPC-4M-FA-ZERO`) o una URL tipo "
                "`bigdipper.com.ar/products/view/6964`."
            )
            st.session_state.chat.append(("assistant", ans))
            st.chat_message("assistant").write(ans)
    else:
        # Mostramos qué detectó (útil para vendedor)
        detected = " | ".join([f"{p.get('Code')} (stock {p.get('Stock')})" for p in products])
        st.chat_message("assistant").write(f"🔎 Detecté: {detected}")

        prompt = build_sales_prompt(products, q)
        answer = call_gemini(prompt)

        st.session_state.chat.append(("assistant", answer))
        st.chat_message("assistant").write(answer)



