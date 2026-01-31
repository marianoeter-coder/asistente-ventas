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
        if clave == "Ventas2025":  # <--- CLAVE DE ACCESO
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
except:
    st.error("⚠️ Falta configurar la API Key en Streamlit.")
    st.stop()

st.title("🤖 Asistente Técnico")
st.info("Pega el link del producto (Hikvision, Cygnus...) y pregunta.")

url = st.text_input("🔗 Link del producto:")

@st.cache_data(show_spinner=False)
def leer_web(link):
    try:
        r = requests.get(link, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        for s in soup(["script", "style", "nav", "footer"]): s.decompose()
        return soup.get_text()[:40000]
    except: return None

if "chat" not in st.session_state: st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]): st.markdown(m["content"])

if p := st.chat_input("Consulta técnica..."):
    if not url: st.warning("Pega una URL primero.")
    else:
        st.session_state.chat.append({"role":"user", "content":p})
        with st.chat_message("user"): st.markdown(p)
        
        with st.chat_message("assistant"):
            with st.spinner("Leyendo manual..."):
                txt = leer_web(url)
                if txt:
                   model = genai.GenerativeModel('gemini-1.5-flash')
                    prompt = f"Responde usando SOLO este texto web:\n\n{txt}\n\nPregunta: {p}"
                    res = model.generate_content(prompt)
                    st.markdown(res.text)
                    st.session_state.chat.append({"role":"assistant", "content":res.text})
                else: st.error("No pude leer la web.")
