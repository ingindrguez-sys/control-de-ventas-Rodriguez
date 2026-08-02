
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from html import escape
import uuid
import os
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "ventas.db"
ASSETS_DIR = APP_DIR / "assets"
PRODUCT_IMAGES = ASSETS_DIR / "products"
BRANDING_DIR = ASSETS_DIR / "branding"
PRODUCT_IMAGES.mkdir(parents=True, exist_ok=True)
BRANDING_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(
    page_title="Embutidos Rodríguez | Ventas",
    page_icon="🐷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- Base de datos ----------
def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def column_exists(conn, table, column):
    return column in [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

def init_db():
    conn = connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        presentation TEXT NOT NULL,
        weight_kg REAL NOT NULL DEFAULT 1,
        cost_per_kg REAL NOT NULL DEFAULT 0,
        public_price_per_kg REAL NOT NULL DEFAULT 0,
        wholesale_price_per_kg REAL NOT NULL DEFAULT 0,
        restaurant_price_per_kg REAL NOT NULL DEFAULT 0,
        stock_kg REAL NOT NULL DEFAULT 0,
        min_stock_kg REAL NOT NULL DEFAULT 0,
        image_path TEXT,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_name TEXT NOT NULL,
        contact_name TEXT,
        phone TEXT,
        city TEXT,
        client_type TEXT NOT NULL DEFAULT 'Público general',
        payment_terms TEXT NOT NULL DEFAULT 'Contado',
        credit_days INTEGER NOT NULL DEFAULT 0,
        notes TEXT,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS client_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        price_per_kg REAL NOT NULL,
        UNIQUE(client_id, product_id),
        FOREIGN KEY(client_id) REFERENCES clients(id),
        FOREIGN KEY(product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        folio TEXT NOT NULL UNIQUE,
        sale_date TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        payment_status TEXT NOT NULL DEFAULT 'Pagada',
        payment_method TEXT,
        notes TEXT,
        FOREIGN KEY(client_id) REFERENCES clients(id)
    );
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity_packages REAL NOT NULL,
        weight_kg REAL NOT NULL,
        price_per_kg REAL NOT NULL,
        cost_per_kg REAL NOT NULL,
        subtotal REAL NOT NULL,
        cost_total REAL NOT NULL,
        profit REAL NOT NULL,
        FOREIGN KEY(sale_id) REFERENCES sales(id),
        FOREIGN KEY(product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movement_date TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        movement_type TEXT NOT NULL,
        quantity_kg REAL NOT NULL,
        reference TEXT,
        notes TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id)
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    # Migración para bases creadas con versiones anteriores.
    if not column_exists(conn, "products", "image_path"):
        conn.execute("ALTER TABLE products ADD COLUMN image_path TEXT")

    defaults = {
        "business_name": "Embutidos Rodríguez",
        "business_subtitle": "Calidad y tradición",
        "business_phone": "",
        "business_address": "La Piedad, Michoacán",
        "logo_path": "",
        "secondary_logo_path": "",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))

    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        products = [
            ("Chorizo extrafino", "Pasta 1 kg", 1.0, 80, 150, 130, 130, 0, 10),
            ("Chorizo extrafino", "Embutido 1 kg", 1.0, 85, 160, 140, 140, 0, 10),
            ("Chorizo argentino", "Embutido 1 kg", 1.0, 90, 160, 140, 140, 0, 8),
            ("Chistorra", "Embutida 1 kg", 1.0, 95, 180, 160, 160, 0, 8),
        ]
        conn.executemany("""
            INSERT INTO products
            (name,presentation,weight_kg,cost_per_kg,public_price_per_kg,
             wholesale_price_per_kg,restaurant_price_per_kg,stock_kg,min_stock_kg)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, products)
    if conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0:
        conn.execute("""
            INSERT INTO clients
            (business_name,contact_name,phone,city,client_type,payment_terms,credit_days,notes)
            VALUES ('Público general','','','La Piedad','Público general','Contado',0,'Cliente predeterminado')
        """)
    conn.commit()
    conn.close()

def query_df(sql, params=()):
    conn = connect()
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df

def execute(sql, params=()):
    conn = connect()
    cur = conn.execute(sql, params)
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid

def get_setting(key, default=""):
    conn = connect()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key, value):
    conn = connect()
    conn.execute("""
        INSERT INTO settings(key,value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()

def money(v):
    return f"${float(v):,.2f}"

def next_folio():
    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] + 1
    conn.close()
    return f"ER-{date.today().year}-{n:05d}"

def effective_price(client_id, product_row):
    conn = connect()
    special = conn.execute(
        "SELECT price_per_kg FROM client_prices WHERE client_id=? AND product_id=?",
        (int(client_id), int(product_row["id"]))
    ).fetchone()
    if special:
        conn.close()
        return float(special[0])
    ctype = conn.execute("SELECT client_type FROM clients WHERE id=?", (int(client_id),)).fetchone()[0]
    conn.close()
    if ctype == "Restaurante":
        return float(product_row["restaurant_price_per_kg"])
    if ctype in ("Mayorista", "Tienda", "Cadena comercial"):
        return float(product_row["wholesale_price_per_kg"])
    return float(product_row["public_price_per_kg"])

def save_uploaded_file(uploaded, folder, prefix):
    if not uploaded:
        return ""
    suffix = Path(uploaded.name).suffix.lower() or ".png"
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}{suffix}"
    path = folder / filename
    path.write_bytes(uploaded.getbuffer())
    return str(path.relative_to(APP_DIR))

def safe_asset(relative_path):
    if not relative_path:
        return None
    p = APP_DIR / relative_path
    return p if p.exists() else None

def sales_detail(start=None, end=None):
    sql = """
        SELECT s.id AS sale_id, s.sale_date AS Fecha, s.folio AS Folio,
               c.business_name AS Cliente, c.client_type AS TipoCliente,
               p.name AS Producto, p.presentation AS Presentación,
               si.quantity_packages AS Unidades, si.weight_kg AS Kilos,
               si.price_per_kg AS PrecioKg, si.subtotal AS Venta,
               si.cost_total AS Costo, si.profit AS Utilidad,
               s.payment_status AS Estado, s.payment_method AS FormaPago
        FROM sales s
        JOIN clients c ON c.id=s.client_id
        JOIN sale_items si ON si.sale_id=s.id
        JOIN products p ON p.id=si.product_id
    """
    params = ()
    if start and end:
        sql += " WHERE s.sale_date BETWEEN ? AND ?"
        params = (start, end)
    sql += " ORDER BY s.sale_date, s.id"
    return query_df(sql, params)

# ---------- Ticket ----------
def build_ticket_html(sale_id, paper_width="80 mm", show_prices=True):
    sale = query_df("""
        SELECT s.id, s.folio, s.sale_date, s.payment_status, s.payment_method, s.notes,
               c.business_name, c.contact_name, c.phone, c.city
        FROM sales s JOIN clients c ON c.id=s.client_id WHERE s.id=?
    """, (sale_id,))
    items = query_df("""
        SELECT p.name, p.presentation, si.quantity_packages, si.weight_kg,
               si.price_per_kg, si.subtotal
        FROM sale_items si JOIN products p ON p.id=si.product_id
        WHERE si.sale_id=? ORDER BY si.id
    """, (sale_id,))
    if sale.empty:
        return "<p>Venta no encontrada.</p>"
    s = sale.iloc[0]
    width_px = "302px" if paper_width == "80 mm" else "219px"
    total_kg = float(items["weight_kg"].sum())
    total = float(items["subtotal"].sum())
    business_name = escape(get_setting("business_name", "Embutidos Rodríguez"))
    subtitle = escape(get_setting("business_subtitle", ""))
    phone_b = escape(get_setting("business_phone", ""))
    address = escape(get_setting("business_address", ""))

    rows = []
    for _, item in items.iterrows():
        price = (
            f"<div class='line'><span>{item['weight_kg']:.3f} kg × ${item['price_per_kg']:,.2f}</span>"
            f"<strong>${item['subtotal']:,.2f}</strong></div>"
            if show_prices else f"<div>{item['weight_kg']:.3f} kg</div>"
        )
        rows.append(
            f"<div class='item'><strong>{escape(str(item['name']))}</strong><br>"
            f"<span>{escape(str(item['presentation']))} · {item['quantity_packages']:g} unidad(es)</span>{price}</div>"
        )
    totals = f"<div class='rule'></div><div class='line'><strong>TOTAL KG</strong><strong>{total_kg:.3f} kg</strong></div>"
    if show_prices:
        totals += f"<div class='line total'><strong>TOTAL</strong><strong>${total:,.2f}</strong></div>"

    return f"""<!DOCTYPE html>
<html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Ticket {escape(str(s['folio']))}</title>
<style>
@page {{ size:{paper_width} auto; margin:3mm; }}
*{{box-sizing:border-box}} body{{margin:0;background:#fff;color:#111;font-family:Arial,sans-serif;font-size:12px}}
.ticket{{width:{width_px};max-width:100%;margin:auto;padding:6px}} .center{{text-align:center}}
.brand{{font-size:18px;font-weight:800}} .small{{font-size:10px}} .rule{{border-top:1px dashed #111;margin:8px 0}}
.line{{display:flex;justify-content:space-between;gap:8px}} .item{{padding:5px 0;border-bottom:1px dotted #777}}
.item span{{font-size:11px}} .total{{font-size:16px;margin-top:5px}}
.signature{{margin-top:30px;border-top:1px solid #111;text-align:center;padding-top:4px}}
.no-print{{display:block;width:100%;margin:12px auto;padding:10px;border:0;border-radius:8px;font-weight:700}}
@media print{{.no-print{{display:none!important}}}}
</style></head><body><div class='ticket'>
<div class='center'><div class='brand'>{business_name}</div><div>{subtitle}</div>
<div class='small'>{address}</div><div class='small'>{phone_b}</div><div class='small'>Ticket de venta / entrega</div></div>
<div class='rule'></div>
<div><strong>Folio:</strong> {escape(str(s['folio']))}</div>
<div><strong>Fecha:</strong> {escape(str(s['sale_date']))}</div>
<div><strong>Cliente:</strong> {escape(str(s['business_name']))}</div>
<div><strong>Pago:</strong> {escape(str(s['payment_status']))} · {escape(str(s['payment_method'] or ''))}</div>
<div class='rule'></div>{''.join(rows)}{totals}
{f"<div class='rule'></div><div><strong>Observaciones:</strong> {escape(str(s['notes']))}</div>" if s['notes'] else ""}
<div class='signature'>Recibí de conformidad</div><div class='center small' style='margin-top:16px'>Gracias por su compra</div>
<button class='no-print' onclick='window.print()'>IMPRIMIR TICKET</button>
</div></body></html>"""


def get_openai_key():
    """Obtiene la clave exclusivamente desde secretos del servidor o variable de entorno."""
    try:
        return st.secrets.get("OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    except Exception:
        return os.getenv("OPENAI_API_KEY", "")

def business_snapshot():
    """Genera un resumen compacto y verificable de la información comercial."""
    today = date.today()
    current_month = today.replace(day=1).isoformat()
    detail = sales_detail(current_month, today.isoformat())
    products = query_df("""
        SELECT name Producto, presentation Presentacion, stock_kg StockKg,
               min_stock_kg MinimoKg, cost_per_kg CostoKg,
               public_price_per_kg PrecioPublicoKg,
               wholesale_price_per_kg PrecioMayoreoKg,
               restaurant_price_per_kg PrecioRestauranteKg
        FROM products WHERE active=1 ORDER BY name,presentation
    """)
    clients = query_df("""
        SELECT business_name Cliente, client_type Tipo, city Ciudad,
               payment_terms CondicionPago, credit_days DiasCredito
        FROM clients WHERE active=1 ORDER BY business_name
    """)
    pending = query_df("""
        SELECT s.folio Folio,s.sale_date Fecha,c.business_name Cliente,
               ROUND(SUM(si.subtotal),2) Saldo
        FROM sales s JOIN clients c ON c.id=s.client_id
        JOIN sale_items si ON si.sale_id=s.id
        WHERE s.payment_status='Pendiente'
        GROUP BY s.id ORDER BY s.sale_date
    """)
    last_12_start = (today - timedelta(days=365)).isoformat()
    history = sales_detail(last_12_start, today.isoformat())

    snapshot = {
        "fecha_consulta": today.isoformat(),
        "resumen_mes_actual": {
            "ventas": round(float(detail["Venta"].sum()), 2) if not detail.empty else 0,
            "kilos": round(float(detail["Kilos"].sum()), 3) if not detail.empty else 0,
            "utilidad_bruta": round(float(detail["Utilidad"].sum()), 2) if not detail.empty else 0,
        },
        "productos": products.to_dict(orient="records"),
        "clientes": clients.to_dict(orient="records"),
        "cuentas_por_cobrar": pending.to_dict(orient="records"),
        "ventas_ultimos_12_meses": history.to_dict(orient="records") if not history.empty else [],
    }
    return snapshot

def ask_business_ai(question):
    api_key = get_openai_key()
    if not api_key:
        raise RuntimeError(
            "No está configurada la clave OPENAI_API_KEY en los secretos del servidor."
        )
    client = OpenAI(api_key=api_key)
    snapshot = business_snapshot()
    instructions = """
Eres el asistente comercial interno de Embutidos Rodríguez.
Responde en español claro y práctico.
Usa únicamente los datos JSON proporcionados. No inventes ventas, kilos, costos,
clientes, fechas ni utilidades. Cuando no existan datos suficientes, dilo claramente.
Distingue siempre utilidad bruta estimada de utilidad neta.
Cuando recomiendes producir, considera inventario, mínimos y ventas históricas,
pero indica que se trata de una recomendación y no de una orden automática.
Da primero una respuesta directa y después, cuando sea útil, presenta cifras y acciones.
"""
    response = client.responses.create(
        model="gpt-5-mini",
        instructions=instructions,
        input=(
            "DATOS DEL NEGOCIO:\n"
            + json.dumps(snapshot, ensure_ascii=False, default=str)
            + "\n\nPREGUNTA DEL USUARIO:\n"
            + question
        ),
    )
    return response.output_text

init_db()

# ---------- Diseño ----------
st.markdown("""
<style>
:root { --brand:#7a161b; --brand2:#a52a2f; --soft:#f7f2ef; }
.block-container {padding-top:1rem; padding-bottom:3rem;}
section[data-testid="stSidebar"] {background:linear-gradient(180deg,#521014,#7a161b);}
section[data-testid="stSidebar"] * {color:white;}
section[data-testid="stSidebar"] .stRadio label {font-weight:600;}
div[data-testid="stMetric"] {background:white;border:1px solid #eadfdb;padding:14px;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.04)}
.stButton>button {border-radius:10px;font-weight:700}
[data-testid="stForm"] {background:#fff;border:1px solid #eadfdb;padding:18px;border-radius:14px}
.product-card {border:1px solid #eadfdb;border-radius:14px;padding:12px;background:#fff;height:100%}
.badge {display:inline-block;padding:3px 8px;border-radius:999px;background:#f4e4df;font-size:12px;font-weight:700}
.hero {padding:20px;border-radius:18px;background:linear-gradient(135deg,#7a161b,#a52a2f);color:white;margin-bottom:16px}
.hero h1 {color:white;margin:0}
@media(max-width:700px){.block-container{padding-left:.8rem;padding-right:.8rem}.hero{padding:15px}}
</style>
""", unsafe_allow_html=True)

business_name = get_setting("business_name", "Embutidos Rodríguez")
logo = safe_asset(get_setting("logo_path", ""))

with st.sidebar:
    if logo:
        st.image(str(logo), use_container_width=True)
    st.markdown(f"## {business_name}")
    st.caption("Control comercial")
    menu = st.radio(
        "Menú",
        ["Inicio", "Nueva venta", "Asistente IA", "Tickets", "Inventario", "Clientes", "Productos",
         "Cuentas por cobrar", "Reportes", "Configuración"],
        label_visibility="collapsed",
    )

# ---------- Inicio ----------
if menu == "Inicio":
    st.markdown(f"<div class='hero'><h1>{escape(business_name)}</h1><div>Ventas, inventario, clientes y utilidad en un solo lugar.</div></div>", unsafe_allow_html=True)
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    today_iso = today.isoformat()
    month_detail = sales_detail(month_start, today_iso)
    all_products = query_df("SELECT * FROM products WHERE active=1")
    pending = query_df("""
        SELECT COALESCE(SUM(si.subtotal),0) saldo
        FROM sales s JOIN sale_items si ON si.sale_id=s.id WHERE s.payment_status='Pendiente'
    """).iloc[0]["saldo"]

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Ventas del mes", money(month_detail["Venta"].sum() if not month_detail.empty else 0))
    c2.metric("Kilos vendidos", f"{month_detail['Kilos'].sum() if not month_detail.empty else 0:,.2f} kg")
    c3.metric("Utilidad bruta", money(month_detail["Utilidad"].sum() if not month_detail.empty else 0))
    c4.metric("Por cobrar", money(pending))
    c5.metric("Inventario", f"{all_products['stock_kg'].sum() if not all_products.empty else 0:,.2f} kg")

    left, right = st.columns([2,1])
    with left:
        st.subheader("Ventas diarias del mes")
        if month_detail.empty:
            st.info("Registra ventas para comenzar a ver las gráficas.")
        else:
            daily = month_detail.groupby("Fecha", as_index=False).agg(Ventas=("Venta","sum"), Kilos=("Kilos","sum"))
            daily["Fecha"] = pd.to_datetime(daily["Fecha"])
            st.line_chart(daily.set_index("Fecha")[["Ventas"]], height=280)
    with right:
        st.subheader("Productos más vendidos")
        if not month_detail.empty:
            top_products = month_detail.groupby("Producto", as_index=False)["Kilos"].sum().sort_values("Kilos", ascending=False).head(5)
            st.bar_chart(top_products.set_index("Producto"), height=280)
        else:
            st.info("Sin datos.")

    left, right = st.columns(2)
    with left:
        st.subheader("Mejores clientes del mes")
        if not month_detail.empty:
            top_clients = month_detail.groupby("Cliente", as_index=False).agg(Kilos=("Kilos","sum"), Ventas=("Venta","sum")).sort_values("Ventas", ascending=False).head(10)
            st.dataframe(top_clients, use_container_width=True, hide_index=True)
        else:
            st.info("Sin ventas.")
    with right:
        st.subheader("Alertas de inventario")
        low = query_df("""
            SELECT name AS Producto,presentation AS Presentación,ROUND(stock_kg,3) AS Existencia,
                   ROUND(min_stock_kg,3) AS Mínimo
            FROM products WHERE active=1 AND stock_kg<=min_stock_kg ORDER BY stock_kg
        """)
        if low.empty:
            st.success("Inventario dentro de niveles mínimos.")
        else:
            st.warning(f"{len(low)} producto(s) requieren atención.")
            st.dataframe(low, use_container_width=True, hide_index=True)

# ---------- Nueva venta ----------
elif menu == "Nueva venta":
    st.header("Nueva venta")
    clients = query_df("SELECT * FROM clients WHERE active=1 ORDER BY business_name")
    products = query_df("SELECT * FROM products WHERE active=1 ORDER BY name,presentation")
    if clients.empty or products.empty:
        st.error("Primero registra clientes y productos.")
        st.stop()

    if "cart" not in st.session_state:
        st.session_state.cart = []

    client_search = st.text_input("Buscar cliente", placeholder="Nombre, ciudad o tipo...")
    filtered_clients = clients.copy()
    if client_search:
        mask = filtered_clients.astype(str).apply(lambda col: col.str.contains(client_search, case=False, na=False)).any(axis=1)
        filtered_clients = filtered_clients[mask]
    if filtered_clients.empty:
        st.warning("No se encontraron clientes.")
        st.stop()

    client_map = {f"{r.business_name} — {r.client_type}": int(r.id) for _,r in filtered_clients.iterrows()}
    c1,c2,c3 = st.columns(3)
    client_label = c1.selectbox("Cliente", list(client_map))
    sale_date = c2.date_input("Fecha", date.today())
    folio = c3.text_input("Folio", next_folio())
    client_id = client_map[client_label]

    st.subheader("Agregar productos")
    product_search = st.text_input("Buscar producto", placeholder="Nombre o presentación...")
    filtered_products = products.copy()
    if product_search:
        mask = filtered_products.astype(str).apply(lambda col: col.str.contains(product_search, case=False, na=False)).any(axis=1)
        filtered_products = filtered_products[mask]
    product_map = {
        f"{r['name']} — {r['presentation']} | Stock {r['stock_kg']:.3f} kg": int(r["id"])
        for _,r in filtered_products.iterrows()
    }
    if product_map:
        p_label = st.selectbox("Producto", list(product_map))
        pid = product_map[p_label]
        p = products.loc[products["id"]==pid].iloc[0]
        suggested = effective_price(client_id, p)
        a,b,c,d = st.columns(4)
        units = a.number_input("Unidades/paquetes", min_value=0.01, value=1.0, step=1.0)
        kg = b.number_input("Kilos reales", min_value=0.001, value=float(p["weight_kg"]), step=0.050, format="%.3f")
        price = c.number_input("Precio por kg", min_value=0.0, value=float(suggested), step=1.0)
        c.metric("Subtotal", money(kg*price))
        if d.button("Agregar al carrito", type="primary", use_container_width=True):
            current_cart_kg = sum(i["kg"] for i in st.session_state.cart if i["product_id"]==pid)
            if current_cart_kg + kg > float(p["stock_kg"]):
                st.error("La cantidad supera el inventario disponible.")
            else:
                st.session_state.cart.append({
                    "product_id": pid, "producto": p["name"], "presentacion": p["presentation"],
                    "unidades": units, "kg": kg, "precio": price,
                    "costo": float(p["cost_per_kg"]), "subtotal": kg*price,
                    "costo_total": kg*float(p["cost_per_kg"]), "utilidad": kg*(price-float(p["cost_per_kg"]))
                })
                st.rerun()
    else:
        st.info("No se encontraron productos.")

    st.subheader("Carrito")
    if not st.session_state.cart:
        st.info("Agrega al menos un producto.")
    else:
        cart_df = pd.DataFrame(st.session_state.cart)
        show = cart_df[["producto","presentacion","unidades","kg","precio","subtotal","utilidad"]].copy()
        show.columns = ["Producto","Presentación","Unidades","Kg","Precio/kg","Subtotal","Utilidad"]
        st.dataframe(show, use_container_width=True, hide_index=True)
        c1,c2,c3 = st.columns(3)
        c1.metric("Kilos", f"{cart_df['kg'].sum():,.3f}")
        c2.metric("Total", money(cart_df["subtotal"].sum()))
        c3.metric("Utilidad", money(cart_df["utilidad"].sum()))
        remove_index = st.selectbox("Quitar renglón", list(range(1,len(st.session_state.cart)+1)))
        if st.button("Quitar seleccionado"):
            st.session_state.cart.pop(remove_index-1)
            st.rerun()

    c1,c2 = st.columns(2)
    payment_status = c1.selectbox("Estado de pago", ["Pagada","Pendiente"])
    payment_method = c2.selectbox("Forma de pago", ["Efectivo","Transferencia","Crédito","Otro"])
    notes = st.text_area("Observaciones")

    c1,c2 = st.columns(2)
    if c1.button("Guardar venta", type="primary", use_container_width=True, disabled=not st.session_state.cart):
        conn = connect()
        try:
            sale_id = conn.execute("""
                INSERT INTO sales(folio,sale_date,client_id,payment_status,payment_method,notes)
                VALUES (?,?,?,?,?,?)
            """, (folio.strip(),sale_date.isoformat(),client_id,payment_status,payment_method,notes)).lastrowid
            for item in st.session_state.cart:
                stock = conn.execute("SELECT stock_kg FROM products WHERE id=?", (item["product_id"],)).fetchone()[0]
                if item["kg"] > stock:
                    raise ValueError(f"Inventario insuficiente para {item['producto']}.")
                conn.execute("""
                    INSERT INTO sale_items(sale_id,product_id,quantity_packages,weight_kg,price_per_kg,cost_per_kg,
                    subtotal,cost_total,profit) VALUES (?,?,?,?,?,?,?,?,?)
                """, (sale_id,item["product_id"],item["unidades"],item["kg"],item["precio"],item["costo"],
                      item["subtotal"],item["costo_total"],item["utilidad"]))
                conn.execute("UPDATE products SET stock_kg=stock_kg-? WHERE id=?", (item["kg"],item["product_id"]))
                conn.execute("""
                    INSERT INTO inventory_movements(movement_date,product_id,movement_type,quantity_kg,reference,notes)
                    VALUES (?,?,?,?,?,?)
                """, (sale_date.isoformat(),item["product_id"],"Venta",-item["kg"],folio,notes))
            conn.commit()
            st.session_state.cart = []
            st.success(f"Venta {folio} guardada.")
            st.session_state["last_sale_id"] = sale_id
            st.rerun()
        except Exception as e:
            conn.rollback()
            st.error(str(e))
        finally:
            conn.close()
    if c2.button("Vaciar carrito", use_container_width=True, disabled=not st.session_state.cart):
        st.session_state.cart = []
        st.rerun()

# ---------- Asistente IA ----------
elif menu == "Asistente IA":
    st.header("🤖 Asistente de Embutidos Rodríguez")
    st.caption(
        "Consulta ventas, kilos, utilidad bruta, inventario, clientes y cobranza "
        "usando la información registrada en el sistema."
    )

    if not get_openai_key():
        st.warning(
            "La IA todavía no está activada. Al publicar la aplicación, agrega "
            "OPENAI_API_KEY en los secretos de Streamlit. La clave no debe guardarse "
            "en el código ni escribirse directamente en el celular."
        )

    examples = [
        "¿Cuánto vendí este mes y cuál fue mi utilidad bruta?",
        "¿Cuáles son mis cinco mejores clientes por ventas?",
        "¿Qué productos están bajos de inventario?",
        "¿Quién me debe y cuánto?",
        "¿Qué producto fue el más rentable durante los últimos 12 meses?",
        "Con base en ventas e inventario, ¿qué debería revisar para producir?",
    ]
    selected_example = st.selectbox(
        "Pregunta sugerida",
        ["Escribir mi propia pregunta"] + examples
    )
    default_question = "" if selected_example == "Escribir mi propia pregunta" else selected_example
    question = st.text_area(
        "Pregunta",
        value=default_question,
        placeholder="Ejemplo: ¿Qué cliente dejó de comprar este mes?",
        height=110,
    )

    if "ai_history" not in st.session_state:
        st.session_state.ai_history = []

    if st.button("Consultar a la IA", type="primary", use_container_width=True):
        if not question.strip():
            st.error("Escribe una pregunta.")
        else:
            try:
                with st.spinner("Analizando la información del negocio..."):
                    answer = ask_business_ai(question.strip())
                st.session_state.ai_history.insert(
                    0, {"question": question.strip(), "answer": answer}
                )
            except Exception as exc:
                st.error(f"No fue posible consultar la IA: {exc}")

    for item in st.session_state.ai_history[:10]:
        with st.container(border=True):
            st.markdown(f"**Tú:** {item['question']}")
            st.markdown(item["answer"])

# ---------- Tickets ----------
elif menu == "Tickets":
    st.header("Tickets y remisiones")
    sales = query_df("""
        SELECT s.id,s.folio,s.sale_date,c.business_name,
               ROUND(SUM(si.weight_kg),3) kilos,ROUND(SUM(si.subtotal),2) total,s.payment_status
        FROM sales s JOIN clients c ON c.id=s.client_id JOIN sale_items si ON si.sale_id=s.id
        GROUP BY s.id ORDER BY s.sale_date DESC,s.id DESC
    """)
    if sales.empty:
        st.info("No hay ventas.")
    else:
        search = st.text_input("Buscar ticket", placeholder="Folio o cliente...")
        filtered = sales
        if search:
            mask = filtered.astype(str).apply(lambda col: col.str.contains(search,case=False,na=False)).any(axis=1)
            filtered = filtered[mask]
        options = {f"{r.folio} — {r.business_name} — {r.kilos:.3f} kg — {money(r.total)}":int(r.id) for _,r in filtered.iterrows()}
        selected = st.selectbox("Venta", list(options))
        c1,c2 = st.columns(2)
        width = c1.selectbox("Papel", ["80 mm","58 mm"])
        kind = c2.selectbox("Documento", ["Ticket con precios","Remisión sin precios"])
        html_ticket = build_ticket_html(options[selected], width, kind=="Ticket con precios")
        components.html(html_ticket, height=720, scrolling=True)
        folio_selected = filtered.loc[filtered["id"]==options[selected],"folio"].iloc[0]
        st.download_button("Descargar ticket HTML", html_ticket.encode("utf-8"),
                           file_name=f"ticket_{folio_selected}.html", mime="text/html",
                           use_container_width=True)

# ---------- Inventario ----------
elif menu == "Inventario":
    st.header("Inventario")
    products = query_df("SELECT * FROM products WHERE active=1 ORDER BY name,presentation")
    search = st.text_input("Buscar en inventario")
    if search:
        mask = products.astype(str).apply(lambda col: col.str.contains(search,case=False,na=False)).any(axis=1)
        products = products[mask]
    tab1,tab2,tab3 = st.tabs(["Existencias","Movimiento","Historial"])
    with tab1:
        view = products[["name","presentation","stock_kg","min_stock_kg","cost_per_kg"]].copy()
        view.columns = ["Producto","Presentación","Existencia kg","Mínimo kg","Costo/kg"]
        st.dataframe(view,use_container_width=True,hide_index=True)
    with tab2:
        allp = query_df("SELECT * FROM products WHERE active=1 ORDER BY name,presentation")
        pm = {f"{r['name']} — {r['presentation']}":int(r["id"]) for _,r in allp.iterrows()}
        label = st.selectbox("Producto",list(pm))
        mtype = st.selectbox("Tipo",["Entrada de producción","Devolución de cliente","Ajuste positivo","Merma","Ajuste negativo"])
        qty = st.number_input("Cantidad kg",min_value=0.001,step=0.100,format="%.3f")
        ref = st.text_input("Referencia o lote")
        notes = st.text_area("Notas")
        if st.button("Guardar movimiento",type="primary"):
            sign = -1 if mtype in ("Merma","Ajuste negativo") else 1
            conn=connect()
            current=conn.execute("SELECT stock_kg FROM products WHERE id=?",(pm[label],)).fetchone()[0]
            if current+sign*qty<0:
                st.error("El inventario quedaría negativo.")
            else:
                conn.execute("UPDATE products SET stock_kg=stock_kg+? WHERE id=?",(sign*qty,pm[label]))
                conn.execute("""INSERT INTO inventory_movements(movement_date,product_id,movement_type,quantity_kg,reference,notes)
                                VALUES (?,?,?,?,?,?)""",
                             (date.today().isoformat(),pm[label],mtype,sign*qty,ref,notes))
                conn.commit()
                st.success("Movimiento registrado.")
                st.rerun()
            conn.close()
    with tab3:
        hist = query_df("""
            SELECT im.movement_date Fecha,p.name Producto,p.presentation Presentación,
                   im.movement_type Tipo,im.quantity_kg Kg,im.reference Referencia,im.notes Notas
            FROM inventory_movements im JOIN products p ON p.id=im.product_id
            ORDER BY im.id DESC LIMIT 300
        """)
        st.dataframe(hist,use_container_width=True,hide_index=True)

# ---------- Clientes ----------
elif menu == "Clientes":
    st.header("Clientes")
    tab1,tab2,tab3 = st.tabs(["Directorio","Nuevo cliente","Precios especiales"])
    with tab1:
        q=st.text_input("Buscar cliente")
        df=query_df("""SELECT business_name Cliente,contact_name Contacto,phone Teléfono,city Ciudad,
                       client_type Tipo,payment_terms Pago,credit_days 'Días crédito',notes Observaciones
                       FROM clients WHERE active=1 ORDER BY business_name""")
        if q:
            mask=df.astype(str).apply(lambda col: col.str.contains(q,case=False,na=False)).any(axis=1)
            df=df[mask]
        st.dataframe(df,use_container_width=True,hide_index=True)
    with tab2:
        with st.form("client_form",clear_on_submit=True):
            name=st.text_input("Nombre comercial")
            contact=st.text_input("Encargado")
            phone=st.text_input("Teléfono")
            city=st.text_input("Ciudad")
            ctype=st.selectbox("Tipo",["Público general","Tienda","Restaurante","Mayorista","Cadena comercial"])
            terms=st.selectbox("Pago",["Contado","Crédito"])
            days=st.number_input("Días de crédito",min_value=0,step=1)
            notes=st.text_area("Observaciones")
            if st.form_submit_button("Guardar",type="primary"):
                if name.strip():
                    execute("""INSERT INTO clients(business_name,contact_name,phone,city,client_type,payment_terms,credit_days,notes)
                               VALUES (?,?,?,?,?,?,?,?)""",(name.strip(),contact,phone,city,ctype,terms,int(days),notes))
                    st.success("Cliente guardado.")
                else: st.error("El nombre es obligatorio.")
    with tab3:
        clients=query_df("SELECT id,business_name FROM clients WHERE active=1 ORDER BY business_name")
        products=query_df("SELECT id,name,presentation FROM products WHERE active=1 ORDER BY name")
        cm={r.business_name:int(r.id) for _,r in clients.iterrows()}
        pm={f"{r['name']} — {r['presentation']}":int(r["id"]) for _,r in products.iterrows()}
        cl=st.selectbox("Cliente",list(cm))
        pr=st.selectbox("Producto",list(pm))
        val=st.number_input("Precio especial por kg",min_value=0.0,step=1.0)
        if st.button("Guardar precio especial",type="primary"):
            conn=connect()
            conn.execute("""INSERT INTO client_prices(client_id,product_id,price_per_kg) VALUES (?,?,?)
                            ON CONFLICT(client_id,product_id) DO UPDATE SET price_per_kg=excluded.price_per_kg""",
                         (cm[cl],pm[pr],val))
            conn.commit(); conn.close(); st.success("Precio guardado.")

# ---------- Productos ----------
elif menu == "Productos":
    st.header("Catálogo de productos")
    tab1,tab2,tab3 = st.tabs(["Catálogo visual","Nuevo producto","Editar producto"])
    with tab1:
        q=st.text_input("Buscar producto")
        products=query_df("SELECT * FROM products WHERE active=1 ORDER BY name,presentation")
        if q:
            mask=products.astype(str).apply(lambda col: col.str.contains(q,case=False,na=False)).any(axis=1)
            products=products[mask]
        cols=st.columns(3)
        for idx,(_,p) in enumerate(products.iterrows()):
            with cols[idx%3]:
                st.markdown("<div class='product-card'>",unsafe_allow_html=True)
                img=safe_asset(p["image_path"])
                if img: st.image(str(img),use_container_width=True)
                else: st.markdown("### 🐷")
                st.markdown(f"### {p['name']}")
                st.markdown(f"<span class='badge'>{p['presentation']}</span>",unsafe_allow_html=True)
                st.write(f"**Stock:** {p['stock_kg']:.3f} kg")
                st.write(f"**Público:** {money(p['public_price_per_kg'])}/kg")
                st.write(f"**Mayoreo:** {money(p['wholesale_price_per_kg'])}/kg")
                st.markdown("</div>",unsafe_allow_html=True)
    with tab2:
        with st.form("product_form",clear_on_submit=True):
            name=st.text_input("Producto")
            pres=st.text_input("Presentación")
            image=st.file_uploader("Fotografía",type=["png","jpg","jpeg","webp"])
            weight=st.number_input("Peso estándar kg",min_value=0.001,value=1.0,step=0.050,format="%.3f")
            cost=st.number_input("Costo/kg",min_value=0.0,step=1.0)
            public=st.number_input("Precio público/kg",min_value=0.0,step=1.0)
            wholesale=st.number_input("Precio mayoreo/kg",min_value=0.0,step=1.0)
            restaurant=st.number_input("Precio restaurante/kg",min_value=0.0,step=1.0)
            minimum=st.number_input("Mínimo de inventario kg",min_value=0.0,step=1.0)
            if st.form_submit_button("Guardar producto",type="primary"):
                if name.strip() and pres.strip():
                    path=save_uploaded_file(image,PRODUCT_IMAGES,"product")
                    execute("""INSERT INTO products(name,presentation,weight_kg,cost_per_kg,public_price_per_kg,
                               wholesale_price_per_kg,restaurant_price_per_kg,min_stock_kg,image_path)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (name.strip(),pres.strip(),weight,cost,public,wholesale,restaurant,minimum,path))
                    st.success("Producto guardado.")
                else: st.error("Nombre y presentación son obligatorios.")
    with tab3:
        products=query_df("SELECT * FROM products WHERE active=1 ORDER BY name,presentation")
        pm={f"{r['name']} — {r['presentation']}":int(r["id"]) for _,r in products.iterrows()}
        label=st.selectbox("Producto a editar",list(pm))
        p=products.loc[products["id"]==pm[label]].iloc[0]
        with st.form("edit_product"):
            name=st.text_input("Producto",p["name"])
            pres=st.text_input("Presentación",p["presentation"])
            image=st.file_uploader("Cambiar fotografía",type=["png","jpg","jpeg","webp"],key="edit_img")
            weight=st.number_input("Peso estándar kg",min_value=0.001,value=float(p["weight_kg"]),step=0.050,format="%.3f")
            cost=st.number_input("Costo/kg",min_value=0.0,value=float(p["cost_per_kg"]),step=1.0)
            public=st.number_input("Precio público/kg",min_value=0.0,value=float(p["public_price_per_kg"]),step=1.0)
            wholesale=st.number_input("Precio mayoreo/kg",min_value=0.0,value=float(p["wholesale_price_per_kg"]),step=1.0)
            restaurant=st.number_input("Precio restaurante/kg",min_value=0.0,value=float(p["restaurant_price_per_kg"]),step=1.0)
            minimum=st.number_input("Mínimo kg",min_value=0.0,value=float(p["min_stock_kg"]),step=1.0)
            if st.form_submit_button("Actualizar",type="primary"):
                path=p["image_path"]
                if image: path=save_uploaded_file(image,PRODUCT_IMAGES,"product")
                execute("""UPDATE products SET name=?,presentation=?,weight_kg=?,cost_per_kg=?,public_price_per_kg=?,
                           wholesale_price_per_kg=?,restaurant_price_per_kg=?,min_stock_kg=?,image_path=? WHERE id=?""",
                        (name,pres,weight,cost,public,wholesale,restaurant,minimum,path,pm[label]))
                st.success("Producto actualizado.")

# ---------- Cobranza ----------
elif menu == "Cuentas por cobrar":
    st.header("Cuentas por cobrar")
    pending=query_df("""
        SELECT s.id,s.folio Folio,s.sale_date Fecha,c.business_name Cliente,
               ROUND(SUM(si.subtotal),2) Total
        FROM sales s JOIN clients c ON c.id=s.client_id JOIN sale_items si ON si.sale_id=s.id
        WHERE s.payment_status='Pendiente' GROUP BY s.id ORDER BY s.sale_date
    """)
    if pending.empty:
        st.success("No hay saldos pendientes.")
    else:
        c1,c2=st.columns(2)
        c1.metric("Total por cobrar",money(pending["Total"].sum()))
        c2.metric("Ventas pendientes",len(pending))
        st.dataframe(pending.drop(columns=["id"]),use_container_width=True,hide_index=True)
        opts={f"{r.Folio} — {r.Cliente} — {money(r.Total)}":int(r.id) for _,r in pending.iterrows()}
        selected=st.selectbox("Registrar pago",list(opts))
        if st.button("Marcar como pagada",type="primary"):
            execute("UPDATE sales SET payment_status='Pagada' WHERE id=?",(opts[selected],))
            st.success("Pago registrado."); st.rerun()

# ---------- Reportes ----------
elif menu == "Reportes":
    st.header("Reportes")
    c1,c2,c3=st.columns(3)
    period=c1.selectbox("Periodo rápido",["Este mes","Mes anterior","Este año","Personalizado"])
    today=date.today()
    if period=="Este mes":
        start=today.replace(day=1); end=today
    elif period=="Mes anterior":
        first=today.replace(day=1); end=first-timedelta(days=1); start=end.replace(day=1)
    elif period=="Este año":
        start=date(today.year,1,1); end=today
    else:
        start=c2.date_input("Desde",today.replace(day=1))
        end=c3.date_input("Hasta",today)
    if period!="Personalizado":
        c2.write(f"**Desde:** {start}")
        c3.write(f"**Hasta:** {end}")
    detail=sales_detail(start.isoformat(),end.isoformat())
    if detail.empty:
        st.info("Sin ventas en el periodo.")
    else:
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Ventas",money(detail["Venta"].sum()))
        c2.metric("Kilos",f"{detail['Kilos'].sum():,.3f}")
        c3.metric("Utilidad",money(detail["Utilidad"].sum()))
        margin=(detail["Utilidad"].sum()/detail["Venta"].sum()*100) if detail["Venta"].sum() else 0
        c4.metric("Margen bruto",f"{margin:.1f}%")
        tabs=st.tabs(["Por cliente","Por producto","Por mes","Detalle"])
        with tabs[0]:
            df=detail.groupby("Cliente",as_index=False).agg(Kilos=("Kilos","sum"),Ventas=("Venta","sum"),Utilidad=("Utilidad","sum")).sort_values("Ventas",ascending=False)
            st.dataframe(df,use_container_width=True,hide_index=True)
            st.bar_chart(df.set_index("Cliente")[["Ventas"]])
        with tabs[1]:
            df=detail.groupby(["Producto","Presentación"],as_index=False).agg(Kilos=("Kilos","sum"),Ventas=("Venta","sum"),Utilidad=("Utilidad","sum")).sort_values("Kilos",ascending=False)
            st.dataframe(df,use_container_width=True,hide_index=True)
        with tabs[2]:
            temp=detail.copy(); temp["Mes"]=pd.to_datetime(temp["Fecha"]).dt.to_period("M").astype(str)
            df=temp.groupby("Mes",as_index=False).agg(Ventas=("Venta","sum"),Kilos=("Kilos","sum"),Utilidad=("Utilidad","sum"))
            st.line_chart(df.set_index("Mes")[["Ventas","Utilidad"]])
            st.dataframe(df,use_container_width=True,hide_index=True)
        with tabs[3]:
            st.dataframe(detail,use_container_width=True,hide_index=True)
            st.download_button("Descargar CSV",detail.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"reporte_{start}_{end}.csv",mime="text/csv")

# ---------- Configuración ----------
elif menu == "Configuración":
    st.header("Configuración e identidad")
    st.info("Aquí puedes cargar los logotipos y datos que aparecerán en la aplicación y en los tickets.")
    with st.form("settings"):
        name=st.text_input("Nombre comercial",get_setting("business_name"))
        subtitle=st.text_input("Eslogan o subtítulo",get_setting("business_subtitle"))
        phone=st.text_input("Teléfono",get_setting("business_phone"))
        address=st.text_input("Dirección o ciudad",get_setting("business_address"))
        logo_up=st.file_uploader("Logotipo principal",type=["png","jpg","jpeg","webp"])
        secondary_up=st.file_uploader("Logotipo secundario",type=["png","jpg","jpeg","webp"])
        if st.form_submit_button("Guardar configuración",type="primary"):
            set_setting("business_name",name)
            set_setting("business_subtitle",subtitle)
            set_setting("business_phone",phone)
            set_setting("business_address",address)
            if logo_up: set_setting("logo_path",save_uploaded_file(logo_up,BRANDING_DIR,"logo"))
            if secondary_up: set_setting("secondary_logo_path",save_uploaded_file(secondary_up,BRANDING_DIR,"logo2"))
            st.success("Configuración guardada. Recarga la página para ver todos los cambios.")
    st.subheader("Estado de la inteligencia artificial")
    if get_openai_key():
        st.success("La clave del servidor está configurada. El Asistente IA está disponible.")
    else:
        st.warning("Falta configurar OPENAI_API_KEY en los secretos del servidor.")
    st.caption("Por seguridad, la clave nunca se muestra ni se guarda en la base de datos.")

    st.subheader("Respaldo")
    st.write("La información se guarda en `ventas.db`. Descarga una copia periódicamente.")
    if DB_PATH.exists():
        st.download_button("Descargar respaldo de la base de datos",DB_PATH.read_bytes(),
                           file_name=f"respaldo_ventas_{date.today()}.db",
                           mime="application/octet-stream",use_container_width=True)
