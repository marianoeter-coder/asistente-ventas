import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from io import BytesIO
from pypdf import PdfReader

# ===============================
# CONFIGURACIÓN
# ===============================
st.set_page_config(page_title="Asistente Big Dipper", page_icon="🤖")

# ===============================
# LOGIN
# ===============================
if "acceso_concedido" not in st.session_state:
    st.session_state.acceso_concedido = False

def verificar_clave():
    st.title("🔒 Acceso Restringido")
    st.markdown("Herramienta interna de Big Dipper / Cygnus.")
    clave = st.text_input("Ingresa la contraseña del equipo:", type="password")
    if st.button("Entrar"):
        if clave == "Ventas2025":
            st.session_state.acceso_concedido = True
            st.rerun()
        else:
            st.error("Clave incorrecta")

if not st.session_state.acceso_concedido:
    verificar_clave()
    st.stop()

# ===============================
# GEMINI
# ===============================
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except Exception:
    st.error("⚠️ Falta configurar GOOGLE_API_KEY en Streamlit Secrets.")
    st.stop()

# ===============================
# UI
# ===============================
st.title("🤖 Asistente Técnico")
st.info("Pegá el link del producto o el PDF del datasheet y preguntá.")

url = st.text_input("🔗 Link del producto o datasheet:")

# ===============================
# FUNCIONES
# ===============================

@st.cache_data(show_spinner=False)
def leer_web(link):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BigDipperAssistant/1.0)"}
        r = requests.get(link, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for s in soup(["script", "style", "nav", "footer", "header", "aside"]):
            s.decompose()
        text = soup.get_text(separator="\n")
        text = "\n".join(t.strip() for t in text.splitlines() if t.strip())
        return text[:40000] if text else None
    except Exception:
        return None

@st.cache_data(show_spinner=False)
def encontrar_pdf_datasheet(link_pagina):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BigDipperAssistant/1.0)"}
        r = requests.get(link_pagina, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().endswith(".pdf"):
                return urljoin(link_pagina, href)
        return None
    except Exception:
        return None

@st.cache_data(show_spinner=False)
def leer_pdf_texto(pdf_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BigDipperAssistant/1.0)"}
        r = requests.get(pdf_url, headers=headers, timeout=30)
        r.raise_for_status()
        reader = PdfReader(BytesIO(r.content))
        parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
        text = "\n".join(parts).strip()
        return text[:40000] if text else None
    except Exception:
        return None

def elegir_modelo_disponible():
    modelos = genai.list_models()
    candidatos = []
    for m in modelos:
        methods = getattr(m, "supported_generation_methods", []) or []
        if "generateContent" in methods:
            candidatos.append(m.name)

    if not candidatos:
        raise RuntimeError("No hay modelos compatibles con esta API Key.")

    for pref in ["flash", "pro", "gemini"]:
        for name in candidatos:
            if pref in name.lower():
                return name
    return candidatos[0]

# ===============================
# CHAT
# ===============================
if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

with st.expander("🔧 Debug modelos Gemini"):
    try:
        st.write([{"name": m.name, "methods": m.supported_generation_methods} for m in genai.list_models()])
    except Exception as e:
        st.error(e)

# ===============================
# INPUT
# ===============================
if p := st.chat_input("Consulta técnica..."):
    if not url:
        st.warning("Pegá una URL primero.")
        st.stop()

    st.session_state.chat.append({"role": "user", "content": p})
    with st.chat_message("user"):
        st.markdown(p)

    with st.chat_message("assistant"):
        with st.spinner("Leyendo fuente oficial..."):

            pdf_url = None
            txt = None

            if url.lower().endswith(".pdf"):
                pdf_url = url
                txt = leer_pdf_texto(pdf_url)
            else:
                pdf_url = encontrar_pdf_datasheet(url)
                if pdf_url:
                    txt = leer_pdf_texto(pdf_url)

            if not txt:
                txt = leer_web(url)

            if pdf_url:
                st.caption(f"📄 Datasheet detectado: {pdf_url}")

            if not txt:
                st.error("No pude leer ni el datasheet ni la página.")
                st.stop()

            try:
                model_name = elegir_modelo_disponible()
                model = genai.GenerativeModel(model_name)
            except Exception as e:
                st.error(f"No pude seleccionar modelo Gemini: {e}")
                st.stop()

            prompt = (
                "Respondé SOLO usando la información del TEXTO.\n"
                "Si la respuesta no está en el TEXTO, respondé exactamente:\n"
                "\"❌ La página oficial no indica esa información.\"\n\n"
                "TEXTO:\n"
                f"{txt}\n\n"
                "PREGUNTA:\n"
                f"{p}"
            )

            try:
                res = model.generate_content(prompt)
                answer = getattr(res, "text", None) or "❌ No pude generar respuesta."
                st.markdown(answer)
                st.session_state.chat.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Error al consultar Gemini: {e}")


