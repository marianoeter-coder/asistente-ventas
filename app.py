# app.py — Asistente de Ventas Big Dipper (conversacional, detecta modelos/URLs, trae ficha oficial y responde “modo vendedor”)
# Reqs: streamlit, google-generativeai, requests, beautifulsoup4, pypdf

import re
import json
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st
import requests
from bs4 import BeautifulSoup

# Gemini (opcional)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

# PDF (opcional)
try:
    from pypdf import PdfReader
    import io
    PDF_AVAILABLE = True
except Exception:
    PDF_AVAILABLE = False


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖", layout="centered")

API_BASES = [
    "https://www2.bigdipper.com.ar/api",
    "https://www.bigdipper.com.ar/api",
]

WEB_BASES = [
    "https://www.bigdipper.com.ar",
    "https://www2.bigdipper.com.ar",
]

HTTP_TIMEOUT = 10

PDF_MAX_PAGES = 2
PDF_MAX_CHARS = 3000

# Detecta URL de producto con ID
RE_PRODUCT_URL_ID = re.compile(r"(?:bigdipper\.com\.ar\/products\/view\/)(\d+)", re.IGNORECASE)

# Detecta modelo tipo IPC-4M-FA-ZERO / DS-PDBG8-EG2 / etc
# Más permisivo: permite letras/números y guiones, exige al menos 2 guiones.
RE_MODEL = re.compile(r"\b[A-Z0-9]{2,}(?:-[A-Z0-9]{2,}){2,}\b", re.IGNORECASE)

# Para evitar falsos positivos de “IR” en “sIRve”
RE_WORD_IR = re.compile(r"(?<![A-ZÁÉÍÓÚÜÑa-záéíóúüñ])ir(?![A-ZÁÉÍÓÚÜÑa-záéíóúüñ])", re.IGNORECASE)


# =========================
# HELPERS
# =========================
def get_secret(*keys: str) -> Optional[str]:
    for k in keys:
        try:
            v = st.secrets.get(k)
            if v:
                return str(v).strip()
        except Exception:
            continue
    return None


def configure_gemini() -> Optional[Any]:
    if not GEMINI_AVAILABLE:
        return None

    # Acepta cualquiera de estas (tu error venía de nombre de key)
    api_key = get_secret("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")
    if not api_key:
        return None

    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


def post_json(url: str, payload: Dict[str, Any]) -> Optional[Any]:
    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_product_view(product_id: int) -> Optional[Dict[str, Any]]:
    payload = {"ProductId": int(product_id)}
    for base in API_BASES:
        data = post_json(f"{base}/Products/View", payload)
        if isinstance(data, dict) and data.get("ProductId"):
            return data
    return None


def extract_ids_and_models(text: str) -> Tuple[List[int], List[str]]:
    t = text or ""
    ids: List[int] = []
    models: List[str] = []

    for m in RE_PRODUCT_URL_ID.findall(t):
        try:
            ids.append(int(m))
        except Exception:
            pass

    for m in RE_MODEL.findall(t):
        models.append(m.upper().strip())

    # dedup manteniendo orden
    ids = list(dict.fromkeys(ids))
    models = list(dict.fromkeys(models))
    return ids, models


def safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def summarize_product_for_context(p: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "ProductId": p.get("ProductId"),
            "Code": p.get("Code"),
            "DescriptionShort": p.get("DescriptionShort"),
            "Price": p.get("Price"),
            "Stock": p.get("Stock"),
            "DataSheet": p.get("DataSheet"),
            "DescriptionLong": p.get("DescriptionLong"),
        },
        ensure_ascii=False,
    )


@st.cache_data(ttl=60 * 60, show_spinner=False)
def extract_pdf_text(pdf_url: str) -> str:
    if not PDF_AVAILABLE or not pdf_url:
        return ""
    try:
        r = requests.get(pdf_url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200 or not r.content:
            return ""
        reader = PdfReader(io.BytesIO(r.content))
        pages = min(len(reader.pages), PDF_MAX_PAGES)
        chunks = []
        for i in range(pages):
            try:
                chunks.append(reader.pages[i].extract_text() or "")
            except Exception:
                pass
        txt = "\n".join(chunks).strip()
        if len(txt) > PDF_MAX_CHARS:
            txt = txt[:PDF_MAX_CHARS] + "…"
        return txt
    except Exception:
        return ""


@st.cache_data(ttl=60 * 10, show_spinner=False)
def web_search_product_id_by_code(code: str) -> Optional[int]:
    """
    Fallback CLAVE: si no hay endpoint por código,
    scrapea el buscador web para encontrar /products/view/ID.
    """
    code = code.strip().upper()

    # Intentos de búsqueda web (cambian según tu sitio, por eso probamos varias)
    search_paths = [
        ("/products", {"search": code}),
        ("/products", {"q": code}),
        ("/products", {"s": code}),
        ("/", {"search": code}),
    ]

    for wb in WEB_BASES:
        for path, params in search_paths:
            try:
                url = wb + path
                r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
                if r.status_code != 200:
                    continue
                html = r.text or ""
                # buscar link /products/view/#### en el HTML
                m = re.search(r"/products/view/(\d+)", html, flags=re.IGNORECASE)
                if m:
                    return int(m.group(1))

                # si no aparece directo, parseo links (más caro pero más robusto)
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.select("a[href]"):
                    href = a.get("href", "")
                    mm = re.search(r"/products/view/(\d+)", href, flags=re.IGNORECASE)
                    if mm:
                        return int(mm.group(1))
            except Exception:
                continue

    return None


@st.cache_data(ttl=60 * 10, show_spinner=False)
def api_search_product_by_code(code: str) -> Optional[Dict[str, Any]]:
    """
    Intenta endpoints comunes (GET/POST). Si alguno existe, genial.
    Si no, devolver None y luego caemos a scraping.
    """
    code = code.strip().upper()

    # POST endpoints típicos
    post_candidates = [
        ("Products/Search", {"Query": code}),
        ("Products/Search", {"Text": code}),
        ("Products/FindByCode", {"Code": code}),
        ("Products/GetByCode", {"Code": code}),
        ("Products/ByCode", {"Code": code}),
    ]

    # GET endpoints típicos
    get_candidates = [
        ("Products/Search", {"q": code}),
        ("Products/Search", {"query": code}),
        ("Products/GetByCode", {"code": code}),
        (f"Products/GetByCode/{code}", None),
        (f"Products/ByCode/{code}", None),
        (f"Products/Find/{code}", None),
    ]

    for base in API_BASES:
        # POST
        for path, payload in post_candidates:
            data = post_json(f"{base}/{path}", payload)
            if isinstance(data, dict) and data.get("Code"):
                if safe_str(data.get("Code")).upper() == code:
                    pid = data.get("ProductId")
                    return fetch_product_view(int(pid)) if pid else data
            if isinstance(data, list) and data:
                for item in data:
                    if isinstance(item, dict) and safe_str(item.get("Code")).upper() == code:
                        pid = item.get("ProductId")
                        return fetch_product_view(int(pid)) if pid else item

        # GET
        for path, params in get_candidates:
            data = get_json(f"{base}/{path}", params=params)
            if isinstance(data, dict) and data.get("Code"):
                if safe_str(data.get("Code")).upper() == code:
                    pid = data.get("ProductId")
                    return fetch_product_view(int(pid)) if pid else data
            if isinstance(data, list) and data:
                for item in data:
                    if isinstance(item, dict) and safe_str(item.get("Code")).upper() == code:
                        pid = item.get("ProductId")
                        return fetch_product_view(int(pid)) if pid else item

    return None


def resolve_products_from_text(text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Resuelve productos mencionados por URL/ID o por Code.
    Devuelve (products, debug_info)
    """
    ids, models = extract_ids_and_models(text)

    debug = {"ids": ids, "models": models, "resolved": []}
    products: List[Dict[str, Any]] = []

    # 1) IDs directos
    for pid in ids:
        p = fetch_product_view(pid)
        if p:
            products.append(p)
            debug["resolved"].append({"via": "id", "pid": pid, "code": p.get("Code")})

    # 2) Codes
    for code in models:
        # evita repetir
        if any(safe_str(p.get("Code")).upper() == code for p in products):
            continue

        # 2A) intento API
        p = api_search_product_by_code(code)
        if p:
            products.append(p)
            debug["resolved"].append({"via": "api_code", "code": code, "pid": p.get("ProductId")})
            continue

        # 2B) fallback scraping web -> obtiene productId -> View
        pid = web_search_product_id_by_code(code)
        if pid:
            p2 = fetch_product_view(pid)
            if p2:
                products.append(p2)
                debug["resolved"].append({"via": "web_scrape", "code": code, "pid": pid, "real_code": p2.get("Code")})

    # de-dup
    uniq = []
    seen = set()
    for p in products:
        key = (safe_str(p.get("ProductId")), safe_str(p.get("Code")).upper())
        if key not in seen:
            uniq.append(p)
            seen.add(key)

    return uniq, debug


def infer_basic_answers(question: str, products: List[Dict[str, Any]]) -> str:
    """
    Respuesta sin IA: usa ficha + reglas simples.
    (Importante: NO usar 'ir' substring, usa regex de palabra)
    """
    q = (question or "").lower()

    if not products:
        return (
            "No pude identificar productos en tu consulta.\n\n"
            "👉 Pasame el/los **modelos exactos** (ej: `IPC-4M-FA-ZERO`) o una URL `bigdipper.com.ar/products/view/####`."
        )

    out = []
    for p in products:
        code = safe_str(p.get("Code")).upper()
        longd = safe_str(p.get("DescriptionLong"))
        shortd = safe_str(p.get("DescriptionShort"))
        out.append(f"**{code}** — {shortd}".strip(" —"))

        # Exterior
        if "exterior" in q or "afuera" in q:
            ipm = re.search(r"\bIP6[6-9]\b", longd, re.IGNORECASE)
            if ipm:
                out.append(f"• **Uso exterior:** Sí. La ficha indica **{ipm.group(0)}**.")
            else:
                out.append("• **Uso exterior:** No lo puedo confirmar con la ficha (no veo IP66/IP67).")

        # Alimentación
        if ("aliment" in q) or ("poe" in q) or ("fuente" in q) or ("volt" in q):
            if re.search(r"\bpoe\b", longd, re.IGNORECASE):
                out.append("• **Alimentación:** Compatible con **PoE** (según ficha).")
            elif re.search(r"\b12\s*v\b", longd, re.IGNORECASE):
                out.append("• **Alimentación:** La ficha menciona **12V**.")
            elif re.search(r"\b24\s*v\b", longd, re.IGNORECASE):
                out.append("• **Alimentación:** La ficha menciona **24V**.")
            else:
                out.append("• **Alimentación:** No figura explícito en la ficha.")

        # IR / visión nocturna (FIX: solo IR como palabra)
        if ("vision noct" in q) or ("infrarro" in q) or RE_WORD_IR.search(q) or ("noche" in q):
            if re.search(r"\bir\b|infrarro", longd, re.IGNORECASE):
                out.append("• **Visión nocturna:** La ficha menciona **IR / infrarrojo**.")
            elif re.search(r"luz blanca", longd, re.IGNORECASE):
                out.append("• **Visión nocturna:** La ficha indica **luz blanca** (color) y especifica distancia.")
            else:
                out.append("• **Visión nocturna:** No lo puedo confirmar con la ficha.")

    return "\n".join(out).strip()


def build_gemini_prompt(question: str, products: List[Dict[str, Any]], pdf_texts: Dict[str, str]) -> str:
    blocks = []
    for p in products:
        code = safe_str(p.get("Code")).upper()
        blocks.append(f"FICHA_JSON_{code}:\n{summarize_product_for_context(p)}\n")
        if pdf_texts.get(code):
            blocks.append(f"DATASHEET_TEXTO_{code}:\n{pdf_texts[code]}\n")

    context = "\n".join(blocks)

    return f"""
Sos un asistente técnico-comercial para vendedores (Argentina, español rioplatense).
Respondé SOLO con lo que puedas justificar con FICHA_JSON / DATASHEET_TEXTO.
Si falta un dato, decí “no figura en la ficha/datasheet” y sugerí qué dato confirmar.

Formato:
- Respuesta corta, clara, con viñetas.
- Si preguntan compatibilidad (ej cámara con XVR), explicá condición (ONVIF/RTSP/canales IP, etc.) y qué revisar.

CONSULTA:
{question}

INFO OFICIAL:
{context}
""".strip()


def answer_with_gemini(model, question: str, products: List[Dict[str, Any]]) -> str:
    if not model:
        return infer_basic_answers(question, products)

    pdf_texts = {}
    for p in products:
        code = safe_str(p.get("Code")).upper()
        ds = safe_str(p.get("DataSheet"))
        if ds:
            pdf_texts[code] = extract_pdf_text(ds)

    prompt = build_gemini_prompt(question, products, pdf_texts)

    try:
        resp = model.generate_content(prompt)
        txt = (getattr(resp, "text", "") or "").strip()
        return txt if txt else infer_basic_answers(question, products)
    except Exception:
        return infer_basic_answers(question, products)


# =========================
# UI
# =========================
st.title("🤖 Asistente de Ventas Big Dipper")
st.caption("Escribí como lo haría un vendedor. Podés mezclar modelo + consulta en una sola frase.")

gemini_model = configure_gemini()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_products" not in st.session_state:
    st.session_state.last_products = []
if "show_debug" not in st.session_state:
    st.session_state.show_debug = False

# Toggle debug (para que puedas ver qué detecta)
with st.sidebar:
    st.session_state.show_debug = st.toggle("Mostrar debug (detector de modelos/IDs)", value=st.session_state.show_debug)
    st.markdown("**Secrets**: si usás Gemini, cargá `GEMINI_API_KEY` o `GOOGLE_API_KEY` en Streamlit Cloud.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_text = st.chat_input("Tu consulta…")
if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        with st.spinner("Buscando ficha oficial…"):
            products, debug = resolve_products_from_text(user_text)

            # Conversacional: si no detecta nada en este turno, usa los últimos productos si existían
            if not products and st.session_state.last_products:
                products = st.session_state.last_products

            # Actualiza memoria corta
            if products:
                st.session_state.last_products = products

            if not products:
                reply = (
                    "No pude identificar productos en tu consulta.\n\n"
                    "👉 Pasame el/los **modelos exactos** (ej: `IPC-4M-FA-ZERO`) o una URL `bigdipper.com.ar/products/view/####`.\n"
                    "Si el modelo existe pero no lo engancho, activá **debug** y veo qué está detectando."
                )
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})
            else:
                reply = answer_with_gemini(gemini_model, user_text, products)
                st.markdown(reply)

                # Evidencia
                with st.expander("Ver datos oficiales usados (ficha)"):
                    for p in products:
                        code = safe_str(p.get("Code")).upper()
                        st.markdown(
                            f"- **{code}** | Stock: {p.get('Stock')} | Datasheet: {safe_str(p.get('DataSheet')) or '—'}"
                        )

                if st.session_state.show_debug:
                    with st.expander("Debug: qué detectó / cómo resolvió"):
                        st.json(debug)

                st.session_state.messages.append({"role": "assistant", "content": reply})
