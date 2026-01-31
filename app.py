import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

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
        if clave == "Ventas2025":  # <--- CLAVE DE ACCESO (recomendado mover a st.secrets)
            st.session_state.acceso_concedido = True
            st.rerun()
        else:
            st.error("Clave incorrecta")

if not st.session_state.acceso_concedido:
    verificar_clave()
    st.stop()

# --- APP ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except Exception:
    st.error("⚠️ Falta configurar la API Key en Streamlit (GOOGLE_API_KEY).")
    st.stop()

st.title("🤖 Asistente Técnico")
st.info("Pegá el link del producto (Hikvision, Cygnus...) y preguntá.")

url = st.text_input("🔗 Link del producto:")

@st.cache_data(show_spinner=False)
def leer_web(link: str):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; BigDipperAssistant/1.0)"
        }
        r = requests.get(link, headers=headers, timeout=15)
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

if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if p := st.chat_input("Consulta técnica..."):
    if not url:
        st.warning("Pegá una URL primero.")
    else:
        st.session_state.chat.append({"role": "user", "content": p})
        with st.chat_message("user"):
            st.markdown(p)

        with st.chat_message("assistant"):
            with st.spinner("Leyendo fuente oficial..."):
                txt = leer_web(url)

                if not txt:
                    st.error("No pude leer la web (bloqueo del sitio, timeout o contenido vacío).")
                else:
                    model = genai.GenerativeModel("gemini-1.5-flash")

                    # Prompt anti-alucinación (sin triple comillas para evitar líos de indentación)
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

