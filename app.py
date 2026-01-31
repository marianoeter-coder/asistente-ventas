import streamlit as st
import google.generativeai as genai
import requests
import re
import pdfplumber

# ------------------------
# CONFIG
# ------------------------
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖", layout="centered")

# ------------------------
# SEGURIDAD
# ------------------------
if "auth" not in st.session_state:
    st.session_state.auth = False

def login():
    st.title("🔒 Acceso Interno")
    pwd = st.text_input("Contraseña del equipo", type="password")
    if st.button("Ingresar"):
        if pwd == "Ventas2025":
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Clave incorrecta")

if not st.session_state.auth:
    login()
    st.stop()

# ------------------------
# GEMINI
# ------------------------
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    st.error("Falta configurar GOOGLE_API_KEY")
    st.stop()

model = genai.GenerativeModel("gemini-1.5-flash")

# ------------------------
# UI
# ------------------------
st.title("🤖 Asistente de Ventas Big Dipper")

if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# ------------------------
# UTILIDADES
# ------------------------

def extract_models(text):
    text = text.upper()
    return list(set(re.findall(r"[A-Z]{2,}-[A-Z0-9\-]+", text)))

def fetch_product(code):
    try:
        r = requests.post(
            "https://www2.bigdipper.com.ar/api/Products/View",
            json={"ProductId": 0, "Code": code},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def read_pdf(url):
    try:
        r = requests.get(url, timeout=10)
        with open("temp.pdf", "wb") as f:
            f.write(r.content)
        with pdfplumber.open("temp.pdf") as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except:
        return ""

# ------------------------
# CHAT INPUT
# ------------------------

if user := st.chat_input("Escribí tu consulta (ej: ¿La IPC-4M-FA-ZERO sirve para exterior?)"):

    st.session_state.chat.append({"role":"user", "content":user})
    with st.chat_message("user"):
        st.markdown(user)

    with st.chat_message("assistant"):

        models = extract_models(user)

        # Si no puso modelos → pedirlos
        if not models:
            msg = "Decime el **modelo exacto** del producto y lo reviso."
            st.markdown(msg)
            st.session_state.chat.append({"role":"assistant", "content":msg})
            st.stop()

        context = ""
        found = False

        for m in models:
            prod = fetch_product(m)
            if prod:
                found = True
                context += f"\nPRODUCTO {m}\n"
                context += f"Nombre: {prod.get('DescriptionShort')}\n"
                context += f"Stock: {prod.get('Stock')}\n"
                context += f"Descripción: {prod.get('DescriptionLong')}\n"

                if prod.get("DataSheet"):
                    pdf_text = read_pdf(prod["DataSheet"])
                    context += f"\nDATASHEET:\n{pdf_text[:6000]}\n"

        if not found:
            msg = "No encontré esos modelos en Big Dipper."
            st.markdown(msg)
            st.session_state.chat.append({"role":"assistant", "content":msg})
            st.stop()

        # Prompt de vendedor
        prompt = f"""
Sos un asesor técnico de ventas de Big Dipper.
Respondé como vendedor profesional.

Usá SOLO la información técnica provista.
Si no se puede responder con certeza, decilo.

Información:
{context}

Pregunta del vendedor:
{user}
"""

        res = model.generate_content(prompt)
        st.markdown(res.text)
        st.session_state.chat.append({"role":"assistant", "content":res.text})




