import streamlit as st
import google.generativeai as genai
import requests
import re

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖")

# ==========================================
# LOGIN
# ==========================================
if "acceso_concedido" not in st.session_state:
    st.session_state.acceso_concedido = False

def verificar_clave():
    st.title("🔒 Acceso Restringido")
    st.markdown("Asistente interno Big Dipper / Cygnus")
    clave = st.text_input("Contraseña:", type="password")
    if st.button("Entrar"):
        if clave == "Ventas2025":
            st.session_state.acceso_concedido = True
            st.rerun()
        else:
            st.error("Clave incorrecta")

if not st.session_state.acceso_concedido:
    verificar_clave()
    st.stop()

# ==========================================
# GEMINI
# ==========================================
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    st.error("Falta GOOGLE_API_KEY en Streamlit Secrets")
    st.stop()

# ==========================================
# UI
# ==========================================
st.title("🤖 Asistente de Ventas Big Dipper")
st.info("Escribí tu consulta con modelos. Ej: '¿La IPC-4M-FA-ZERO sirve para boliche y funciona con el XVR-AHD-410?'")

# ==========================================
# FUNCIONES
# ==========================================

def extraer_modelos(texto):
    return list(set(re.findall(r"[A-Z]{2,}-[A-Z0-9\-]+", texto.upper())))

def buscar_bigdipper_por_codigo(codigo):
    try:
        r = requests.post(
            "https://www2.bigdipper.com.ar/api/Products/Search",
            json={"Search": codigo},
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        if data and len(data) > 0:
            return data[0]
    except:
        return None

def obtener_producto_bigdipper(product_id):
    try:
        r = requests.post(
            "https://www2.bigdipper.com.ar/api/Products/View",
            json={"ProductId": product_id},
            timeout=15
        )
        r.raise_for_status()
        return r.json()
    except:
        return None

def producto_a_texto(p):
    return f"""
MODELO: {p.get('Code')}
NOMBRE: {p.get('DescriptionShort')}
STOCK: {p.get('Stock')}
PRECIO USD: {p.get('Price')}

DESCRIPCION TECNICA:
{p.get('DescriptionLong')}

DATASHEET:
{p.get('DataSheet')}
"""

def elegir_modelo_gemini():
    modelos = genai.list_models()
    for m in modelos:
        if "generateContent" in m.supported_generation_methods and "flash" in m.name.lower():
            return m.name
    for m in modelos:
        if "generateContent" in m.supported_generation_methods:
            return m.name
    raise RuntimeError("No hay modelos Gemini disponibles")

# ==========================================
# CHAT
# ==========================================
if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# ==========================================
# INPUT
# ==========================================
if user_input := st.chat_input("Consulta..."):
    st.session_state.chat.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Analizando productos..."):
            modelos = extraer_modelos(user_input)

            contexto = ""

            for m in modelos:
                prod = buscar_bigdipper_por_codigo(m)
                if prod:
                    ficha = obtener_producto_bigdipper(prod["ProductId"])
                    if ficha:
                        contexto += producto_a_texto(ficha)

            if not contexto:
                st.error("No pude encontrar modelos Big Dipper en la consulta.")
                st.stop()

            try:
                model = genai.GenerativeModel(elegir_modelo_gemini())
                prompt = f"""
Eres el asistente de ventas técnicas de Big Dipper.

Usa solo la información de las fichas oficiales.
No inventes datos técnicos.
Sí puedes inferir usos comerciales y compatibilidad basándote en las características.

FICHAS DE PRODUCTOS:
{contexto}

PREGUNTA DEL VENDEDOR:
{user_input}
"""
                res = model.generate_content(prompt)
                answer = res.text
                st.markdown(answer)
                st.session_state.chat.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Error Gemini: {e}")



