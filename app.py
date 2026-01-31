import streamlit as st
import google.generativeai as genai
import requests

# ================= CONFIG =================

st.set_page_config(page_title="Asistente Big Dipper", page_icon="🤖")

# ================= SEGURIDAD =================

if "acceso" not in st.session_state:
    st.session_state.acceso = False

def login():
    st.title("🔒 Acceso Interno Big Dipper")
    clave = st.text_input("Contraseña", type="password")
    if st.button("Entrar"):
        if clave == "Ventas2025":
            st.session_state.acceso = True
            st.rerun()
        else:
            st.error("Clave incorrecta")

if not st.session_state.acceso:
    login()
    st.stop()

# ================= GEMINI =================

try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    st.error("Falta GOOGLE_API_KEY en Streamlit Secrets")
    st.stop()

model = genai.GenerativeModel("gemini-pro")

# ================= FUNCIONES BIG DIPPER =================

def buscar_producto_por_codigo(code):
    try:
        r = requests.post(
            "https://www.bigdipper.com.ar/api/Products/FindByCode",
            json={"Code": code},
            timeout=10
        )
        data = r.json()
        if "ProductId" not in data:
            return None
        return data["ProductId"]
    except:
        return None

def obtener_producto(product_id):
    try:
        r = requests.post(
            "https://www.bigdipper.com.ar/api/Products/View",
            json={"ProductId": product_id},
            timeout=10
        )
        return r.json()
    except:
        return None

# ================= IA =================

def responder_ventas(pregunta, productos):
    contexto = ""

    for p in productos:
        contexto += f"""
Producto: {p['Code']} - {p['DescriptionShort']}
Stock: {p['Stock']}
Descripción técnica:
{p['DescriptionLong']}
---
"""

    prompt = f"""
Sos un asistente técnico de ventas de Big Dipper.

Respondé SOLO usando estos datos oficiales:
{contexto}

Si algo no está explícitamente en los datos:
→ Inferilo técnicamente como un vendedor experto.
→ NO respondas "no figura en la ficha".

Pregunta del vendedor:
{pregunta}
"""

    return model.generate_content(prompt).text

# ================= UI =================

st.title("🤖 Asistente de Ventas Big Dipper")

if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

consulta = st.chat_input("Ej: ¿La IPC-4M-FA-ZERO sirve para exterior y con qué XVR funciona?")

if consulta:
    st.session_state.chat.append({"role":"user","content":consulta})
    with st.chat_message("user"):
        st.markdown(consulta)

    with st.chat_message("assistant"):
        with st.spinner("Buscando productos Big Dipper..."):

            # Extraer modelos de la consulta
            palabras = consulta.replace("?", "").replace(",", "").split()
            modelos = [p for p in palabras if "-" in p and len(p) > 5]

            productos = []

            for m in modelos:
                pid = buscar_producto_por_codigo(m)
                if pid:
                    prod = obtener_producto(pid)
                    if prod:
                        productos.append(prod)

            if not productos:
                st.error("No encontré esos modelos en Big Dipper.")
            else:
                respuesta = responder_ventas(consulta, productos)
                st.markdown(respuesta)
                st.session_state.chat.append({"role":"assistant","content":respuesta})




