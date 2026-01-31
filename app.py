import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Asistente Big Dipper", page_icon="🤖")

# --- SEGURIDAD ---
if "acceso_concedido" not in st.session_state:
    st.session_state.acceso_concedido = False

def verificar_clave():
    st.title("🔒 Acceso Restringido")
    st.markdown("Herramienta interna de Big Dipper / Cygnus.")
    clave = st.text_input("Ingresa la contraseña del equipo:", type="password")
    if st.button("Entrar"):
        # Recomendado: mover la clave a st.secrets["APP_PASSWORD"]
        if clave == "Ventas2025":
            st.session_state.acceso_concedido = True
            st.rerun()
        else:
            st.error("Clave incorrecta")

if not st.session_state.acceso_concedido:
    verificar_clave()
    st.stop()

# --- GEMINI API KEY ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except Exception:
    st.error("⚠️ Falta configurar GOOGLE_API_KEY en Streamlit Secrets.")
    st.stop()

# --- UI ---
st.title("🤖 Asistente Técnico")
st.info("Pegá el link del producto (Hikvision, Cygnus...) y preguntá.")

url = st.text_input("🔗 Link del producto:")

# --- (Opcional) Whitelist de dominios permitidos ---
# Si querés permitir cualquier URL, dejalo vacío: DOMINIOS_PERMITIDOS = []
DOMINIOS_PERMITIDOS = []

def dominio_permitido(link: str) -> bool:
    if not DOMINIOS_PERMITIDOS:
        return True
    try:
        host = urlparse(link).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in DOMINIOS_PERMITIDOS)
    except Exception:
        return False

@st.cache_data(show_spinner=False)
def leer_web(link: str):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; BigDipperAssistant/1.0)"
        }
        r = requests.get(link, headers=headers, timeout=20)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        # limpiar basura común
        for s in soup(["script", "style", "nav", "footer", "header", "aside"]):
            s.decompose()

        text = soup.get_text(separator="\n")
        text = "\n".join([line.strip() for line in text.splitlines() if line.strip()])

        return text[:40000] if text else None
    except Exception:
        return None

def elegir_modelo_disponible():
    """
    Devuelve un nombre de modelo disponible que soporte generateContent,
    basado en los modelos habilitados por la API key actual.
    """
    try:
        modelos = genai.list_models()
    except Exception as e:
        raise RuntimeError(f"No pude listar modelos con esta API key: {e}")

    candidatos = []
    for m in modelos:
        methods = getattr(m, "supported_generation_methods", []) or []
        if "generateContent" in methods:
            candidatos.append(m.name)  # suele venir como "models/xxxx"

    if not candidatos:
        raise RuntimeError("Con esta API key no hay modelos que soporten generateContent.")

    # Prioridad razonable: flash > pro > gemini
    prioridad = ["flash", "pro", "gemini"]
    for p in prioridad:
        for name in candidatos:
            if p in name.lower():
                return name

    return candidatos[0]

# --- Chat state ---
if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# --- Debug opcional ---
with st.expander("🔧 Debug (modelos disponibles)"):
    try:
        st.write([{
            "name": m.name,
            "methods": getattr(m, "supported_generation_methods", [])
        } for m in genai.list_models()])
    except Exception as e:
        st.error(e)

# --- Input principal ---
if p := st.chat_input("Consulta técnica..."):
    if not url:
        st.warning("Pegá una URL primero.")
        st.stop()

    if not dominio_permitido(url):
        st.error("❌ Dominio no permitido. Pegá un link oficial autorizado.")
        st.stop()

    st.session_state.chat.append({"role": "user", "content": p})
    with st.chat_message("user"):
        st.markdown(p)

    with st.chat_message("assistant"):
        with st.spinner("Leyendo fuente oficial..."):
            txt = leer_web(url)

            if not txt:
                st.error("No pude leer la web (bloqueo del sitio, timeout o contenido vacío).")
                st.stop()

            # Elegir modelo compatible con TU key (evita 404 NotFound)
            try:
                model_name = elegir_modelo_disponible()
                model = genai.GenerativeModel(model_name)
            except Exception as e:
                st.error(f"Error al elegir modelo: {e}")
                st.stop()

            # Prompt anti-alucinación
            prompt = (
                "INSTRUCCIONES:\n"
                "1) Respondé SOLO con información que esté explícitamente en el TEXTO.\n"
                "2) Si la respuesta NO está en el TEXTO, respondé EXACTAMENTE:\n"
                "\"❌ La página oficial no indica esa información.\"\n"
                "3) Respondé breve y técnico. Si aplica, usá viñetas.\n\n"
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
                st.error(f"Error al consultar el modelo: {e}")

