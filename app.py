# app.py
# Asistente de Ventas Big Dipper (modo conversacional + memoria de último producto)
# - Detecta modelos y/o URLs de productos Big Dipper
# - Si el usuario pregunta sin modelo, usa el/los últimos productos cargados
# - Responde en "modo ventas" (razona con ficha + recomendaciones, sin inventar specs)
#
# Requisitos (requirements.txt) sugeridos:
# streamlit
# google-generativeai
# requests
# pypdf

import re
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import requests

# Google Gemini SDK
import google.generativeai as genai

# ----------------------------
# CONFIG
# ----------------------------
BASE = "https://www.bigdipper.com.ar"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# Modelos Gemini: podés cambiarlo
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"

# ----------------------------
# UTIL: API KEY (Streamlit Secrets)
# ----------------------------
def get_api_key() -> Optional[str]:
    # Acepta cualquiera de estas keys (por si cambiaste el nombre en Secrets)
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if k in st.secrets and st.secrets[k]:
            return str(st.secrets[k]).strip()
    return None


def get_gemini_model():
    api_key = get_api_key()
    if not api_key:
        return None
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(DEFAULT_GEMINI_MODEL)


# ----------------------------
# UTIL: extracción de URLs y modelos
# ----------------------------
PRODUCT_URL_RE = re.compile(r"(https?://(?:www\.)?bigdipper\.com\.ar/products/view/(\d+))", re.IGNORECASE)

# Modelo típico: mezcla letras/números con guiones (ej: IPC-4M-FA-ZERO, LM108-V2)
# Requisitos:
# - 2+ segmentos separados por "-"
# - al menos 1 dígito en todo el token
MODEL_RE = re.compile(
    r"\b([A-Z0-9]+(?:-[A-Z0-9]+)+)\b",
    re.IGNORECASE
)

def extract_urls(text: str) -> List[Tuple[str, int]]:
    """Devuelve lista (url, product_id)."""
    out = []
    for m in PRODUCT_URL_RE.finditer(text or ""):
        url = m.group(1)
        pid = int(m.group(2))
        out.append((url, pid))
    return out


def normalize_model(token: str) -> str:
    return token.strip().upper()


def extract_models(text: str) -> List[str]:
    """Extrae candidatos a modelo. Filtra falsos positivos."""
    if not text:
        return []
    candidates = [normalize_model(m.group(1)) for m in MODEL_RE.finditer(text)]
    cleaned = []
    for c in candidates:
        if len(c) < 6:
            continue
        if not any(ch.isdigit() for ch in c):
            continue
        # Evitar cosas tipo "HTTP-200" etc.
        if c.startswith("HTTP-") or c.startswith("HTTPS-"):
            continue
        cleaned.append(c)
    # Unique manteniendo orden
    seen = set()
    uniq = []
    for c in cleaned:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


# ----------------------------
# UTIL: acceso a ficha Big Dipper
# ----------------------------
def _requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def fetch_product_json_by_product_id(product_id: int, session: requests.Session) -> Optional[Dict[str, Any]]:
    """
    Intenta obtener el JSON del producto con varias rutas posibles.
    La web parece responder a un XHR 'View' con payload {ProductId: ####}.
    """
    endpoints = [
        f"{BASE}/Products/View",
        f"{BASE}/products/view",
        f"{BASE}/Products/ProductView",
        f"{BASE}/products/ProductView",
        f"{BASE}/api/Products/View",
        f"{BASE}/api/products/view",
        f"{BASE}/api/Product/View",
        f"{BASE}/api/product/view",
    ]

    payload = {"ProductId": int(product_id)}

    for ep in endpoints:
        try:
            r = session.post(ep, json=payload, timeout=12)
            if r.status_code != 200:
                continue
            # Algunos devuelven HTML, otros JSON
            ct = (r.headers.get("content-type") or "").lower()
            if "application/json" in ct:
                data = r.json()
            else:
                # Intentar parsear JSON si viene como texto
                txt = r.text.strip()
                if txt.startswith("{") and txt.endswith("}"):
                    data = json.loads(txt)
                else:
                    continue

            # Validar estructura esperada
            if isinstance(data, dict) and ("Code" in data or "ProductId" in data or "DescriptionLong" in data):
                return data
        except Exception:
            continue

    # Fallback: si no pudimos por endpoint, intentar scrapear la página y buscar el ProductId en JS
    try:
        page = session.get(f"{BASE}/products/view/{product_id}", timeout=12)
        if page.status_code == 200:
            # Buscar un JSON embebido si existiera
            # (esto es best-effort)
            m = re.search(r'("ProductId"\s*:\s*%d[^}]*})' % int(product_id), page.text)
            if m:
                try:
                    return json.loads("{" + m.group(1).split("{", 1)[-1])
                except Exception:
                    pass
    except Exception:
        pass

    return None


def fetch_product_json_by_code(code: str, session: requests.Session) -> Optional[Dict[str, Any]]:
    """
    No tenemos un endpoint oficial documentado para buscar por Code.
    Estrategia robusta:
    1) Intentar abrir una búsqueda interna simple (si existe).
    2) Fallback: pedir al usuario URL o JSON si no se puede.
    """
    # Intento 1: endpoints hipotéticos (si alguno existe, joya)
    endpoints = [
        f"{BASE}/Products/Search",
        f"{BASE}/products/search",
        f"{BASE}/api/Products/Search",
        f"{BASE}/api/products/search",
    ]
    payloads = [
        {"q": code},
        {"Query": code},
        {"text": code},
        {"Code": code},
    ]
    for ep in endpoints:
        for pl in payloads:
            try:
                r = session.post(ep, json=pl, timeout=10)
                if r.status_code != 200:
                    continue
                ct = (r.headers.get("content-type") or "").lower()
                if "application/json" not in ct:
                    continue
                data = r.json()
                # Si devuelve lista de productos, elegir el que matchee por Code
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and normalize_model(str(item.get("Code", ""))) == normalize_model(code):
                            # Si item ya es la ficha completa
                            if "DescriptionLong" in item:
                                return item
                            # Si solo trae ProductId
                            pid = item.get("ProductId") or item.get("Id")
                            if pid:
                                return fetch_product_json_by_product_id(int(pid), session)
                if isinstance(data, dict):
                    # Puede venir {results:[...]}
                    results = data.get("results") or data.get("Results") or data.get("data") or data.get("Data")
                    if isinstance(results, list):
                        for item in results:
                            if isinstance(item, dict) and normalize_model(str(item.get("Code", ""))) == normalize_model(code):
                                pid = item.get("ProductId") or item.get("Id")
                                if pid:
                                    return fetch_product_json_by_product_id(int(pid), session)
            except Exception:
                continue

    return None


def compact_product(product: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza campos relevantes y evita KeyErrors."""
    return {
        "ProductId": product.get("ProductId"),
        "Code": product.get("Code") or product.get("code"),
        "DescriptionShort": product.get("DescriptionShort") or "",
        "DescriptionLong": product.get("DescriptionLong") or "",
        "Price": product.get("Price"),
        "Stock": product.get("Stock"),
        "Image": product.get("Image"),
        "DataSheet": product.get("DataSheet"),
        "Links": product.get("Links") or [],
    }


# ----------------------------
# LLM: prompt modo ventas
# ----------------------------
SALES_SYSTEM = """Sos un Asistente Técnico-Comercial para vendedores de Big Dipper (Argentina).
Tu objetivo: ayudar a responder consultas de clientes rápido y bien, SIN inventar datos técnicos.

Reglas:
- Usá SIEMPRE la "ficha oficial" (DescriptionLong/Short, datasheet, links) como base.
- Si el usuario pregunta algo que NO está explícito en la ficha, NO lo afirmes como hecho.
  En ese caso, respondé como vendedor: "Recomendación / criterio práctico" + "qué habría que confirmar" + "pregunta corta al cliente".
- Si hay múltiples productos en la consulta, compará y respondé compatibilidad de forma prudente.
- Estilo: español argentino, claro, directo, orientado a cerrar venta (sin humo).
- Formato sugerido:
  1) Respuesta corta (sí/no o recomendación)
  2) Sustento con 2-6 bullets basados en ficha
  3) Si faltan datos: qué confirmar / disclaimer
  4) Próximo paso (pregunta al cliente o sugerencia de alternativa)
"""

def build_user_prompt(user_question: str, products: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append("CONSULTA DEL VENDEDOR/CLIENTE:")
    lines.append(user_question.strip())
    lines.append("")
    lines.append("FICHAS OFICIALES DISPONIBLES (NO INVENTAR FUERA DE ESTO):")
    for p in products:
        code = p.get("Code")
        pid = p.get("ProductId")
        lines.append(f"\n---\nProducto: {code} (ProductId: {pid})")
        lines.append(f"Descripción corta: {p.get('DescriptionShort','')}")
        lines.append("Descripción larga:")
        lines.append(p.get("DescriptionLong",""))
        ds = p.get("DataSheet")
        if ds:
            lines.append(f"Datasheet: {ds}")
        links = p.get("Links") or []
        if links:
            lines.append("Links:")
            for lk in links[:4]:
                lines.append(f"- {lk}")
    return "\n".join(lines)


def ask_gemini(model, user_question: str, products: List[Dict[str, Any]]) -> str:
    prompt = build_user_prompt(user_question, products)
    try:
        resp = model.generate_content(
            [
                {"role": "user", "parts": [SALES_SYSTEM]},
                {"role": "user", "parts": [prompt]},
            ],
            generation_config={
                "temperature": 0.4,
                "max_output_tokens": 650,
            },
        )
        text = (resp.text or "").strip()
        return text if text else "No pude generar una respuesta. Probá reformular la pregunta."
    except Exception as e:
        return f"Error al consultar Gemini: {e}"


# ----------------------------
# APP STATE
# ----------------------------
def init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "products_cache" not in st.session_state:
        # cache por Code y por ProductId
        st.session_state.products_cache = {"by_code": {}, "by_pid": {}}
    if "last_products" not in st.session_state:
        # lista de codes referenciados recientemente (en orden)
        st.session_state.last_products = []


def cache_product(prod: Dict[str, Any]):
    p = compact_product(prod)
    code = normalize_model(str(p.get("Code") or ""))
    pid = p.get("ProductId")
    if code:
        st.session_state.products_cache["by_code"][code] = p
    if pid is not None:
        st.session_state.products_cache["by_pid"][int(pid)] = p
    # actualizar last_products
    if code:
        # mover al frente (más reciente)
        lp = [c for c in st.session_state.last_products if c != code]
        lp.insert(0, code)
        st.session_state.last_products = lp[:5]  # guardar hasta 5


def get_cached_by_code(code: str) -> Optional[Dict[str, Any]]:
    return st.session_state.products_cache["by_code"].get(normalize_model(code))


def get_cached_by_pid(pid: int) -> Optional[Dict[str, Any]]:
    return st.session_state.products_cache["by_pid"].get(int(pid))


def get_last_products() -> List[Dict[str, Any]]:
    out = []
    for code in st.session_state.last_products:
        p = get_cached_by_code(code)
        if p:
            out.append(p)
    return out


# ----------------------------
# MAIN UI
# ----------------------------
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖", layout="centered")
init_state()

st.title("🤖 Asistente de Ventas Big Dipper")

with st.sidebar:
    debug = st.toggle("Mostrar debug (detector)", value=False)
    st.caption("Secrets: si usás Gemini, cargá `GEMINI_API_KEY` o `GOOGLE_API_KEY` en Streamlit Cloud.")
    if debug:
        st.write("Últimos productos:", st.session_state.last_products)

model = get_gemini_model()
if not model:
    st.warning("Falta API Key. Cargá `GEMINI_API_KEY` o `GOOGLE_API_KEY` en **Manage app → Settings → Secrets**.")

# Render chat
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

user_text = st.chat_input("Tu consulta… (podés poner modelo, URL y pregunta en una sola frase)")
if user_text:
    # User msg
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    # Assistant processing
    with st.chat_message("assistant"):
        with st.spinner("Analizando…"):
            session = _requests_session()

            urls = extract_urls(user_text)
            models = extract_models(user_text)

            if debug:
                st.write({"urls": urls, "models": models})

            products: List[Dict[str, Any]] = []

            # 1) Si hay URLs, priorizar y cargar por ProductId
            for url, pid in urls:
                cached = get_cached_by_pid(pid)
                if cached:
                    products.append(cached)
                    continue
                data = fetch_product_json_by_product_id(pid, session)
                if data:
                    cache_product(data)
                    products.append(compact_product(data))

            # 2) Si hay modelos, cargar por code (cache o best-effort search)
            for code in models:
                cached = get_cached_by_code(code)
                if cached:
                    products.append(cached)
                    continue
                data = fetch_product_json_by_code(code, session)
                if data:
                    cache_product(data)
                    products.append(compact_product(data))

            # 3) Si NO se detectó ningún producto, usar contexto (último producto cargado)
            if not products:
                last = get_last_products()
                if last:
                    products = last
                    if debug:
                        st.write("Usando contexto (últimos productos):", [p.get("Code") for p in products])

            # Deduplicar por Code
            dedup = {}
            for p in products:
                c = normalize_model(str(p.get("Code") or ""))
                if c and c not in dedup:
                    dedup[c] = p
            products = list(dedup.values())

            # Si aún no hay productos, pedir algo útil (sin cartel molesto azul)
            if not products:
                msg = (
                    "No pude enganchar ningún producto en tu consulta.\n\n"
                    "Pegame **una URL** tipo `bigdipper.com.ar/products/view/####` "
                    "o el **modelo exacto** (como figura en Big Dipper).\n\n"
                    "Tip: también sirve si pegás el **JSON** de la ficha (como hiciste antes)."
                )
                st.markdown(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            else:
                # Cachear todo lo detectado (para que el siguiente mensaje “se acuerde”)
                for p in products:
                    cache_product(p)

                # Responder con Gemini si hay API key; si no, responder “sin IA” usando ficha
                if model:
                    answer = ask_gemini(model, user_text, products)
                else:
                    # Respuesta fallback sin IA, basada en ficha
                    p = products[0]
                    answer = (
                        f"**{p.get('Code')}**\n\n"
                        f"- {p.get('DescriptionShort')}\n"
                        f"- Stock: {p.get('Stock')}\n"
                        f"- Precio: USD {p.get('Price')}\n\n"
                        "Si querés, decime **qué necesitás resolver** (uso, ambiente, distancia, compatibilidad) "
                        "y lo traduzco a una recomendación comercial."
                    )

                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})

                # Opcional: mostrar “fuentes” (ficha) en expander
                with st.expander("Ver datos oficiales usados (ficha)"):
                    for p in products:
                        st.write(f"**{p.get('Code')}**")
                        st.write(f"- ProductId: {p.get('ProductId')}")
                        st.write(f"- Stock: {p.get('Stock')}")
                        st.write(f"- Precio: {p.get('Price')}")
                        ds = p.get("DataSheet")
                        if ds:
                            st.write(f"- Datasheet: {ds}")
