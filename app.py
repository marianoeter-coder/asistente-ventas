# app.py
# Asistente de Ventas Big Dipper (modo conversacional)
# - Acepta: consulta libre + modelos + (opcional) URL /products/view/#### + (opcional) JSON pegado
# - Para "modelo suelto" (ej LM108-V2) necesita un índice: models.csv (Code,ProductId)
#   Ej: LM108-V2,5904
# - Evita dependencias problemáticas (bs4, pdfplumber). Solo usa requests + regex.

import json
import os
import re
from typing import Dict, Any, List, Optional, Tuple

import requests
import streamlit as st

# Gemini (opcional)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖", layout="wide")

APP_TITLE = "🤖 Asistente de Ventas Big Dipper"

# Endpoint para obtener JSON por ProductId.
# IMPORTANTE: si tu endpoint real es otro, lo cambiás acá o lo ponés en Secrets como PRODUCT_API_URL.
# Formatos soportados:
# 1) PRODUCT_API_URL = "https://www.bigdipper.com.ar/api/products/view"   -> GET {base}/{id}
# 2) PRODUCT_API_URL = "https://www.bigdipper.com.ar/api/products/view?id={id}" -> GET con .format(id=id)
DEFAULT_PRODUCT_API_URL = "https://www.bigdipper.com.ar/api/products/view"

# URL base de páginas públicas
PUBLIC_VIEW_PREFIX = "https://www.bigdipper.com.ar/products/view/"

# Archivo de mapeo Code -> ProductId (para que funcione "LM108-V2" sin URL)
MODELS_CSV_PATH = "models.csv"

# Timeout requests
HTTP_TIMEOUT = 12


# =========================
# HELPERS
# =========================
def get_secret(*keys: str) -> Optional[str]:
    """Devuelve el primer secret encontrado entre varias keys."""
    for k in keys:
        try:
            v = st.secrets.get(k, None)
            if v:
                return str(v).strip()
        except Exception:
            pass
    # fallback env
    for k in keys:
        v = os.getenv(k)
        if v:
            return v.strip()
    return None


def safe_get(url: str, headers: Optional[dict] = None) -> Tuple[int, str]:
    r = requests.get(url, headers=headers or {}, timeout=HTTP_TIMEOUT)
    return r.status_code, r.text


def safe_get_json(url: str, headers: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    r = requests.get(url, headers=headers or {}, timeout=HTTP_TIMEOUT)
    if r.status_code >= 200 and r.status_code < 300:
        try:
            return r.json()
        except Exception:
            return None
    return None


def normalize_code(s: str) -> str:
    return re.sub(r"[^A-Z0-9\-_.]", "", s.upper().strip())


def extract_urls(text: str) -> List[str]:
    return re.findall(r"(https?://[^\s]+)", text)


def extract_view_ids_from_urls(urls: List[str]) -> List[int]:
    ids = []
    for u in urls:
        m = re.search(r"/products/view/(\d+)", u)
        if m:
            ids.append(int(m.group(1)))
    return ids


def looks_like_json_object(text: str) -> bool:
    t = text.strip()
    return t.startswith("{") and t.endswith("}") and '"ProductId"' in t


def parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_candidate_codes(text: str) -> List[str]:
    """
    Detecta modelos tipo IPC-4M-FA-ZERO, LM108-V2, XVR-AHD-410, etc.
    Regla: bloque con letras/números y guiones, al menos 4 chars y contiene al menos un número.
    """
    candidates = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9\-_\.]{3,}\b", text)
    out = []
    for c in candidates:
        c2 = normalize_code(c)
        if len(c2) < 4:
            continue
        if not re.search(r"\d", c2):
            continue
        # filtrar cosas comunes
        if c2 in {"HTTP", "HTTPS", "WWW"}:
            continue
        out.append(c2)
    # unique manteniendo orden
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


@st.cache_data(show_spinner=False)
def load_models_map(csv_path: str) -> Dict[str, int]:
    """
    Lee models.csv con formato:
    CODE,PRODUCT_ID
    LM108-V2,5904
    IPC-4M-FA-ZERO,6964
    """
    mapping: Dict[str, int] = {}
    if not os.path.exists(csv_path):
        return mapping
    with open(csv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            code = normalize_code(parts[0])
            try:
                pid = int(re.sub(r"\D", "", parts[1]))
            except Exception:
                continue
            if code:
                mapping[code] = pid
    return mapping


def build_product_api_url(product_id: int) -> str:
    base = get_secret("PRODUCT_API_URL") or DEFAULT_PRODUCT_API_URL
    # Si el usuario configuró un template con {id}
    if "{id}" in base:
        return base.format(id=product_id)
    # si termina con "/" o no
    if base.endswith("/"):
        return f"{base}{product_id}"
    # Si parece querystring tipo "...?id="
    if "id=" in base and base.endswith("id="):
        return f"{base}{product_id}"
    # default: base/id
    return f"{base}/{product_id}"


def fetch_product_by_id(product_id: int) -> Optional[Dict[str, Any]]:
    """
    Intenta:
    1) API JSON (PRODUCT_API_URL)
    2) Si falla, intenta leer HTML de /products/view/{id} y sacar JSON embebido (si existiera).
       (Sin bs4: solo regex)
    """
    # 1) API JSON
    api_url = build_product_api_url(product_id)
    j = safe_get_json(api_url)
    if j and isinstance(j, dict) and ("ProductId" in j or "Code" in j):
        return j

    # 2) Fallback: HTML público
    html_url = f"{PUBLIC_VIEW_PREFIX}{product_id}"
    try:
        status, html = safe_get(html_url)
        if status < 200 or status >= 300:
            return None
        # Buscar un bloque JSON con "ProductId": <id>
        # Intento A: objeto JSON directo en scripts
        m = re.search(r'(\{[^{}]*"ProductId"\s*:\s*' + str(product_id) + r'[^{}]*\})', html)
        if m:
            maybe = m.group(1)
            parsed = parse_json_from_text(maybe)
            if parsed:
                return parsed
        # Intento B: si hay algo tipo window.__PRODUCT__ = {...};
        m2 = re.search(r'__PRODUCT__\s*=\s*(\{.*?\});', html, flags=re.DOTALL)
        if m2:
            parsed = parse_json_from_text(m2.group(1))
            if parsed:
                return parsed
    except Exception:
        return None

    return None


def compact_product(product: Dict[str, Any]) -> Dict[str, Any]:
    """Deja solo lo útil para el prompt."""
    keys = [
        "ProductId", "Code", "DescriptionShort", "DescriptionLong",
        "Price", "Stock", "Image", "DataSheet", "Links"
    ]
    out = {}
    for k in keys:
        if k in product:
            out[k] = product[k]
    return out


def answer_without_gemini(product: Optional[Dict[str, Any]], question: str) -> str:
    """
    Modo sin IA: responde SOLO con ficha.
    Si pregunta algo no presente, lo marca como no confirmable.
    """
    if not product:
        return "No pude obtener la ficha del producto con lo que me pasaste. Probá pegar una URL /products/view/#### o el JSON de la ficha."

    code = product.get("Code", "Producto")
    long_desc = (product.get("DescriptionLong") or "").strip()
    short_desc = (product.get("DescriptionShort") or "").strip()

    # Respuesta mínima orientada a ventas, sin inventar
    lines = []
    lines.append(f"**{code}**")
    if short_desc:
        lines.append(f"- {short_desc}")

    if "IP67" in long_desc.upper():
        lines.append("- ✔️ Apto exterior: la ficha indica **IP67**.")
    else:
        lines.append("- ⚠️ Exterior: no veo **IP67/IP66** en la ficha pegada; no lo confirmo.")

    # Alimentación/PoE
    if re.search(r"\bPOE\b", long_desc.upper()):
        lines.append("- 🔌 Alimentación: la ficha indica **PoE**.")
    else:
        lines.append("- 🔌 Alimentación: no lo veo claro en la ficha pegada; no lo confirmo.")

    # IR / luz blanca
    if "LUZ BLANCA" in long_desc.upper():
        lines.append("- 💡 Iluminación: trae **luz blanca** (la ficha menciona hasta 30 m).")
        lines.append("- 🌙 IR: si el producto es “Zero a color”, normalmente NO es IR clásico; pero **sin ficha específica no lo confirmo**.")

    # Links útiles
    ds = product.get("DataSheet")
    if ds:
        lines.append(f"- 📄 Datasheet: {ds}")
    img = product.get("Image")
    if img:
        lines.append(f"- 🖼️ Imagen: {img}")

    lines.append("\nSi querés, pegá la pregunta exacta y te marco qué parte está soportada por ficha y qué sería recomendación comercial.")
    return "\n".join(lines)


def gemini_generate(product_payload: Dict[str, Any], user_question: str) -> str:
    """
    Respuesta estilo vendedor:
    - Primero: lo confirmado por ficha
    - Luego: recomendación comercial (si falta dato), claramente marcado como "orientación"
    - Evitar inventar specs
    """
    api_key = get_secret("GEMINI_API_KEY", "GOOGLE_API_KEY")
    if not api_key or not GEMINI_AVAILABLE:
        # fallback
        return answer_without_gemini(product_payload, user_question)

    genai.configure(api_key=api_key)

    model_name = get_secret("GEMINI_MODEL") or "gemini-1.5-flash"
    model = genai.GenerativeModel(model_name)

    product = compact_product(product_payload)

    system = (
        "Sos un asistente para VENDEDORES de Big Dipper (Argentina). "
        "Objetivo: ayudar a responder rápido consultas técnicas/comerciales.\n\n"
        "REGLAS IMPORTANTES:\n"
        "1) NO inventes especificaciones técnicas.\n"
        "2) Separá SIEMPRE en 2 bloques:\n"
        "   A) 'Confirmado por ficha' (solo con datos presentes)\n"
        "   B) 'Orientación comercial' (deducciones razonables de uso, dejando claro que no es dato de ficha)\n"
        "3) Si te preguntan compatibilidad con otro producto y NO hay datos en ficha, explicá qué habría que chequear "
        "(ej: estándar ONVIF, tipos de señal, PoE/12V, resolución soportada, codecs, etc.) y pedí el modelo del otro equipo.\n"
        "4) Estilo: claro, vendedor, en español argentino, directo.\n"
    )

    prompt = (
        f"{system}\n"
        f"FICHA (JSON resumido):\n{json.dumps(product, ensure_ascii=False)}\n\n"
        f"PREGUNTA DEL VENDEDOR:\n{user_question}\n\n"
        "Respondé ahora con bullets cortos."
    )

    resp = model.generate_content(prompt)
    text = (resp.text or "").strip()
    if not text:
        return answer_without_gemini(product_payload, user_question)
    return text


# =========================
# UI STATE
# =========================
if "messages" not in st.session_state:
    st.session_state.messages = []

st.title(APP_TITLE)

with st.sidebar:
    st.caption("Configuración")
    debug = st.toggle("Mostrar debug (detector)", value=False, help="Muestra qué modelos/IDs detectó y qué resolvió.")
    st.divider()
    st.caption("Secrets (Streamlit Cloud)")
    st.write("- `GOOGLE_API_KEY` o `GEMINI_API_KEY` (si usás Gemini)")
    st.write("- `PRODUCT_API_URL` (si tu API real no es la default)")
    st.write("- `GEMINI_MODEL` (opcional)")
    st.divider()
    st.caption("Tip")
    st.write("Para que funcione escribir **solo el modelo** (ej: LM108-V2), subí un `models.csv` con `CODE,ID`.")


# Mostrar historial chat
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])


# =========================
# CORE CHAT
# =========================
user_text = st.chat_input("Escribí tu consulta (podés incluir modelo, URL y compatibilidad en la misma frase)")
if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        with st.spinner("Analizando..."):
            # 1) Si pegó JSON de ficha
            product_data: Optional[Dict[str, Any]] = None
            used_source = ""

            if looks_like_json_object(user_text):
                product_data = parse_json_from_text(user_text)
                if product_data:
                    used_source = "json pegado"

            # 2) URL /products/view/#### -> ID
            urls = extract_urls(user_text)
            ids = extract_view_ids_from_urls(urls)
            if not product_data and ids:
                product_id = ids[0]
                product_data = fetch_product_by_id(product_id)
                used_source = f"url view id={product_id}"

            # 3) Modelos en texto -> models.csv -> ID -> fetch
            models_map = load_models_map(MODELS_CSV_PATH)
            detected_codes = extract_candidate_codes(user_text)
            resolved_ids = []
            if not product_data and detected_codes:
                for c in detected_codes:
                    if c in models_map:
                        resolved_ids.append(models_map[c])
                if resolved_ids:
                    product_id = resolved_ids[0]
                    product_data = fetch_product_by_id(product_id)
                    used_source = f"models.csv {detected_codes[0]} -> id={product_id}"

            # Debug info
            if debug:
                st.markdown("**Debug**")
                st.code(
                    json.dumps(
                        {
                            "urls": urls,
                            "view_ids": ids,
                            "detected_codes": detected_codes,
                            "models_csv_size": len(models_map),
                            "resolved_ids_from_models": resolved_ids,
                            "used_source": used_source,
                            "product_got": bool(product_data),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    language="json",
                )

            # Si no hay producto, igual respondemos conversacional sin romper UX.
            if not product_data:
                msg = (
                    "No pude enganchar ningún producto con tu mensaje.\n\n"
                    "Para que funcione **sin URL**, necesito poder convertir `MODELO -> ProductId`.\n"
                    "👉 Solución práctica: subí/creá un `models.csv` en el repo con líneas tipo:\n\n"
                    "- `LM108-V2,5904`\n"
                    "- `IPC-4M-FA-ZERO,6964`\n\n"
                    "Mientras tanto, pegá una URL pública tipo:\n"
                    f"- `{PUBLIC_VIEW_PREFIX}####`\n"
                    "o pegá el **JSON** de la ficha (como hiciste antes)."
                )
                st.markdown(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            else:
                # Responder con Gemini si está, sino fallback por ficha
                response = gemini_generate(product_data, user_text)
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})

                # Mostrar “datos oficiales usados” colapsable
                with st.expander("Ver datos oficiales usados (ficha)"):
                    code = product_data.get("Code", "")
                    pid = product_data.get("ProductId", "")
                    stock = product_data.get("Stock", "")
                    price = product_data.get("Price", "")
                    ds = product_data.get("DataSheet", "")
                    st.write(f"- **Code:** {code}")
                    st.write(f"- **ProductId:** {pid}")
                    if stock != "":
                        st.write(f"- **Stock:** {stock}")
                    if price != "":
                        st.write(f"- **Price:** {price}")
                    if ds:
                        st.write(f"- **Datasheet:** {ds}")
                    st.code(json.dumps(compact_product(product_data), ensure_ascii=False, indent=2), language="json")
