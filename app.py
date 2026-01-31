import streamlit as st
import requests
import re
import google.generativeai as genai

# ---------------- CONFIG ----------------
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
MODEL = genai.GenerativeModel("gemini-1.5-flash")

BASE = "https://www.bigdipper.com.ar/api"

# ---------------- UTILIDADES ----------------

def normalize(text):
    return re.sub(r"[^A-Z0-9]", "", text.upper())

def extract_models(text):
    return re.findall(r"[A-Z]{2,}-?[A-Z0-9]{3,}-?[A-Z0-9]{2,}", text.upper())

# ---------------- BUSCADOR INTELIGENTE ----------------

def search_big_dipper(query):
    url = f"{BASE}/Products/Search"
    r = requests.get(url, params={"search": query}, timeout=10)
    if r.status_code != 200:
        return []
    return r.json()

def score_match(user_code, product):
    u = normalize(user_code)
    p = normalize(product["Code"] + " " + product.get("DescriptionShort", "") + " " + product.get("DescriptionLong", ""))
    score = 0
    for part in re.findall(r"[A-Z0-9]{3,}", u):
        if part in p:
            score += 1
    return score

def find_best_product(user_code):
    candidates = search_big_dipper(user_code)
    if not candidates:
        return None

    ranked = [(score_match(user_code, p), p) for p in candidates]
    ranked.sort(reverse=True, key=lambda x: x[0])

    if ranked[0][0] == 0:
        return None

    return ranked[0][1]

# ---------------- GÉMINI VENDEDOR ----------------

def ask_gemini(products, question):
    context = ""
    for p in products:
        context += f"""
PRODUCTO: {p['Code']}
DESCRIPCION: {p['DescriptionLong']}
STOCK: {p['Stock']}
DATASHEET: {p['DataSheet']}
"""

    prompt = f"""
Sos un asesor técnico de Big Dipper.
Usá SOLO la información oficial del producto para responder.

Si el dato no está en la ficha, razoná como un vendedor experto.
No digas "no se puede saber" si es obvio por el tipo de producto.

CONTEXTO:
{context}

PREGUNTA:
{question}

Respondé claro, en español argentino, orientado a ventas técnicas.
"""

    return MODEL.generate_content(prompt).text

# ---------------- UI ----------------

st.set_page_config(page_title="Asistente de Ventas Big Dipper")

st.title("🤖 Asistente de Ventas Big Dipper")

query = st.chat_input("Ej: ¿La IPC-4M-FA-ZERO sirve para exterior y con qué XVR funciona?")

if query:
    st.chat_message("user").write(query)

    models = extract_models(query)

    if not models:
        st.chat_message("assistant").error("No detecté ningún modelo en la consulta.")
    else:
        products = []
        for m in models:
            p = find_best_product(m)
            if p:
                products.append(p)

        if not products:
            st.chat_message("assistant").error("No pude asociar esos modelos a productos reales.")
        else:
            st.chat_message("assistant").write("🔎 Productos identificados:")
            for p in products:
                st.write(f"**{p['Code']}** — Stock: {p['Stock']}")

            answer = ask_gemini(products, query)
            st.chat_message("assistant").success(answer)




