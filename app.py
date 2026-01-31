# app.py — Asistente de Ventas Big Dipper (conversacional, detecta modelos/URLs, trae ficha oficial y responde “modo vendedor”)
# Reqs: streamlit, google-generativeai, requests, beautifulsoup4 (opcional), pypdf

import re
import json
import time
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st
import requests

# Gemini (opcional: si no hay key, cae a modo “reglas + ficha”)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

# PDF (opcional, no rompe si falla)
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

# API (probado por tu captura: /api/Products/View con POST)
API_BASES = [
    "https://www2.bigdipper.com.ar/api",
    "https://www.bigdipper.com.ar/api",
]

# Timeouts
HTTP_TIMEOUT = 8

# Límite de texto a extraer de PDF (para no explotar tokens)
PDF_MAX_PAGES = 3
PDF_MAX_CHARS = 3500

# Regex para detectar:
RE_PRODUCT_URL_ID = re.compile(r"(?:bigdipper\.com\.ar\/products\/view\/)(\d+)", re.IGNORECASE)
RE_ANY_ID = re.compile(r"\b(\d{3,8})\b")  # IDs plausibles
RE_MODEL = re.compile(r"\b[A-Z0-9]{3,}(?:-[A-Z0-9]+)+\b", re.IGNORECASE)


# =========================
# HELPERS
# =========================
def get_secret(*keys: str) -> Optional[str]:
    """Lee keys posibles de st.secrets sin romper."""
    for k in keys:
        try:
            v = st.secrets.get(k)
            if v:
                return str(v).strip()
        except Exception:
            continue
    return None


def configure_gemini() -> Optional[Any]:
    """Configura Gemini si hay key."""
    if not GEMINI_AVAILABLE:
        return None

    # Acepta cualquiera de estas keys para que no te vuelva a pasar lo del KeyError
    api_key = get_secret("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")
    if not api_key:
        return None

    genai.configure(api_key=api_key)
    # Flash es más rápido/ barato para esto
    return genai.GenerativeModel("gemini-1.5-flash")


def post_json(url: str, payload: Dict[str, Any]) -> Optional[Any]:
    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


@st.cache_data(ttl=60 * 10, show_spinner=False)  # cachea 10 min
def fetch_product_view(product_id: int) -> Optional[Dict[str, Any]]:
    """Trae ficha oficial por ProductId usando /Products/View."""
    payload = {"ProductId": int(product_id)}
    for base in API_BASES:
        data = post_json(f"{base}/Products/View", payload)
        if isinstance(data, dict) and data.get("ProductId"):
            return data
    return None


@st.cache_data(ttl=60 * 10, show_spinner=False)
def search_product_by_code(code: str) -> Optional[Dict[str, Any]]:
    """
    Busca producto por Code probando endpoints típicos.
    Si tu backend tiene otro endpoint, igual te queda listo para sumar 1 línea.
    """
    code = code.strip().upper()

    # 1) Intento: endpoint tipo Search (muy común)
    candidates_payloads = [
        ("Products/Search", {"Query": code}),
        ("Products/Search", {"Text": code}),
        ("Products/Search", {"q": code}),
        ("Products/Find", {"Query": code}),
        ("Products/FindByCode", {"Code": code}),
        ("Products/ByCode", {"Code": code}),
        ("Products/GetByCode", {"Code": code}),
    ]

    for base in API_BASES:
        for path, payload in candidates_payloads:
            data = post_json(f"{base}/{path}", payload)

            # Caso A: devuelve lista
            if isinstance(data, list) and data:
                # Match exacto por "Code"
                for p in data:
                    if isinstance(p, dict) and str(p.get("Code", "")).upper() == code:
                        # Si ya trae ProductId, confirmamos con View
                        pid = p.get("ProductId")
                        if pid:
                            full = fetch_product_view(int(pid))
                            return full or p
                        return p

            # Caso B: devuelve dict directo
            if isinstance(data, dict) and data.get("Code"):
                if str(data.get("Code", "")).upper() == code:
                    pid = data.get("ProductId")
                    if pid:
                        full = fetch_product_view(int(pid))
                        return full or data
                    return data

    return None


def extract_ids_and_models(text: str) -> Tuple[List[int], List[str]]:
    """Extrae IDs desde URL y modelos tipo IPC-4M-FA-ZERO."""
    t = text or ""
    ids = []
    models = []

    for m in RE_PRODUCT_URL_ID.findall(t):
        try:
            ids.append(int(m))
        except Exception:
            pass

    # Modelos
    for m in RE_MODEL.findall(t):
        mm = m.upper().strip()
        # descarta cosas raras tipo "HTTP-200" si aparecieran
        if len(mm) >= 6 and "-" in mm:
            models.append(mm)

    # De-dup manteniendo orden
    ids = list(dict.fromkeys(ids))
    models = list(dict.fromkeys(models))
    return ids, models


def safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def summarize_product_for_context(p: Dict[str, Any]) -> str:
    """Resumen compacto de ficha para meter en el contexto del modelo."""
    return json.dumps({
        "ProductId": p.get("ProductId"),
        "Code": p.get("Code"),
        "DescriptionShort": p.get("DescriptionShort"),
        "Price": p.get("Price"),
        "Stock": p.get("Stock"),
        "Image": p.get("Image"),
        "DataSheet": p.get("DataSheet"),
        "Links": p.get("Links", []),
        "DescriptionLong": p.get("DescriptionLong"),
    }, ensure_ascii=False)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def extract_pdf_text(pdf_url: str) -> str:
    """Baja PDF y extrae texto (si pypdf está disponible)."""
    if not PDF_AVAILABLE:
        return ""

    if not pdf_url:
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


def infer_basic_answers(question: str, products: List[Dict[str, Any]]) -> str:
    """
    Respuesta “modo vendedor” sin IA: usa ficha y reglas.
    (Sirve como fallback si no hay Gemini o si falla.)
    """
    q = (question or "").lower()

    if not products:
        return "No pude identificar productos en tu consulta. Pasame el/los modelos exactos o una URL de producto (ej: bigdipper.com.ar/products/view/6964)."

    lines = []
    for p in products:
        code = safe_str(p.get("Code")).upper()
        longd = safe_str(p.get("DescriptionLong"))
        shortd = safe_str(p.get("DescriptionShort"))
        lines.append(f"**{code}** — {shortd}".strip(" —"))

        # Exterior: si tiene IP67 / IP66 en descripción
        if "exterior" in q or "afuera" in q:
            if re.search(r"\bIP6[6-9]\b", longd, re.IGNORECASE):
                ip = re.search(r"\bIP6[6-9]\b", longd, re.IGNORECASE).group(0)
                lines.append(f"• **Uso exterior:** Sí. La ficha indica protección **{ip}**.")
            else:
                lines.append("• **Uso exterior:** No lo puedo confirmar con la ficha visible (no aparece IP66/IP67).")

        # Alimentación: PoE / 12V / 24V
        if "aliment" in q or "poe" in q or "fuente" in q or "volt" in q:
            if re.search(r"\bpoe\b", longd, re.IGNORECASE):
                lines.append("• **Alimentación:** Compatible con **PoE** (según ficha).")
            elif re.search(r"\b12\s*v\b", longd, re.IGNORECASE):
                lines.append("• **Alimentación:** La ficha menciona **12V**.")
            elif re.search(r"\b24\s*v\b", longd, re.IGNORECASE):
                lines.append("• **Alimentación:** La ficha menciona **24V**.")
            else:
                lines.append("• **Alimentación:** No figura explícito en la ficha que tengo acá.")

        # IR / luz
        if "ir" in q or "infrarro" in q or "noche" in q or "oscur" in q:
            if re.search(r"\bir\b|infrarro", longd, re.IGNORECASE):
                lines.append("• **Visión nocturna:** La ficha menciona **IR / infrarrojo**.")
            elif re.search(r"luz blanca", longd, re.IGNORECASE):
                # tu ejemplo de cámara Zero habla de luz blanca
                lines.append("• **Visión nocturna:** No usa IR clásico; la ficha indica **luz blanca** para color (hasta la distancia indicada).")
            else:
                lines.append("• **Visión nocturna:** No lo puedo confirmar con la ficha.")

    # Compatibilidad (si hay 2 modelos en la pregunta)
    if len(products) >= 2 and ("compat" in q or "funciona con" in q or "sirve con" in q or "xvr" in q or "nvr" in q):
        codes = [safe_str(p.get("Code")).upper() for p in products]
        lines.append("\n**Compatibilidad (criterio práctico):**")
        # reglas simples por prefijo
        cam = None
        rec = None
        for c in codes:
            if c.startswith("IPC") or c.startswith("NVC") or c.startswith("NVR"):
                cam = cam or c
            if c.startswith("XVR") or c.startswith("DVR") or c.startswith("NVR"):
                rec = rec or c

        # Si parece cámara IP con XVR: depende si el XVR soporta canales IP/ONVIF
        if any(c.startswith("IPC") for c in codes) and any(c.startswith("XVR") for c in codes):
            lines.append("• **IPC (IP) + XVR:** depende de que el XVR sea **híbrido con canales IP** y soporte **ONVIF/RTSP**. Si el XVR es solo analógico, no.")
        else:
            lines.append("• Para confirmarlo 100%, necesito ver en la ficha del grabador cuántos **canales IP** soporta y si dice **ONVIF/RTSP**.")

    return "\n".join(lines).strip()


def build_gemini_prompt(question: str, products: List[Dict[str, Any]], pdf_texts: Dict[str, str]) -> str:
    """
    Prompt “modo vendedor” bien estricto:
    - Solo afirmar lo que esté en la ficha/PDF
    - Si falta algo, decir que no figura
    - Dar respuesta accionable para vender (pero sin chamuyo)
    """
    prod_blocks = []
    for p in products:
        code = safe_str(p.get("Code")).upper()
        prod_blocks.append(f"FICHA_JSON_{code}:\n{summarize_product_for_context(p)}\n")
        pdf = pdf_texts.get(code, "")
        if pdf:
            prod_blocks.append(f"DATASHEET_TEXTO_{code}:\n{pdf}\n")

    context = "\n".join(prod_blocks)

    return f"""
Sos un asistente técnico-comercial para vendedores (Argentina, español rioplatense).
Tu trabajo: responder consultas técnicas usando SOLO la info provista en FICHA_JSON / DATASHEET_TEXTO.
Reglas:
- No inventes compatibilidades. Si no está, decí “no figura en la ficha/datasheet”.
- Si te preguntan “sirve para X” (ej: boliche), respondé con criterio técnico basado en la ficha (potencia, IP, tipo, etc.) y aclarando límites.
- Respuesta corta, clara, con viñetas. Siempre incluir “Qué confirmaría” si falta un dato.

CONSULTA DEL VENDEDOR:
{question}

INFO OFICIAL DISPONIBLE:
{context}
""".strip()


def answer_with_gemini(model, question: str, products: List[Dict[str, Any]]) -> str:
    """Usa Gemini si está disponible; si falla, cae al modo reglas."""
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
        text = getattr(resp, "text", None) or ""
        text = text.strip()
        if not text:
            return infer_basic_answers(question, products)
        return text
    except Exception:
        return infer_basic_answers(question, products)


def resolve_products_from_text(text: str) -> List[Dict[str, Any]]:
    """Resuelve productos mencionados por URL/ID o por código."""
    ids, models = extract_ids_and_models(text)

    products: List[Dict[str, Any]] = []

    # 1) Por IDs (URL)
    for pid in ids:
        p = fetch_product_view(pid)
        if p:
            products.append(p)

    # 2) Por modelos (code)
    for code in models:
        # si ya lo tenemos por ID, no repetir
        if any(safe_str(p.get("Code")).upper() == code for p in products):
            continue
        p = search_product_by_code(code)
        if p:
            products.append(p)

    # de-dup por ProductId/Code
    uniq = []
    seen = set()
    for p in products:
        key = (safe_str(p.get("ProductId")), safe_str(p.get("Code")).upper())
        if key not in seen:
            uniq.append(p)
            seen.add(key)
    return uniq


# =========================
# UI
# =========================
st.title("🤖 Asistente de Ventas Big Dipper")
st.caption("Escribí como lo haría un vendedor (incluí modelos si los tenés). Ej: “¿La IPC-4M-FA-ZERO sirve para exterior y con qué grabador funciona?”")

gemini_model = configure_gemini()

# Session state chat
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_products" not in st.session_state:
    st.session_state.last_products = []  # cache de productos del último turno


# Render historial
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


user_text = st.chat_input("Tu consulta…")
if user_text:
    # User msg
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        with st.spinner("Analizando ficha oficial…"):
            products = resolve_products_from_text(user_text)

            # Si no encontró productos en esta pregunta, pero venimos de uno anterior,
            # usamos “memoria corta” de sesión: ayuda a que sea conversacional.
            if not products and st.session_state.last_products:
                products = st.session_state.last_products

            # Guardar para el próximo turno
            if products:
                st.session_state.last_products = products

            # Si sigue sin productos: pedir modelo o URL
            if not products:
                reply = (
                    "No pude identificar productos en tu consulta.\n\n"
                    "👉 Pegame el/los **modelos exactos** (ej: `IPC-4M-FA-ZERO`) **o** una URL tipo "
                    "`bigdipper.com.ar/products/view/6964`.\n\n"
                    "Tip: también sirve si pegás el JSON de la ficha, como hiciste antes."
                )
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})
            else:
                reply = answer_with_gemini(gemini_model, user_text, products)

                # Bloque opcional: “evidencia” rápida para vendedores (sin ensuciar demasiado)
                # Si querés sacarlo, borrá este bloque.
                evid = []
                for p in products:
                    code = safe_str(p.get("Code")).upper()
                    shortd = safe_str(p.get("DescriptionShort"))
                    stock = p.get("Stock")
                    ds = safe_str(p.get("DataSheet"))
                    evid.append(f"- **{code}**: {shortd} | Stock: {stock} | Datasheet: {ds if ds else '—'}")

                st.markdown(reply)
                with st.expander("Ver datos oficiales usados (ficha)"):
                    st.markdown("\n".join(evid))

                st.session_state.messages.append({"role": "assistant", "content": reply})



