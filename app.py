import re
import json
import unicodedata
from typing import List, Dict, Any, Optional, Tuple

import streamlit as st
import requests
from bs4 import BeautifulSoup

# pypdf (ya lo tenés en requirements)
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# Gemini (opcional: si no está o no hay key, igual funciona)
try:
    import google.generativeai as genai
except Exception:
    genai = None


# ----------------------------
# CONFIG
# ----------------------------
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖", layout="centered")

# Dominio base (probamos con y sin www)
BASES = [
    "https://www.bigdipper.com.ar",
    "https://bigdipper.com.ar",
]

# Endpoint API (según tu DevTools: www2.bigdipper.com.ar/api/Products/View)
API_VIEW = "https://www2.bigdipper.com.ar/api/Products/View"

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

REQ_TIMEOUT = 15


# ----------------------------
# HELPERS
# ----------------------------
def normalize_text(s: str) -> str:
    """Normaliza texto para extracción robusta de modelos."""
    if not s:
        return ""
    s = s.replace("–", "-").replace("—", "-").replace("-", "-")
    s = unicodedata.normalize("NFKC", s)
    return s.strip()


def extract_product_ids(text: str) -> List[int]:
    """Extrae IDs tipo /products/view/6964."""
    if not text:
        return []
    text = normalize_text(text)
    ids = re.findall(r"/products/view/(\d+)", text, flags=re.IGNORECASE)
    out = []
    for x in ids:
        try:
            out.append(int(x))
        except Exception:
            pass
    return list(dict.fromkeys(out))


def extract_models(text: str) -> List[str]:
    """
    Extrae modelos del mensaje.
    Reglas:
    - Debe tener al menos 1 dígito
    - Permite letras/números y separadores - _ /
    - Ej: IPC-4M-FA-ZERO, XVR6104-I, LM108-V2, DS-PDBG8-EG2
    """
    if not text:
        return []
    text = normalize_text(text).upper()

    # Captura tokens "con separadores" (lo más común en catálogos)
    pattern_sep = r"\b[A-Z0-9]{2,}(?:[-_/][A-Z0-9]{1,})+\b"
    candidates = re.findall(pattern_sep, text)

    # También capturar tokens tipo "XVR6104I" (sin separadores) si hiciera falta,
    # pero filtramos fuerte para no agarrar palabras sueltas.
    pattern_compact = r"\b[A-Z]{2,}\d{2,}[A-Z0-9]{0,}\b"
    candidates += re.findall(pattern_compact, text)

    # Filtrar: al menos 1 dígito, largo razonable
    out = []
    for c in candidates:
        if any(ch.isdigit() for ch in c) and len(c) >= 5:
            out.append(c)

    # Únicos manteniendo orden
    return list(dict.fromkeys(out))


def safe_get_secret(*names: str) -> Optional[str]:
    """Lee secrets con fallback (Streamlit Cloud)."""
    for n in names:
        try:
            v = st.secrets.get(n)
            if v:
                return str(v).strip()
        except Exception:
            pass
    return None


@st.cache_data(show_spinner=False, ttl=60 * 10)
def fetch_list_search_html(query: str) -> Optional[str]:
    """Busca en /products/list?s=QUERY y devuelve HTML."""
    query = (query or "").strip()
    if not query:
        return None

    for base in BASES:
        url = f"{base}/products/list"
        try:
            r = requests.get(url, params={"s": query}, headers=UA, timeout=REQ_TIMEOUT)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            continue
    return None


def parse_ids_from_list_html(html: str) -> List[int]:
    """Parsea IDs /products/view/#### desde el HTML de list."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/products/view/(\d+)", href, flags=re.IGNORECASE)
        if m:
            try:
                ids.append(int(m.group(1)))
            except Exception:
                pass
    return list(dict.fromkeys(ids))


@st.cache_data(show_spinner=False, ttl=60 * 10)
def resolve_model_to_product_id(model: str) -> Optional[int]:
    """
    Intenta resolver MODELO -> ProductId.
    Estrategia:
    1) /products/list?s=MODELO (scrape links a /products/view/ID)
    2) Si hay varios, devuelve el primero (en debug mostramos todos).
    """
    model = (model or "").strip()
    if not model:
        return None

    html = fetch_list_search_html(model)
    if not html:
        return None

    ids = parse_ids_from_list_html(html)
    if not ids:
        return None

    return ids[0]


@st.cache_data(show_spinner=False, ttl=60 * 10)
def fetch_product_json(product_id: int) -> Optional[Dict[str, Any]]:
    """Llama al endpoint API /api/Products/View con {"ProductId": id}."""
    if not product_id:
        return None
    payload = {"ProductId": int(product_id)}
    try:
        r = requests.post(API_VIEW, json=payload, headers={**UA, "Content-Type": "application/json"}, timeout=REQ_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and data.get("ProductId"):
                return data
    except Exception:
        pass
    return None


@st.cache_data(show_spinner=False, ttl=60 * 30)
def fetch_pdf_text(pdf_url: str, max_pages: int = 6) -> str:
    """Extrae texto del datasheet PDF (si pypdf está disponible)."""
    if not pdf_url or not PdfReader:
        return ""
    try:
        r = requests.get(pdf_url, headers=UA, timeout=REQ_TIMEOUT)
        if r.status_code != 200 or not r.content:
            return ""
        from io import BytesIO
        reader = PdfReader(BytesIO(r.content))
        texts = []
        for i, page in enumerate(reader.pages[:max_pages]):
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                texts.append(t.strip())
        return "\n".join(texts).strip()
    except Exception:
        return ""


def build_product_brief(p: Dict[str, Any]) -> str:
    """Resumen corto de ficha para modo ventas."""
    if not p:
        return ""
    code = p.get("Code") or ""
    desc_short = p.get("DescriptionShort") or ""
    stock = p.get("Stock")
    price = p.get("Price")
    datasheet = p.get("DataSheet") or ""
    lines = []
    if code:
        lines.append(f"**{code}**")
    if desc_short:
        lines.append(f"- {desc_short}")
    if stock is not None:
        lines.append(f"- Stock: {stock}")
    if price is not None:
        lines.append(f"- Precio: USD {price}")
    if datasheet:
        lines.append(f"- Datasheet: {datasheet}")
    return "\n".join(lines).strip()


def sales_answer_from_product(question: str, products: List[Dict[str, Any]]) -> str:
    """
    Respuesta "modo ventas" sin inventar:
    - Usa ficha (DescriptionLong)
    - Usa datasheet texto si hace falta
    - Para compatibilidad, responde con reglas generales + qué chequear
    """
    q = (question or "").strip()
    if not products:
        return "No pude traer la ficha del/los producto(s). Probá con una URL /products/view/#### o verificá que el modelo exista en la web."

    # Si es 1 producto: responder preguntas típicas rápidas con keywords
    if len(products) == 1:
        p = products[0]
        long_desc = (p.get("DescriptionLong") or "").lower()
        short_desc = (p.get("DescriptionShort") or "").lower()
        datasheet = p.get("DataSheet") or ""

        # Reglas simples útiles
        ql = q.lower()
        if "exterior" in ql or "intemperie" in ql:
            if "ip67" in long_desc or "ip66" in long_desc:
                return f"Sí, sirve para exterior: la ficha indica **{('IP67' if 'ip67' in long_desc else 'IP66')}** (protección para intemperie)."
            return "En la ficha no encuentro IP66/IP67. Para confirmar exterior, necesito el dato de protección (IP)."

        if "poe" in ql or "aliment" in ql or "fuente" in ql:
            if "poe" in long_desc:
                return "La ficha indica que **es compatible con alimentación PoE**."
            return "No veo PoE en la ficha. Para confirmar alimentación, necesito el datasheet o el dato de consumo/voltaje."

        if "micro" in ql or "audio" in ql:
            if "micrófono" in long_desc or "microfono" in long_desc or "micrófono integrado" in long_desc:
                return "Sí: la ficha dice **micrófono integrado**."
            return "En la ficha no veo mención de micrófono/audio."

        if "ir" in ql or "infrarro" in ql:
            # Ojo: en tu ejemplo de Zero dice "luz blanca" (no IR)
            if "luz blanca" in long_desc:
                return "Según ficha, no habla de IR: indica **luz blanca (cálida) hasta 30 m** para color 24/7."
            return "No veo IR/luz blanca en la ficha. Si me pasás el datasheet, lo confirmo."

        # default: dar resumen y sugerir cómo confirmar
        msg = build_product_brief(p)
        extra = ""
        if datasheet and PdfReader:
            pdf_text = fetch_pdf_text(datasheet)
            if pdf_text:
                extra = "\n\nSi querés, pegá la pregunta exacta y lo busco también dentro del datasheet."
        return f"{msg}\n\n**Lo que dice la ficha:**\n{p.get('DescriptionLong','')}{extra}"

    # Si son 2+ productos: compatibilidad
    # Sin inventar: explicar cómo se determina y qué falta.
    codes = [p.get("Code") for p in products if p.get("Code")]
    ql = q.lower()

    # Heurística: cámara IP vs XVR analógico
    # (solo como guía comercial, no como confirmación)
    hint = []
    joined = " ".join([(p.get("DescriptionLong") or "") for p in products]).lower()
    if any("ipc" in (c or "").lower() for c in codes) and any("xvr" in (c or "").lower() for c in codes):
        hint.append(
            "📌 **Compatibilidad cámara IP + XVR:** en general un **XVR es para cámaras analógicas** (TVI/CVI/AHD/CVBS). "
            "Algunos XVR son **híbridos** y aceptan **canales IP/ONVIF**, pero depende del modelo exacto del XVR."
        )

    hint.append(
        "Para confirmarlo bien, hay que chequear en la ficha/datasheet del grabador: **cantidad de canales IP soportados**, "
        "**compatibilidad ONVIF/RTSP**, y el **perfil de compresión** (H.265/H.264)."
    )

    # Entregar info de ambos
    briefs = "\n\n".join([build_product_brief(p) for p in products])
    return f"{briefs}\n\n" + "\n".join(hint)


def maybe_gemini_polish(answer: str, question: str) -> str:
    """
    Si hay Gemini y key, reescribe en tono ventas sin agregar hechos nuevos.
    """
    if not genai:
        return answer

    key = safe_get_secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return answer

    try:
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
Sos un asistente para vendedores de seguridad electrónica/iluminación.
Reescribí la respuesta para que sea clara y útil para ventas.
REGLA: No inventes datos. No agregues specs que no estén en el texto. Si falta info, decí qué falta confirmar.
Pregunta del vendedor: {question}
Respuesta base (hechos): {answer}
Devolvé la versión final en español argentino, concisa.
"""
        resp = model.generate_content(prompt)
        text = getattr(resp, "text", "") or ""
        return text.strip() if text.strip() else answer
    except Exception:
        return answer


# ----------------------------
# UI
# ----------------------------
st.title("🤖 Asistente de Ventas Big Dipper")

with st.sidebar:
    debug = st.toggle("Mostrar debug (detector de modelos/IDs)", value=False)
    st.caption("Secrets: si usás Gemini, cargá **GEMINI_API_KEY** o **GOOGLE_API_KEY** en Streamlit Cloud.")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Render chat history
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

user_msg = st.chat_input("Tu consulta… (podés incluir modelo(s) y compatibilidad en la misma frase)")

if user_msg:
    user_msg = normalize_text(user_msg)

    st.session_state.messages.append({"role": "user", "content": user_msg})
    with st.chat_message("user"):
        st.markdown(user_msg)

    with st.chat_message("assistant"):
        with st.spinner("Analizando…"):
            ids = extract_product_ids(user_msg)
            models = extract_models(user_msg)

            if debug:
                st.markdown(f"**Debug**\n- IDs detectados: `{ids}`\n- Modelos detectados: `{models}`")

            product_ids: List[int] = []

            # Si hay IDs directos, usarlos
            product_ids.extend(ids)

            # Si no hay IDs, resolver modelos
            if not product_ids and models:
                for md in models[:3]:  # límite práctico
                    pid = resolve_model_to_product_id(md)
                    if pid:
                        product_ids.append(pid)

            product_ids = list(dict.fromkeys(product_ids))[:3]

            products: List[Dict[str, Any]] = []
            for pid in product_ids:
                pj = fetch_product_json(pid)
                if pj:
                    products.append(pj)

            if not products:
                msg = (
                    "No pude identificar productos en tu consulta.\n\n"
                    "👉 Pegame **una URL** tipo `bigdipper.com.ar/products/view/####` "
                    "o el/los **modelos exactos** (ej: `IPC-4M-FA-ZERO`).\n\n"
                    "Si el modelo **no existe en la web** (solo en el sistema interno), acá no lo voy a poder resolver."
                )
                st.markdown(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            else:
                base_answer = sales_answer_from_product(user_msg, products)
                final_answer = maybe_gemini_polish(base_answer, user_msg)

                st.markdown(final_answer)

                # Opcional: mostrar "fuente" usada (sin molestar)
                if debug:
                    with st.expander("Ver datos oficiales usados (ficha)"):
                        for p in products:
                            st.json({
                                "ProductId": p.get("ProductId"),
                                "Code": p.get("Code"),
                                "DescriptionShort": p.get("DescriptionShort"),
                                "Stock": p.get("Stock"),
                                "Price": p.get("Price"),
                                "DataSheet": p.get("DataSheet"),
                                "Links": p.get("Links"),
                            })

                st.session_state.messages.append({"role": "assistant", "content": final_answer})
