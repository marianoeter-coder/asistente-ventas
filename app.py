import streamlit as st
import google.generativeai as genai
import requests
import re

# ===============================
# CONFIG
# ===============================
st.set_page_config(page_title="Asistente Big Dipper", page_icon="🤖")

# ===============================
# LOGIN
# ===============================
if "acceso_concedido" not in st.session_state:
    st.session_state.acceso_concedido = False

def verificar_clave():
    st.title("🔒 Acceso Restringido")
    st.markdown("Herramienta interna Big Dipper / Cygnus")
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

# ===============================
# GEMINI
# ===============================
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    st.error("Falta GOOGLE_API_KEY en Streamlit Secrets")
    st.stop()

# ===============================
# UI
# ===============================
st.title("🤖 Asistente Técnico Big Dipper")
st.info("Pegá el link del producto Big Dipper y preguntá.")

url = st.text_input("🔗 Link del producto:")

# ===============================
# FUNCIONES
# ===============================

def extraer_product_id(link):
    m = re.search(r"/view/(\d+)", link)
    return int(m.group(1)) if m else None

def obtener_producto(product_id):
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
CODIGO: {p.get('Code')}
NOMBRE: {p.get('DescriptionShort')}
PRECIO USD: {p.get('Price')}
STOCK: {p.get('Stock')}

DESCRIPCION TECNICA:
{p.get('DescriptionLong')}

PROTECCION: {p.get('IPRating','No especificado')}
POE: {"Si" if "PoE" in (p.get("DescriptionLong") or "") else "No indicado"}

LINK DATASHEET: {p.get('DataSheet')}
"""

def elegir_modelo():
    modelos = genai.list_models()
    for m in modelos:
        if "generateContent" in m.supported_generation_methods:
            if "flash" in m.name.lower():
                return m.name
    for m in modelos:
        if "generateContent" in m.supported_generation_methods:
            return m.name
    raise RuntimeError("No hay modelos Gemini disponibles.")

# ===============================
# CHAT
# ===============================
if "chat" not in st.session_state:
    st.session_state.chat = []

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# ===============================
# INPUT
# ===============================
if p := st.chat_input("Consulta técnica..."):
    if not url:
        st.warning("Pegá un link de Big Dipper.")
        st.stop()

    pid = extraer_product_id(url)
    if not pid:
        st.error("No pude detectar el ID del producto en el link.")
        st.stop()

    producto = obtener_producto(pid)
    if not producto:
        st.error("No pude consultar la base de datos de Big Dipper.")
        st.stop()

    contexto = producto_a_texto(producto)

    st.caption(f"📦 Producto: {producto.get('DescriptionShort')} | Stock: {producto.get('Stock')}")

    st.session_state.chat.append({"role": "user", "content": p})
    with st.chat_message("user"):
        st.markdown(p)

    with st.chat_message("assistant"):
        with st.spinner("Consultando base Big Dipper..."):
            try:
                model = genai.GenerativeModel(elegir_modelo())
                prompt = f"""
Respondé SOLO usando los datos oficiales del producto.
Si la información no está presente, respondé exactamente:
"❌ La ficha oficial no indica esa información."

FICHA DEL PRODUCTO:
{contexto}

PREGUNTA:
{p}
"""
                res = model.generate_content(prompt)
                answer = res.text
                st.markdown(answer)
                st.session_state.chat.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Error Gemini: {e}")



