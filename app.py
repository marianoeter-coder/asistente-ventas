import streamlit as st
import google.generativeai as genai
import requests
import re
import pdfplumber
import io

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Asistente de Ventas Big Dipper", page_icon="🤖")
st.title("🤖 Asistente de Ventas Big Dipper")

# =========================
# API KEY
# =========================
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    st.error("Falta configurar GOOGLE_API_KEY en secrets.")
    st.stop()

# =========================
# BIG DIPPER API
# =========================
SEARCH_API = "https://www.bigdipper.com.ar/api/Products/Search"
VIEW_API = "https://www.bigdipper.com.ar/api/Products/View"

# =========================
# MODELO DETECTOR
# =========================
def extract_models(text):
    pattern = r'[A-Z]{2,10}(?:-[A-Z0-9]+){1,6}'
    return list(set(re.findall(pattern, text.upper())))

# =========================
# BUSCAR PRODUCTO
# =========================
def get_product(model_code):
    try:
        r = requests.post(SEARCH_API, json={"Text": model_code}, timeout=10)
        data = r.json()

        if "Items" not in data or len(data["Items"]) == 0:
            return None

        product_id = data["Items"][0]["ProductId"]

        r = requests.post(VIEW_API, json={"ProductId": product_id}, timeout=10)
        return r.json()
    except:
        return None

# =========================
# LEER PDF
# =========================
def read_pdf(url):
    try:
        r = requests.get(url, timeout=10)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        return text[:35000]
    except:
        return ""

# =========================
# CHAT
# =========================
if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# =========================
# INPUT
# =========================
question = st.chat_input("Escribí tu consulta (ej: ¿La IPC-4M-FA-ZERO sirve para exterior y con qué XVR funciona?)")

if question:
    st.session_state.chat.append({"role":"user","content":question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Analizando productos Big Dipper..."):

            models = extract_models(question)

            if not models:
                st.error("No se detectaron modelos en la consulta.")
                st.stop()

            context = ""

            for model in models:
                product = get_product(model)
                if not product:
                    continue

                context += f"\n\nMODELO: {model}\n"
                context += product.get("DescriptionLong","") + "\n"

                if product.get("DataSheet"):
                    pdf_text = read_pdf(product["DataSheet"])
                    context += "\nDATASHEET:\n" + pdf_text

            if not context:
                st.error("No encontré esos modelos en Big Dipper.")
                st.stop()

            prompt = f"""
Sos un asesor técnico y comercial de Big Dipper.
Respondé de forma clara, útil y profesional.
Usá SOLO la información siguiente.

{context}

Pregunta:
{question}
"""

            model = genai.GenerativeModel("gemini-1.5-pro")
            res = model.generate_content(prompt)

            st.markdown(res.text)
            st.session_state.chat.append({"role":"assistant","content":res.text})



