 from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from supabase import Client, create_client

st.set_page_config(
    page_title="Embutidos Rodríguez",
    page_icon="🐷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------
# Configuración y sesión de Supabase
# ---------------------------------------------------------------------
def get_secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value).strip() if value else ""


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_KEY = (
    get_secret("SUPABASE_PUBLISHABLE_KEY")
    or get_secret("SUPABASE_ANON_KEY")
)


def new_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Faltan SUPABASE_URL y SUPABASE_PUBLISHABLE_KEY en Secrets."
        )
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    access_token = st.session_state.get("sb_access_token")
    refresh_token = st.session_state.get("sb_refresh_token")
    if access_token and refresh_token:
        try:
            client.auth.set_session(access_token, refresh_token)
        except Exception:
            clear_auth()
    return client


def clear_auth() -> None:
    for key in (
        "sb_access_token",
        "sb_refresh_token",
        "sb_user_email",
        "cart",
    ):
        st.session_state.pop(key, None)


def save_auth(response: Any) -> None:
    session = getattr(response, "session", None)
    user = getattr(response, "user", None)
    if session:
        st.session_state["sb_access_token"] = session.access_token
        st.session_state["sb_refresh_token"] = session.refresh_token
    if user:
        st.session_state["sb_user_email"] = user.email


def is_logged_in() -> bool:
    return bool(
        st.session_state.get("sb_access_token")
        and st.session_state.get("sb_refresh_token")
    )


def require_configuration() -> None:
    if SUPABASE_URL and SUPABASE_KEY:
        return
    st.error("La aplicación todavía no está conectada a Supabase.")
    st.markdown(
        """
En **Streamlit Community Cloud → Manage app → Settings → Secrets**
pega:

```toml
SUPABASE_URL = "https://TU-PROYECTO.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_TU_CLAVE"
```

Después guarda los cambios y espera el nuevo despliegue.
"""
    )
    st.stop()


def money(value: float | int | None) -> str:
    return f"${float(value or 0):,.2f}"


def records(response: Any) -> list[dict[str, Any]]:
    return list(getattr(response, "data", None) or [])


def current_client() -> Client:
    return new_supabase_client()


# ---------------------------------------------------------------------
# Configuración comercial persistente
# ---------------------------------------------------------------------
def get_business_settings() -> dict[str, Any]:
    defaults = {
        "business_name": "Embutidos Rodríguez",
        "slogan": "Tradición que se disfruta en cada bocado",
        "phone": "",
        "address": "La Piedad, Michoacán",
        "logo_url": "",
        "secondary_logo_url": "",
    }
    try:
        rows = records(
            current_client().table("business_settings").select("*").limit(1).execute()
        )
        if rows:
            defaults.update({k: v for k, v in rows[0].items() if v is not None})
    except Exception:
        # La tabla se crea con el archivo de actualización SQL v5.1.
        pass
    return defaults


def save_business_settings(payload: dict[str, Any]) -> None:
    sb = current_client()
    rows = records(sb.table("business_settings").select("owner_id").limit(1).execute())
    if rows:
        sb.table("business_settings").update(payload).eq(
            "owner_id", rows[0]["owner_id"]
        ).execute()
    else:
        sb.table("business_settings").insert(payload).execute()


def upload_branding_file(uploaded_file: Any, label: str) -> str:
    sb = current_client()
    user_response = sb.auth.get_user()
    user = getattr(user_response, "user", None)
    if not user:
        raise RuntimeError("No fue posible identificar al usuario.")
    suffix = Path(uploaded_file.name).suffix.lower() or ".png"
    object_path = f"{user.id}/{label}{suffix}"
    data = uploaded_file.getvalue()
    try:
        sb.storage.from_("branding").remove([object_path])
    except Exception:
        pass
    sb.storage.from_("branding").upload(
        object_path,
        data,
        {"content-type": uploaded_file.type or "image/png", "upsert": "true"},
    )
    result = sb.storage.from_("branding").get_public_url(object_path)
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("publicUrl") or result.get("public_url") or "")
    return str(result)

# ---------------------------------------------------------------------
# Operaciones de datos
# ---------------------------------------------------------------------
def get_products(active_only: bool = True) -> list[dict[str, Any]]:
    query = current_client().table("products").select("*")
    if active_only:
        query = query.eq("active", True)
    return records(query.order("name").order("presentation").execute())


def get_clients(active_only: bool = True) -> list[dict[str, Any]]:
    query = current_client().table("clients").select("*")
    if active_only:
        query = query.eq("active", True)
    return records(query.order("business_name").execute())


def get_client_price(client_id: str, product: dict[str, Any]) -> float:
    special = records(
        current_client()
        .table("client_prices")
        .select("price_per_kg")
        .eq("client_id", client_id)
        .eq("product_id", product["id"])
        .limit(1)
        .execute()
    )
    if special:
        return float(special[0]["price_per_kg"])

    client_rows = records(
        current_client()
        .table("clients")
        .select("client_type")
        .eq("id", client_id)
        .limit(1)
        .execute()
    )
    client_type = client_rows[0]["client_type"] if client_rows else "Público general"
    if client_type == "Restaurante":
        return float(product.get("restaurant_price_per_kg") or 0)
    if client_type in ("Mayorista", "Tienda", "Cadena comercial"):
        return float(product.get("wholesale_price_per_kg") or 0)
    return float(product.get("public_price_per_kg") or 0)


def next_folio() -> str:
    year = date.today().year
    rows = records(
        current_client()
        .table("sales")
        .select("folio")
        .like("folio", f"ER-{year}-%")
        .execute()
    )
    maximum = 0
    for row in rows:
        try:
            maximum = max(maximum, int(str(row["folio"]).split("-")[-1]))
        except Exception:
            pass
    return f"ER-{year}-{maximum + 1:05d}"


def sales_summary(start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    query = current_client().table("sales_summary").select("*")
    if start:
        query = query.gte("sale_date", start.isoformat())
    if end:
        query = query.lte("sale_date", end.isoformat())
    return records(query.order("sale_date", desc=True).execute())


def sale_detail(sale_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    sales = records(
        current_client()
        .table("sales")
        .select("*, clients(business_name,contact_name,phone,city,address)")
        .eq("id", sale_id)
        .limit(1)
        .execute()
    )
    items = records(
        current_client()
        .table("sale_items")
        .select("*, products(name,presentation)")
        .eq("sale_id", sale_id)
        .order("created_at")
        .execute()
    )
    return (sales[0] if sales else None, items)


def build_ticket_html(sale_id: str, paper_width: str, show_prices: bool) -> str:
    sale, items = sale_detail(sale_id)
    if not sale:
        return "<p>Venta no encontrada.</p>"

    client = sale.get("clients") or {}
    total_kg = sum(float(item.get("weight_kg") or 0) for item in items)
    total = sum(float(item.get("subtotal") or 0) for item in items)
    width_px = "302px" if paper_width == "80 mm" else "219px"

    rows: list[str] = []
    for item in items:
        product = item.get("products") or {}
        price_line = (
            f"<div class='line'><span>{float(item['weight_kg']):.3f} kg × "
            f"{money(item['price_per_kg'])}</span><strong>{money(item['subtotal'])}</strong></div>"
            if show_prices
            else f"<div>{float(item['weight_kg']):.3f} kg</div>"
        )
        rows.append(
            "<div class='item'>"
            f"<strong>{escape(str(product.get('name', 'Producto')))}</strong><br>"
            f"<span>{escape(str(product.get('presentation', '')))} · "
            f"{float(item.get('quantity_units') or 0):g} unidad(es)</span>"
            f"{price_line}</div>"
        )

    total_html = (
        f"<div class='line'><strong>TOTAL KG</strong><strong>{total_kg:.3f} kg</strong></div>"
    )
    if show_prices:
        total_html += (
            f"<div class='line total'><strong>TOTAL</strong><strong>{money(total)}</strong></div>"
        )

    settings = get_business_settings()
    business_name = escape(str(settings.get("business_name") or "Embutidos Rodríguez"))
    slogan = escape(str(settings.get("slogan") or ""))

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
@page {{ size:{paper_width} auto; margin:3mm; }}
*{{box-sizing:border-box}} body{{margin:0;background:#fff;color:#111;font-family:Arial,sans-serif;font-size:12px}}
.ticket{{width:{width_px};max-width:100%;margin:auto;padding:6px}} .center{{text-align:center}}
.brand{{font-size:18px;font-weight:800}} .rule{{border-top:1px dashed #111;margin:8px 0}}
.line{{display:flex;justify-content:space-between;gap:8px}} .item{{padding:5px 0;border-bottom:1px dotted #777}}
.item span{{font-size:11px}} .total{{font-size:16px;margin-top:5px}}
.signature{{margin-top:30px;border-top:1px solid #111;text-align:center;padding-top:4px}}
.small{{font-size:10px}} .no-print{{display:block;width:100%;margin:12px auto;padding:10px;border:0;border-radius:8px;font-weight:700}}
@media print{{.no-print{{display:none!important}}}}
</style></head><body><div class="ticket">
<div class="center"><div class="brand">{business_name}</div>
<div>{slogan}</div>
<div class="small">Ticket de venta / entrega</div></div>
<div class="rule"></div>
<div><strong>Folio:</strong> {escape(str(sale['folio']))}</div>
<div><strong>Fecha:</strong> {escape(str(sale['sale_date']))}</div>
<div><strong>Cliente:</strong> {escape(str(client.get('business_name','')))}</div>
<div><strong>Pago:</strong> {escape(str(sale.get('payment_status','')))} · {escape(str(sale.get('payment_method') or ''))}</div>
<div class="rule"></div>
{''.join(rows)}
<div class="rule"></div>{total_html}
{f"<div class='rule'></div><div><strong>Observaciones:</strong> {escape(str(sale.get('notes')))}</div>" if sale.get('notes') else ""}
<div class="signature">Recibí de conformidad</div>
<div class="center small" style="margin-top:16px">Gracias por su compra</div>
<button class="no-print" onclick="window.print()">IMPRIMIR TICKET</button>
</div></body></html>"""


# ---------------------------------------------------------------------
# Apariencia
# ---------------------------------------------------------------------
st.markdown(
    """
<style>
:root { --brand:#7a161b; --brand2:#a52a2f; }
.block-container {padding-top:1rem; padding-bottom:3rem;}
section[data-testid="stSidebar"] {background:linear-gradient(180deg,#521014,#7a161b);}
section[data-testid="stSidebar"] * {color:white;}
div[data-testid="stMetric"] {background:white;border:1px solid #eadfdb;padding:14px;border-radius:14px}
.stButton>button {border-radius:10px;font-weight:700}
.hero {padding:20px;border-radius:18px;background:linear-gradient(135deg,#7a161b,#a52a2f);color:white;margin-bottom:16px}
.hero h1 {color:white;margin:0}
</style>
""",
    unsafe_allow_html=True,
)

require_configuration()

# ---------------------------------------------------------------------
# Inicio de sesión
# ---------------------------------------------------------------------
if not is_logged_in():
    st.markdown(
        "<div class='hero'><h1>🐷 Embutidos Rodríguez</h1>"
        "<div>Base de datos permanente en Supabase</div></div>",
        unsafe_allow_html=True,
    )
    login_tab, signup_tab = st.tabs(["Iniciar sesión", "Crear cuenta"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Correo electrónico")
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button(
                "Entrar", type="primary", use_container_width=True
            )
            if submitted:
                try:
                    response = new_supabase_client().auth.sign_in_with_password(
                        {"email": email.strip(), "password": password}
                    )
                    save_auth(response)
                    st.rerun()
                except Exception as exc:
                    st.error(f"No fue posible iniciar sesión: {exc}")

    with signup_tab:
        st.caption("Crea únicamente la cuenta del propietario por ahora.")
        with st.form("signup_form"):
            new_email = st.text_input("Correo", key="signup_email")
            new_password = st.text_input(
                "Contraseña de al menos 6 caracteres",
                type="password",
                key="signup_password",
            )
            submitted_signup = st.form_submit_button(
                "Crear cuenta", use_container_width=True
            )
            if submitted_signup:
                try:
                    response = new_supabase_client().auth.sign_up(
                        {"email": new_email.strip(), "password": new_password}
                    )
                    if getattr(response, "session", None):
                        save_auth(response)
                        st.success("Cuenta creada e iniciada.")
                        st.rerun()
                    else:
                        st.success(
                            "Cuenta creada. Revisa tu correo para confirmar la cuenta "
                            "y después inicia sesión."
                        )
                except Exception as exc:
                    st.error(f"No fue posible crear la cuenta: {exc}")
    st.stop()

# ---------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------
settings = get_business_settings()
with st.sidebar:
    if settings.get("logo_url"):
        st.image(settings["logo_url"], use_container_width=True)
    st.markdown(f"## 🐷 {settings.get('business_name', 'Embutidos Rodríguez')}")
    st.caption(st.session_state.get("sb_user_email", ""))
    menu = st.radio(
        "Menú",
        [
            "Inicio",
            "Nueva venta",
            "Tickets",
            "Inventario",
            "Clientes",
            "Productos",
            "Cuentas por cobrar",
            "Reportes",
            "Configuración",
        ],
        label_visibility="collapsed",
    )
    if st.button("Cerrar sesión", use_container_width=True):
        try:
            current_client().auth.sign_out()
        except Exception:
            pass
        clear_auth()
        st.rerun()

# ---------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------
if menu == "Inicio":
    today = date.today()
    month_start = today.replace(day=1)
    rows = sales_summary(month_start, today)
    products = get_products()

    sales_total = sum(float(r.get("total") or 0) for r in rows)
    kilos = sum(float(r.get("total_kg") or 0) for r in rows)
    profit = sum(float(r.get("profit") or 0) for r in rows)
    receivable = sum(
        max(float(r.get("total") or 0) - float(r.get("paid_amount") or 0), 0)
        for r in rows
        if r.get("payment_status") != "Pagada"
    )
    stock = sum(float(p.get("stock_kg") or 0) for p in products)

    logo_cols = st.columns([1, 1, 4])
    if settings.get("logo_url"):
        logo_cols[0].image(settings["logo_url"], use_container_width=True)
    if settings.get("secondary_logo_url"):
        logo_cols[1].image(settings["secondary_logo_url"], use_container_width=True)
    st.markdown(
        f"<div class='hero'><h1>{escape(str(settings.get('business_name', 'Embutidos Rodríguez')))}</h1>"
        f"<div>{escape(str(settings.get('slogan', '')))}</div>"
        "<div style='margin-top:6px;font-size:12px'>Los datos se guardan permanentemente en Supabase.</div></div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Ventas del mes", money(sales_total))
    c2.metric("Kilos vendidos", f"{kilos:,.2f} kg")
    c3.metric("Utilidad bruta", money(profit))
    c4.metric("Por cobrar", money(receivable))
    c5.metric("Inventario", f"{stock:,.2f} kg")

    left, right = st.columns(2)
    with left:
        st.subheader("Ventas recientes")
        display = pd.DataFrame(rows[:10])
        if display.empty:
            st.info("Todavía no hay ventas.")
        else:
            st.dataframe(
                display[
                    [
                        "folio",
                        "sale_date",
                        "client_name",
                        "total_kg",
                        "total",
                        "payment_status",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
    with right:
        st.subheader("Alertas de inventario")
        low = [
            {
                "Producto": p["name"],
                "Presentación": p["presentation"],
                "Existencia": float(p.get("stock_kg") or 0),
                "Mínimo": float(p.get("min_stock_kg") or 0),
            }
            for p in products
            if float(p.get("stock_kg") or 0)
            <= float(p.get("min_stock_kg") or 0)
        ]
        if low:
            st.warning(f"{len(low)} producto(s) requieren atención.")
            st.dataframe(pd.DataFrame(low), use_container_width=True, hide_index=True)
        else:
            st.success("El inventario está por encima de los mínimos.")

# ---------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------
elif menu == "Productos":
    st.header("Productos")
    tab1, tab2, tab3 = st.tabs(["Catálogo visual", "Nuevo producto", "Editar producto"])

    with tab1:
        products = get_products()
        q = st.text_input("Buscar producto")
        if q:
            ql = q.lower()
            products = [
                p for p in products
                if ql in str(p.get("name", "")).lower()
                or ql in str(p.get("presentation", "")).lower()
            ]
        header_cols = st.columns([1, 1, 4])
        if settings.get("logo_url"):
            header_cols[0].image(settings["logo_url"], use_container_width=True)
        if settings.get("secondary_logo_url"):
            header_cols[1].image(settings["secondary_logo_url"], use_container_width=True)
        header_cols[2].markdown(
            f"## {settings.get('business_name', 'Embutidos Rodríguez')}\n"
            f"*{settings.get('slogan', '')}*"
        )
        if not products:
            st.info("No hay productos registrados.")
        else:
            cols = st.columns(3)
            for idx, product in enumerate(products):
                with cols[idx % 3]:
                    with st.container(border=True):
                        st.markdown("### 🐷")
                        st.markdown(f"### {product['name']}")
                        st.caption(product['presentation'])
                        st.write(f"**Precio público:** {money(product.get('public_price_per_kg'))}/kg")
                        st.write(f"**Mayoreo:** {money(product.get('wholesale_price_per_kg'))}/kg")
                        st.write(f"**Restaurante:** {money(product.get('restaurant_price_per_kg'))}/kg")
                        st.write(f"**Existencia:** {float(product.get('stock_kg') or 0):.3f} kg")

    with tab2:
        with st.form("new_product", clear_on_submit=True):
            name = st.text_input("Nombre")
            presentation = st.text_input("Presentación")
            c1, c2, c3 = st.columns(3)
            weight = c1.number_input(
                "Peso estándar kg", min_value=0.001, value=1.0, step=0.050
            )
            cost = c2.number_input("Costo por kg", min_value=0.0, step=1.0)
            minimum = c3.number_input(
                "Inventario mínimo kg", min_value=0.0, step=1.0
            )
            c1, c2, c3 = st.columns(3)
            public = c1.number_input("Precio público/kg", min_value=0.0, step=1.0)
            wholesale = c2.number_input(
                "Precio mayoreo/kg", min_value=0.0, step=1.0
            )
            restaurant = c3.number_input(
                "Precio restaurante/kg", min_value=0.0, step=1.0
            )
            if st.form_submit_button("Guardar producto", type="primary"):
                if not name.strip() or not presentation.strip():
                    st.error("Nombre y presentación son obligatorios.")
                else:
                    try:
                        current_client().table("products").insert(
                            {
                                "name": name.strip(),
                                "presentation": presentation.strip(),
                                "weight_kg": weight,
                                "cost_per_kg": cost,
                                "public_price_per_kg": public,
                                "wholesale_price_per_kg": wholesale,
                                "restaurant_price_per_kg": restaurant,
                                "min_stock_kg": minimum,
                            }
                        ).execute()
                        st.success("Producto guardado permanentemente.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with tab3:
        products = get_products()
        if not products:
            st.info("Primero registra un producto.")
        else:
            product_map = {
                f"{p['name']} — {p['presentation']}": p for p in products
            }
            label = st.selectbox("Producto", list(product_map))
            p = product_map[label]
            with st.form("edit_product"):
                name = st.text_input("Nombre", value=p["name"])
                presentation = st.text_input(
                    "Presentación", value=p["presentation"]
                )
                c1, c2, c3 = st.columns(3)
                weight = c1.number_input(
                    "Peso estándar kg",
                    min_value=0.001,
                    value=float(p.get("weight_kg") or 1),
                    step=0.050,
                )
                cost = c2.number_input(
                    "Costo/kg",
                    min_value=0.0,
                    value=float(p.get("cost_per_kg") or 0),
                    step=1.0,
                )
                minimum = c3.number_input(
                    "Mínimo kg",
                    min_value=0.0,
                    value=float(p.get("min_stock_kg") or 0),
                    step=1.0,
                )
                c1, c2, c3 = st.columns(3)
                public = c1.number_input(
                    "Público/kg",
                    min_value=0.0,
                    value=float(p.get("public_price_per_kg") or 0),
                    step=1.0,
                )
                wholesale = c2.number_input(
                    "Mayoreo/kg",
                    min_value=0.0,
                    value=float(p.get("wholesale_price_per_kg") or 0),
                    step=1.0,
                )
                restaurant = c3.number_input(
                    "Restaurante/kg",
                    min_value=0.0,
                    value=float(p.get("restaurant_price_per_kg") or 0),
                    step=1.0,
                )
                if st.form_submit_button("Actualizar", type="primary"):
                    try:
                        current_client().table("products").update(
                            {
                                "name": name.strip(),
                                "presentation": presentation.strip(),
                                "weight_kg": weight,
                                "cost_per_kg": cost,
                                "public_price_per_kg": public,
                                "wholesale_price_per_kg": wholesale,
                                "restaurant_price_per_kg": restaurant,
                                "min_stock_kg": minimum,
                            }
                        ).eq("id", p["id"]).execute()
                        st.success("Producto actualizado.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

# ---------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------
elif menu == "Clientes":
    st.header("Clientes")
    tab1, tab2, tab3, tab4 = st.tabs(["Directorio", "Nuevo cliente", "Editar cliente", "Precio especial"])

    with tab1:
        clients = get_clients()
        q = st.text_input("Buscar cliente")
        if q:
            ql = q.lower()
            clients = [
                c
                for c in clients
                if ql in str(c.get("business_name", "")).lower()
                or ql in str(c.get("city", "")).lower()
                or ql in str(c.get("client_type", "")).lower()
            ]
        if clients:
            view = pd.DataFrame(clients)
            st.dataframe(
                view[
                    [
                        "business_name",
                        "contact_name",
                        "phone",
                        "city",
                        "client_type",
                        "payment_terms",
                        "credit_days",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No hay clientes registrados.")

    with tab2:
        with st.form("new_client", clear_on_submit=True):
            business_name = st.text_input("Nombre comercial")
            contact_name = st.text_input("Encargado")
            phone = st.text_input("Teléfono")
            city = st.text_input("Ciudad")
            address = st.text_input("Dirección")
            c1, c2, c3 = st.columns(3)
            client_type = c1.selectbox(
                "Tipo",
                [
                    "Público general",
                    "Tienda",
                    "Restaurante",
                    "Mayorista",
                    "Cadena comercial",
                ],
            )
            payment_terms = c2.selectbox("Condición de pago", ["Contado", "Crédito"])
            credit_days = c3.number_input(
                "Días de crédito", min_value=0, step=1
            )
            notes = st.text_area("Observaciones")
            if st.form_submit_button("Guardar cliente", type="primary"):
                if not business_name.strip():
                    st.error("El nombre comercial es obligatorio.")
                else:
                    try:
                        current_client().table("clients").insert(
                            {
                                "business_name": business_name.strip(),
                                "contact_name": contact_name,
                                "phone": phone,
                                "city": city,
                                "address": address,
                                "client_type": client_type,
                                "payment_terms": payment_terms,
                                "credit_days": int(credit_days),
                                "notes": notes,
                            }
                        ).execute()
                        st.success("Cliente guardado permanentemente.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with tab3:
        clients = get_clients()
        if not clients:
            st.info("Primero registra un cliente.")
        else:
            client_map = {c["business_name"]: c for c in clients}
            selected_label = st.selectbox("Cliente a editar", list(client_map))
            c = client_map[selected_label]
            with st.form("edit_client"):
                business_name = st.text_input("Nombre comercial", value=c.get("business_name") or "")
                contact_name = st.text_input("Encargado", value=c.get("contact_name") or "")
                phone = st.text_input("Teléfono", value=c.get("phone") or "")
                city = st.text_input("Ciudad", value=c.get("city") or "")
                address = st.text_input("Dirección", value=c.get("address") or "")
                types = ["Público general", "Tienda", "Restaurante", "Mayorista", "Cadena comercial"]
                terms = ["Contado", "Crédito"]
                c1, c2, c3 = st.columns(3)
                client_type = c1.selectbox(
                    "Tipo", types,
                    index=types.index(c.get("client_type")) if c.get("client_type") in types else 0,
                )
                payment_terms = c2.selectbox(
                    "Condición de pago", terms,
                    index=terms.index(c.get("payment_terms")) if c.get("payment_terms") in terms else 0,
                )
                credit_days = c3.number_input(
                    "Días de crédito", min_value=0, step=1,
                    value=int(c.get("credit_days") or 0),
                )
                notes = st.text_area("Observaciones", value=c.get("notes") or "")
                active = st.checkbox("Cliente activo", value=bool(c.get("active", True)))
                if st.form_submit_button("Guardar cambios", type="primary"):
                    if not business_name.strip():
                        st.error("El nombre comercial es obligatorio.")
                    else:
                        try:
                            current_client().table("clients").update({
                                "business_name": business_name.strip(),
                                "contact_name": contact_name,
                                "phone": phone,
                                "city": city,
                                "address": address,
                                "client_type": client_type,
                                "payment_terms": payment_terms,
                                "credit_days": int(credit_days),
                                "notes": notes,
                                "active": active,
                            }).eq("id", c["id"]).execute()
                            st.success("Cliente actualizado correctamente.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

    with tab4:
        clients = get_clients()
        products = get_products()
        if not clients or not products:
            st.info("Se necesitan clientes y productos.")
        else:
            client_map = {c["business_name"]: c["id"] for c in clients}
            product_map = {
                f"{p['name']} — {p['presentation']}": p["id"] for p in products
            }
            client_label = st.selectbox("Cliente", list(client_map))
            product_label = st.selectbox("Producto", list(product_map))
            price = st.number_input(
                "Precio especial por kg", min_value=0.0, step=1.0
            )
            if st.button("Guardar precio especial", type="primary"):
                try:
                    existing = records(
                        current_client()
                        .table("client_prices")
                        .select("id")
                        .eq("client_id", client_map[client_label])
                        .eq("product_id", product_map[product_label])
                        .limit(1)
                        .execute()
                    )
                    payload = {
                        "client_id": client_map[client_label],
                        "product_id": product_map[product_label],
                        "price_per_kg": price,
                    }
                    if existing:
                        current_client().table("client_prices").update(
                            payload
                        ).eq("id", existing[0]["id"]).execute()
                    else:
                        current_client().table("client_prices").insert(
                            payload
                        ).execute()
                    st.success("Precio especial guardado.")
                except Exception as exc:
                    st.error(str(exc))

# ---------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------
elif menu == "Inventario":
    st.header("Inventario")
    tab1, tab2, tab3 = st.tabs(["Existencias", "Movimiento", "Historial"])

    with tab1:
        products = get_products()
        if products:
            view = pd.DataFrame(products)
            st.dataframe(
                view[
                    [
                        "name",
                        "presentation",
                        "stock_kg",
                        "min_stock_kg",
                        "cost_per_kg",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No hay productos.")

    with tab2:
        products = get_products()
        if not products:
            st.info("Primero registra un producto.")
        else:
            product_map = {
                f"{p['name']} — {p['presentation']}": p for p in products
            }
            label = st.selectbox("Producto", list(product_map))
            p = product_map[label]
            movement_type = st.selectbox(
                "Tipo",
                [
                    "Entrada de producción",
                    "Devolución de cliente",
                    "Ajuste positivo",
                    "Merma",
                    "Ajuste negativo",
                ],
            )
            quantity = st.number_input(
                "Cantidad kg", min_value=0.001, step=0.100, format="%.3f"
            )
            reference = st.text_input("Referencia o lote")
            notes = st.text_area("Notas")
            if st.button("Guardar movimiento", type="primary"):
                sign = -1 if movement_type in ("Merma", "Ajuste negativo") else 1
                new_stock = float(p.get("stock_kg") or 0) + sign * quantity
                if new_stock < 0:
                    st.error("El inventario quedaría negativo.")
                else:
                    try:
                        current_client().table("products").update(
                            {"stock_kg": new_stock}
                        ).eq("id", p["id"]).execute()
                        current_client().table("inventory_movements").insert(
                            {
                                "movement_date": date.today().isoformat(),
                                "product_id": p["id"],
                                "movement_type": movement_type,
                                "quantity_kg": sign * quantity,
                                "reference": reference,
                                "notes": notes,
                            }
                        ).execute()
                        st.success("Movimiento guardado permanentemente.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with tab3:
        history = records(
            current_client()
            .table("inventory_movements")
            .select("*, products(name,presentation)")
            .order("created_at", desc=True)
            .limit(300)
            .execute()
        )
        if not history:
            st.info("Todavía no hay movimientos.")
        else:
            rows = []
            for movement in history:
                product = movement.get("products") or {}
                rows.append(
                    {
                        "Fecha": movement["movement_date"],
                        "Producto": product.get("name"),
                        "Presentación": product.get("presentation"),
                        "Tipo": movement["movement_type"],
                        "Kg": movement["quantity_kg"],
                        "Referencia": movement.get("reference"),
                        "Notas": movement.get("notes"),
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------
# Nueva venta
# ---------------------------------------------------------------------
elif menu == "Nueva venta":
    st.header("Nueva venta")
    clients = get_clients()
    products = get_products()
    if not clients or not products:
        st.warning("Primero registra al menos un cliente y un producto.")
        st.stop()

    if "cart" not in st.session_state:
        st.session_state["cart"] = []

    client_map = {
        f"{c['business_name']} — {c['client_type']}": c for c in clients
    }
    c1, c2, c3 = st.columns(3)
    client_label = c1.selectbox("Cliente", list(client_map))
    selected_client = client_map[client_label]
    sale_date = c2.date_input("Fecha", date.today())
    folio = c3.text_input("Folio", next_folio())

    product_map = {
        f"{p['name']} — {p['presentation']} | Stock {float(p.get('stock_kg') or 0):.3f} kg": p
        for p in products
    }
    product_label = st.selectbox("Producto", list(product_map))
    selected_product = product_map[product_label]
    suggested_price = get_client_price(
        selected_client["id"], selected_product
    )

    c1, c2, c3, c4 = st.columns(4)
    units = c1.number_input(
        "Unidades/paquetes", min_value=0.01, value=1.0, step=1.0
    )
    kg = c2.number_input(
        "Kilos reales",
        min_value=0.001,
        value=float(selected_product.get("weight_kg") or 1),
        step=0.050,
        format="%.3f",
    )
    price = c3.number_input(
        "Precio por kg", min_value=0.0, value=suggested_price, step=1.0
    )
    c4.metric("Subtotal", money(kg * price))

    if st.button("Agregar producto", type="primary", use_container_width=True):
        already = sum(
            float(item["weight_kg"])
            for item in st.session_state["cart"]
            if item["product_id"] == selected_product["id"]
        )
        if already + kg > float(selected_product.get("stock_kg") or 0):
            st.error("La cantidad supera el inventario disponible.")
        else:
            cost = float(selected_product.get("cost_per_kg") or 0)
            st.session_state["cart"].append(
                {
                    "product_id": selected_product["id"],
                    "Producto": selected_product["name"],
                    "Presentación": selected_product["presentation"],
                    "quantity_units": units,
                    "weight_kg": kg,
                    "price_per_kg": price,
                    "cost_per_kg": cost,
                    "subtotal": kg * price,
                    "cost_total": kg * cost,
                    "profit": kg * (price - cost),
                }
            )
            st.rerun()

    cart = st.session_state["cart"]
    if cart:
        st.subheader("Productos de la venta")
        st.dataframe(
            pd.DataFrame(cart)[
                [
                    "Producto",
                    "Presentación",
                    "quantity_units",
                    "weight_kg",
                    "price_per_kg",
                    "subtotal",
                    "profit",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Kilos", f"{sum(i['weight_kg'] for i in cart):.3f}")
        c2.metric("Total", money(sum(i["subtotal"] for i in cart)))
        c3.metric("Utilidad bruta", money(sum(i["profit"] for i in cart)))

        remove = st.selectbox(
            "Quitar renglón", list(range(1, len(cart) + 1))
        )
        if st.button("Quitar producto"):
            cart.pop(remove - 1)
            st.rerun()
    else:
        st.info("Agrega productos a la venta.")

    c1, c2 = st.columns(2)
    payment_status = c1.selectbox(
        "Estado de pago", ["Pagada", "Pendiente", "Parcial"]
    )
    payment_method = c2.selectbox(
        "Forma de pago", ["Efectivo", "Transferencia", "Crédito", "Otro"]
    )
    notes = st.text_area("Observaciones")

    c1, c2 = st.columns(2)
    if c1.button(
        "Guardar venta",
        type="primary",
        use_container_width=True,
        disabled=not cart,
    ):
        try:
            sb = current_client()

            # Verificar inventario nuevamente antes de guardar.
            latest_products = {
                p["id"]: p for p in get_products()
            }
            for item in cart:
                current_stock = float(
                    latest_products[item["product_id"]].get("stock_kg") or 0
                )
                if item["weight_kg"] > current_stock:
                    raise RuntimeError(
                        f"Inventario insuficiente para {item['Producto']}."
                    )

            sale_response = (
                sb.table("sales")
                .insert(
                    {
                        "folio": folio.strip(),
                        "sale_date": sale_date.isoformat(),
                        "client_id": selected_client["id"],
                        "payment_status": payment_status,
                        "payment_method": payment_method,
                        "notes": notes,
                    }
                )
                .execute()
            )
            sale_rows = records(sale_response)
            if not sale_rows:
                raise RuntimeError("Supabase no devolvió la venta creada.")
            sale_id = sale_rows[0]["id"]

            for item in cart:
                sb.table("sale_items").insert(
                    {
                        "sale_id": sale_id,
                        "product_id": item["product_id"],
                        "quantity_units": item["quantity_units"],
                        "weight_kg": item["weight_kg"],
                        "price_per_kg": item["price_per_kg"],
                        "cost_per_kg": item["cost_per_kg"],
                        "subtotal": item["subtotal"],
                        "cost_total": item["cost_total"],
                        "profit": item["profit"],
                    }
                ).execute()

                latest = latest_products[item["product_id"]]
                new_stock = float(latest.get("stock_kg") or 0) - float(
                    item["weight_kg"]
                )
                sb.table("products").update(
                    {"stock_kg": new_stock}
                ).eq("id", item["product_id"]).execute()

                sb.table("inventory_movements").insert(
                    {
                        "movement_date": sale_date.isoformat(),
                        "product_id": item["product_id"],
                        "movement_type": "Venta",
                        "quantity_kg": -float(item["weight_kg"]),
                        "reference": folio.strip(),
                        "notes": notes,
                    }
                ).execute()

            if payment_status == "Pagada":
                sb.table("payments").insert(
                    {
                        "sale_id": sale_id,
                        "payment_date": sale_date.isoformat(),
                        "amount": sum(float(i["subtotal"]) for i in cart),
                        "payment_method": payment_method,
                        "notes": "Pago registrado con la venta",
                    }
                ).execute()

            st.session_state["cart"] = []
            st.success(
                f"Venta {folio} guardada permanentemente en Supabase."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"No fue posible guardar la venta: {exc}")

    if c2.button(
        "Vaciar carrito", use_container_width=True, disabled=not cart
    ):
        st.session_state["cart"] = []
        st.rerun()

# ---------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------
elif menu == "Tickets":
    st.header("Tickets")
    rows = sales_summary()
    if not rows:
        st.info("No hay ventas.")
    else:
        search = st.text_input("Buscar por folio o cliente")
        if search:
            sl = search.lower()
            rows = [
                r
                for r in rows
                if sl in str(r.get("folio", "")).lower()
                or sl in str(r.get("client_name", "")).lower()
            ]
        options = {
            f"{r['folio']} — {r['client_name']} — {float(r['total_kg']):.3f} kg — {money(r['total'])}": r[
                "id"
            ]
            for r in rows
        }
        selected = st.selectbox("Venta", list(options))
        c1, c2 = st.columns(2)
        paper = c1.selectbox("Papel", ["58 mm", "80 mm"])
        kind = c2.selectbox(
            "Documento", ["Ticket con precios", "Remisión sin precios"]
        )
        ticket = build_ticket_html(
            options[selected], paper, kind == "Ticket con precios"
        )
        components.html(ticket, height=720, scrolling=True)
        st.download_button(
            "Descargar ticket HTML",
            ticket.encode("utf-8"),
            file_name=f"ticket_{selected.split(' — ')[0]}.html",
            mime="text/html",
            use_container_width=True,
        )

# ---------------------------------------------------------------------
# Cuentas por cobrar
# ---------------------------------------------------------------------
elif menu == "Cuentas por cobrar":
    st.header("Cuentas por cobrar")
    rows = [
        r
        for r in sales_summary()
        if float(r.get("total") or 0) - float(r.get("paid_amount") or 0) > 0.005
    ]
    if not rows:
        st.success("No hay saldos pendientes.")
    else:
        for row in rows:
            row["Saldo"] = float(row["total"]) - float(
                row.get("paid_amount") or 0
            )
        st.metric("Total por cobrar", money(sum(r["Saldo"] for r in rows)))
        st.dataframe(
            pd.DataFrame(rows)[
                [
                    "folio",
                    "sale_date",
                    "client_name",
                    "total",
                    "paid_amount",
                    "Saldo",
                    "payment_status",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
        option_map = {
            f"{r['folio']} — {r['client_name']} — Saldo {money(r['Saldo'])}": r
            for r in rows
        }
        selected = st.selectbox("Venta a abonar", list(option_map))
        row = option_map[selected]
        c1, c2 = st.columns(2)
        amount = c1.number_input(
            "Importe del pago",
            min_value=0.01,
            max_value=float(row["Saldo"]),
            value=float(row["Saldo"]),
            step=1.0,
        )
        method = c2.selectbox(
            "Forma de pago",
            ["Efectivo", "Transferencia", "Otro"],
            key="collection_method",
        )
        notes = st.text_input("Notas del pago")
        if st.button("Registrar pago", type="primary"):
            try:
                current_client().table("payments").insert(
                    {
                        "sale_id": row["id"],
                        "payment_date": date.today().isoformat(),
                        "amount": amount,
                        "payment_method": method,
                        "notes": notes,
                    }
                ).execute()
                remaining = float(row["Saldo"]) - amount
                status = "Pagada" if remaining <= 0.005 else "Parcial"
                current_client().table("sales").update(
                    {"payment_status": status}
                ).eq("id", row["id"]).execute()
                st.success("Pago guardado permanentemente.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

# ---------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------
elif menu == "Reportes":
    st.header("Reportes")
    c1, c2, c3 = st.columns(3)
    quick = c1.selectbox(
        "Periodo",
        ["Este mes", "Mes anterior", "Este año", "Personalizado"],
    )
    today = date.today()
    if quick == "Este mes":
        start, end = today.replace(day=1), today
    elif quick == "Mes anterior":
        last_month_end = today.replace(day=1) - timedelta(days=1)
        start, end = last_month_end.replace(day=1), last_month_end
    elif quick == "Este año":
        start, end = date(today.year, 1, 1), today
    else:
        start = c2.date_input("Desde", today.replace(day=1))
        end = c3.date_input("Hasta", today)

    rows = sales_summary(start, end)
    if not rows:
        st.info("No hay ventas en el periodo.")
    else:
        total = sum(float(r.get("total") or 0) for r in rows)
        kilos = sum(float(r.get("total_kg") or 0) for r in rows)
        profit = sum(float(r.get("profit") or 0) for r in rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Ventas", money(total))
        c2.metric("Kilos", f"{kilos:,.3f}")
        c3.metric("Utilidad bruta", money(profit))

        df = pd.DataFrame(rows)
        st.subheader("Por cliente")
        by_client = (
            df.groupby("client_name", as_index=False)
            .agg(
                Kilos=("total_kg", "sum"),
                Ventas=("total", "sum"),
                Utilidad=("profit", "sum"),
            )
            .sort_values("Ventas", ascending=False)
        )
        st.dataframe(by_client, use_container_width=True, hide_index=True)

        st.subheader("Detalle")
        st.dataframe(
            df[
                [
                    "folio",
                    "sale_date",
                    "client_name",
                    "total_kg",
                    "total",
                    "profit",
                    "paid_amount",
                    "payment_status",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "Descargar reporte CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"ventas_{start}_{end}.csv",
            mime="text/csv",
        )

# ---------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------
elif menu == "Configuración":
    st.header("Configuración e identidad")
    st.caption("Estos datos y logotipos quedan guardados permanentemente en Supabase.")
    current = get_business_settings()
    with st.form("business_settings_form"):
        business_name = st.text_input("Nombre comercial", value=current.get("business_name") or "")
        slogan = st.text_input("Eslogan", value=current.get("slogan") or "")
        phone = st.text_input("Teléfono", value=current.get("phone") or "")
        address = st.text_input("Dirección o ciudad", value=current.get("address") or "")
        logo_file = st.file_uploader("Logotipo principal", type=["png", "jpg", "jpeg", "webp"])
        secondary_file = st.file_uploader("Logotipo de Grupo Comercial Rodríguez", type=["png", "jpg", "jpeg", "webp"])
        submitted = st.form_submit_button("Guardar configuración", type="primary")
        if submitted:
            try:
                logo_url = current.get("logo_url") or ""
                secondary_logo_url = current.get("secondary_logo_url") or ""
                if logo_file:
                    logo_url = upload_branding_file(logo_file, "logo-principal")
                if secondary_file:
                    secondary_logo_url = upload_branding_file(secondary_file, "logo-secundario")
                save_business_settings({
                    "business_name": business_name.strip() or "Embutidos Rodríguez",
                    "slogan": slogan.strip(),
                    "phone": phone.strip(),
                    "address": address.strip(),
                    "logo_url": logo_url,
                    "secondary_logo_url": secondary_logo_url,
                })
                st.success("Configuración guardada permanentemente.")
                st.rerun()
            except Exception as exc:
                st.error(
                    "No fue posible guardar la configuración. Ejecuta primero el archivo "
                    f"actualizacion_v5_1.sql en Supabase. Detalle: {exc}"
                )

    c1, c2 = st.columns(2)
    if current.get("logo_url"):
        c1.image(current["logo_url"], caption="Logotipo principal", use_container_width=True)
    if current.get("secondary_logo_url"):
        c2.image(current["secondary_logo_url"], caption="Logotipo secundario", use_container_width=True)

