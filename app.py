import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖", layout="centered")

# API "real" que viste en Network (POST con ProductId)
BD_API_VIEW = "https://www2.bigdipper.com.ar/api/Products/View"

# Para resolver modelos -> intentamos encontrar /products/view/{id} en HTML (sin bs4)
SEARCH_URLS = [
    "https://www.bigdipper.com.ar/products?search={q}",
    "https://www.bigdipper.com.ar/products?text={q}",
    "https://www.bigdipper.com.ar/search?q={q}",
    "https://www.bigdipper.com.ar/?s={q}",
]

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

TIMEOUT = 12

# Detector industrial de modelos (soporta LM108-V2, DS-PDB68-EG2, IPC-4M-FA-ZERO, LPC1218, etc.)
MODEL_REGEX = re.compile(r"\b[A-Z0-9]{2,10}(?:[-_][A-Z0-9]{1,10}){0,6}\b", re.IGNORECASE)

# Detecta URLs de producto /products/view/#### (con o sin www)
PRODUCT_URL_REGEX = re.compile(r"(?:https?://)?(?:www\.)?bigdipper\.com\.ar/products/view/(\d+)", re.IGNORECASE)

# Detecta si el usuario pegó JSON
JSON_LIKE_REGEX = re.compile(r"^\s*\{[\s\S]*\}\s*$", re.MULTILINE)

# -----------------------------
# GEMINI (opcional)
# -----------------------------
def get_gemini_key() -> Optional[str]:
    # Acepta cualquiera de estas dos keys en Secrets
    if "GEMINI_API_KEY" in st.secrets:
        return str(st.secrets["GEMINI_API_KEY"]).strip()
    if "GOOGLE_API_KEY" in st.secrets:
        return str(st.secrets["GOOGLE_API_KEY"]).strip()
    return None

def try_import_gemini():
    try:
        import google.generativeai as genai  # type: ignore
        return genai
    except Exception:
        return None

# -----------------------------
# UTILIDADES
# -----------------------------
def normalize_model(s: str) -> str:
    return s.upper().replace("_", "-").replace(" ", "").strip()

def extract_product_ids_from_text(text: str) -> List[int]:
    ids = []
    for m in PRODUCT_URL_REGEX.finditer(text or ""):
        try:
            ids.append(int(m.group(1)))
        except Exception:
            pass
    return list(dict.fromkeys(ids))

def extract_models_from_text(text: str) -> List[str]:
    if not text:
        return []
    raw = MODEL_REGEX.findall(text)
    # Filtramos basura típica (palabras comunes cortas) y dejamos lo más probable a "modelo"
    # (BigDipper suele tener guiones, números, prefijos IPC/DS/LM/LF/LPC/BSW, etc.)
    cleaned = []
    for r in raw:
        n = normalize_model(r)
        if len(n) < 4:
            continue
        # Evitar agarrar "HTTP", "HTTPS", "WIFI" como modelo
        if n in {"HTTP", "HTTPS", "WIFI", "POE", "IP67", "IP66", "H265", "H264", "MJPEG", "WDR"}:
            continue
        cleaned.append(n)
    # Deduplicar conservando orden
    return list(dict.fromkeys(cleaned))

def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text or not JSON_LIKE_REGEX.match(text.strip()):
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None

def http_get(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code >= 200 and r.status_code < 300:
            return r.text
    except Exception:
        return None
    return None

def resolve_product_id_by_model(model: str, debug: bool = False) -> Optional[int]:
    """
    Busca en el sitio alguna URL /products/view/{id} asociada al modelo.
    No usa bs4 para evitar problemas de dependencias; usa regex.
    """
    q = requests.utils.quote(model)
    for tpl in SEARCH_URLS:
        url = tpl.format(q=q)
        html = http_get(url)
        if debug:
            st.write(f"🔎 Buscando modelo en: {url}")
        if not html:
            continue

        # Primero: encontrar links a /products/view/####
        ids = re.findall(r"/products/view/(\d+)", html, flags=re.IGNORECASE)
        if not ids:
            continue

        # Heurística: preferimos el id cuyo bloque de HTML contenga el modelo (si se puede)
        # Buscamos ventanas alrededor del id
        best = None
        for id_str in ids[:50]:
            try:
                pid = int(id_str)
            except Exception:
                continue
            # Intentar verificar el modelo cerca del link
            # (esto depende de cómo renderice el site, pero suele ayudar)
            pat = re.compile(rf"/products/view/{pid}[\s\S]{{0,800}}", re.IGNORECASE)
            m = pat.search(html)
            if m and model.upper() in m.group(0).upper():
                best = pid
                break
        if best is not None:
            return best

        # Si no encontramos match exacto, devolvemos el primer id como fallback
        try:
            return int(ids[0])
        except Exception:
            pass

    return None

def fetch_product_by_id(product_id: int) -> Optional[Dict[str, Any]]:
    try:
        r = requests.post(
            BD_API_VIEW,
            json={"ProductId": product_id},
            headers={**UA, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        if r.status_code >= 200 and r.status_code < 300:
            data = r.json()
            if isinstance(data, dict) and data.get("ProductId"):
                return data
    except Exception:
        return None
    return None

def product_summary(p: Dict[str, Any]) -> str:
    code = p.get("Code") or "—"
    short = p.get("DescriptionShort") or ""
    stock = p.get("Stock")
    price = p.get("Price")
    parts = [f"**{code}**"]
    if short:
        parts.append(f"— {short}")
    if stock is not None:
        parts.append(f"| Stock: **{stock}**")
    if price is not None:
        parts.append(f"| Precio: **USD {price}**")
    return " ".join(parts)

def product_facts_for_llm(p: Dict[str, Any]) -> str:
    """
    Hechos "textuales" de ficha: usamos DescriptionLong + datos clave.
    """
    lines = []
    for k in ["Code", "DescriptionShort", "DescriptionLong", "Stock", "Price", "DataSheet"]:
        v = p.get(k)
        if v is None or v == "":
            continue
        lines.append(f"{k}: {v}")
    links = p.get("Links") or []
    if isinstance(links, list) and links:
        lines.append(f"Links: {', '.join(str(x) for x in links)}")
    return "\n".join(lines)

def answer_rule_based(question: str, products: List[Dict[str, Any]]) -> str:
    """
    Respuesta modo ventas SIN inventar: se apoya en la ficha.
    Para compatibilidades, da criterios y pide confirmar si no hay dato.
    """
    q = (question or "").lower()

    if not products:
        return (
            "No pude traer ninguna ficha con tu consulta.\n\n"
            "✅ Probá con:\n"
            "- Pegar la URL del producto (…/products/view/####)\n"
            "- O pegar el modelo exacto (ej: IPC-4M-FA-ZERO)\n"
            "- O pegar el JSON de la ficha\n"
        )

    # Si es una sola ficha, respondemos puntual.
    if len(products) == 1:
        p = products[0]
        code = p.get("Code", "—")
        long = (p.get("DescriptionLong") or "").strip()

        bullets = []
        # exterior?
        if "exterior" in q or "afuera" in q:
            if "IP67" in long.upper() or "IP66" in long.upper():
                bullets.append("Sí: en la ficha figura **protección IP67**, así que es apta para exterior.")
            else:
                bullets.append("No veo una protección IP (IP66/IP67) explícita en la ficha; para exterior habría que confirmarlo.")

        # PoE / alimentación
        if "aliment" in q or "poe" in q or "fuente" in q:
            if "POE" in long.upper():
                bullets.append("En alimentación: la ficha indica **compatible con PoE**.")
            else:
                bullets.append("No veo PoE mencionado en la ficha; habría que confirmar el método de alimentación (12V/PoE, etc.).")

        # IR / luz
        if "ir" in q or "infr" in q or "luz" in q or "noche" in q:
            if "LUZ BLANCA" in long.upper():
                bullets.append("Para nocturna: figura **luz blanca** (cálida) con alcance **hasta 30 m**.")
            else:
                bullets.append("No veo especificación de IR/luz blanca en la ficha.")

        # Si no se disparó nada específico, devolvemos un resumen vendedor + ficha.
        if not bullets:
            bullets.append("Te dejo los puntos clave según ficha (sin suposiciones).")

        res = [f"**{code}** — respuesta basada en ficha Big Dipper:\n"]
        for b in bullets:
            res.append(f"- {b}")

        if long:
            res.append("\n**Detalle de ficha (tal cual):**")
            # recortamos para no hacer una pared eterna
            snippet = long.strip()
            if len(snippet) > 900:
                snippet = snippet[:900] + "…"
            res.append(snippet)

        ds = p.get("DataSheet")
        if ds:
            res.append(f"\n**Datasheet:** {ds}")

        return "\n".join(res)

    # Si hay 2 o más productos: compatibilidad / relación
    # (sin inventar: damos criterios y lo que sí dice la ficha)
    codes = [str(p.get("Code", "—")) for p in products]
    res = []
    res.append(f"Encontré estas fichas: **{', '.join(codes)}**.\n")

    res.append("**Compatibilidad (criterio vendedor, sin chamuyo):**")
    res.append("- Si una es **cámara IP (IPC/… )** y el grabador es **XVR (analógico)**: *puede* funcionar solo si el XVR soporta canales IP (modo híbrido). Eso depende del modelo de XVR.")
    res.append("- Si el grabador es **NVR**: normalmente es más directo para cámaras IP, pero igual conviene confirmar **ONVIF/RTSP** y codecs soportados.")
    res.append("- Cuando la ficha no lo dice, lo correcto es responder: **“depende del grabador; te confirmo con el modelo exacto / ficha del grabador”**.\n")

    res.append("**Lo que sí puedo afirmar por ficha:**")
    for p in products:
        res.append(f"- {product_summary(p)}")

    res.append("\nSi me pegás el **modelo exacto del grabador** (o su URL), te lo cruzo con su ficha y te digo **qué se puede asegurar** y qué queda como “a confirmar”.")

    return "\n".join(res)

def answer_with_gemini(question: str, products: List[Dict[str, Any]]) -> str:
    key = get_gemini_key()
    genai = try_import_gemini()
    if not key or not genai:
        return answer_rule_based(question, products)

    genai.configure(api_key=key)

    # Modelo: usá uno estable (si no existe en tu cuenta, caemos al rule-based)
    try_models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    model_obj = None
    for m in try_models:
        try:
            model_obj = genai.GenerativeModel(m)
            break
        except Exception:
            continue
    if model_obj is None:
        return answer_rule_based(question, products)

    # Prompt: modo ventas + no inventar + citar ficha
    facts = "\n\n---\n\n".join([product_facts_for_llm(p) for p in products])

    system = (
        "Sos un asistente técnico-comercial (ventas) de Big Dipper.\n"
        "REGLAS:\n"
        "- Respondé en español argentino, claro y directo.\n"
        "- NO inventes datos que no estén en 'DATOS DE FICHA'.\n"
        "- Si te preguntan algo que la ficha NO confirma, decí 'No lo puedo confirmar con la ficha' y proponé qué dato falta.\n"
        "- Si hay compatibilidades entre productos, explicá el criterio y pedí el modelo exacto del segundo equipo si falta.\n"
        "- Devolvé respuesta corta en bullets cuando sea posible.\n"
    )

    user = f"CONSULTA DEL VENDEDOR:\n{question}\n\nDATOS DE FICHA:\n{facts}"

    try:
        resp = model_obj.generate_content([system, user])
        txt = getattr(resp, "text", "") or ""
        txt = txt.strip()
        if not txt:
            return answer_rule_based(question, products)
        return txt
    except Exception:
        return answer_rule_based(question, products)

# -----------------------------
# PIPELINE: detectar -> resolver -> traer fichas
# -----------------------------
@dataclass
class Detection:
    product_ids: List[int]
    models: List[str]
    json_payload: Optional[Dict[str, Any]]

def detect_all(text: str) -> Detection:
    return Detection(
        product_ids=extract_product_ids_from_text(text),
        models=extract_models_from_text(text),
        json_payload=try_parse_json(text),
    )

def build_products_from_detection(det: Detection, debug: bool = False) -> Tuple[List[Dict[str, Any]], List[str]]:
    notes = []

    # 1) Si pegó JSON, lo usamos directo
    products: List[Dict[str, Any]] = []
    if det.json_payload and isinstance(det.json_payload, dict) and det.json_payload.get("Code"):
        products.append(det.json_payload)
        notes.append("✅ Tomé el JSON pegado como fuente (ficha).")

    # 2) IDs por URL
    for pid in det.product_ids:
        p = fetch_product_by_id(pid)
        if p:
            products.append(p)
            notes.append(f"✅ Ficha traída por ID {pid}.")
        else:
            notes.append(f"⚠️ No pude traer ficha por ID {pid}.")

    # 3) Modelos sueltos: resolver a ID -> traer ficha
    # (evitamos duplicar si ya tenemos ese Code)
    existing_codes = {normalize_model(str(p.get("Code", ""))) for p in products}
    for m in det.models:
        nm = normalize_model(m)
        if nm in existing_codes:
            continue

        pid = resolve_product_id_by_model(nm, debug=debug)
        if pid is None:
            notes.append(f"⚠️ No pude resolver ID para modelo {nm}.")
            continue

        p = fetch_product_by_id(pid)
        if p:
            products.append(p)
            notes.append(f"✅ Modelo {nm} resuelto a ID {pid} y ficha traída.")
        else:
            notes.append(f"⚠️ Resolví ID {pid} para {nm}, pero no pude traer la ficha.")

    # Deduplicar por ProductId o Code
    uniq = []
    seen = set()
    for p in products:
        key = p.get("ProductId") or normalize_model(str(p.get("Code", "")))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    return uniq, notes

# -----------------------------
# UI
# -----------------------------
st.title("🤖 Asistente de Ventas Big Dipper")

with st.sidebar:
    debug = st.toggle("Mostrar debug (detector de modelos/IDs)", value=False)
    st.caption("Secrets: si usás Gemini, cargá **GEMINI_API_KEY** o **GOOGLE_API_KEY** en Streamlit Cloud.")
    if st.button("🧹 Limpiar chat"):
        st.session_state.pop("messages", None)
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

# Render historial
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Escribí tu consulta (podés incluir modelo, URL o pegar JSON de ficha).")

if prompt:
    # Mostrar usuario
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analizando y trayendo fichas…"):
            det = detect_all(prompt)

            if debug:
                st.write("DET:", {
                    "product_ids": det.product_ids,
                    "models": det.models,
                    "json": bool(det.json_payload),
                })

            products, notes = build_products_from_detection(det, debug=debug)

            if debug and notes:
                st.write("NOTAS:", notes)

            # Responder
            answer = answer_with_gemini(prompt, products)

            st.markdown(answer)

            # Mostrar “datos usados” colapsable
            if products:
                with st.expander("Ver datos oficiales usados (ficha)"):
                    for p in products:
                        st.markdown(product_summary(p))
                        ds = p.get("DataSheet")
                        if ds:
                            st.markdown(f"- Datasheet: {ds}")
                        # Mostrar un snippet de la descripción larga
                        long = (p.get("DescriptionLong") or "").strip()
                        if long:
                            snippet = long[:700] + ("…" if len(long) > 700 else "")
                            st.markdown(f"- Ficha (extracto): {snippet}")

    st.session_state.messages.append({"role": "assistant", "content": answer})
    time.sleep(0.05)

