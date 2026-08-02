
# Embutidos Rodríguez IA Mobile

Aplicación comercial para usar desde iPhone, Android o computadora.

## Módulos

- Tablero de ventas, kilos, utilidad bruta, inventario y cobranza
- Clientes y precios especiales
- Catálogo con fotografías
- Venta con varios productos
- Inventario y movimientos
- Tickets térmicos 58 mm y 80 mm
- Remisiones sin precios
- Reportes por cliente, producto, mes, año y rango
- Asistente de inteligencia artificial basado en los datos del negocio
- Respaldo descargable

## Cómo funciona la IA

El asistente puede responder preguntas como:

- ¿Cuánto vendí este mes?
- ¿Quiénes son mis mejores clientes?
- ¿Quién me debe?
- ¿Qué producto deja más utilidad bruta?
- ¿Qué inventario está bajo?
- ¿Qué conviene revisar para la siguiente producción?

La IA recibe un resumen de la base de datos al hacer cada consulta. No modifica ventas,
inventario ni clientes; únicamente analiza y responde.

## Seguridad de la clave

Nunca escribas la clave de OpenAI dentro de `app.py` ni la subas a GitHub.

En Streamlit Community Cloud:

1. Abre la configuración de la aplicación.
2. Entra a **Secrets**.
3. Agrega:

```toml
OPENAI_API_KEY = "tu_clave"
```

Existe un archivo de ejemplo en `.streamlit/secrets.toml.example`.

## Ejecución en computadora

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Publicación para el celular

1. Sube esta carpeta a un repositorio privado de GitHub.
2. Crea una aplicación en Streamlit Community Cloud.
3. Selecciona el repositorio y `app.py`.
4. Configura `OPENAI_API_KEY` en Secrets.
5. Abre la dirección `streamlit.app` desde Safari en el iPhone.
6. En Safari toca **Compartir** y después **Agregar a pantalla de inicio**.

Así aparecerá como un acceso directo similar a una app.

## Consideraciones importantes

- Streamlit Community Cloud puede servir para iniciar y probar.
- La base SQLite local es sencilla, pero para una operación diaria estable y varios usuarios
  se recomienda migrar después a PostgreSQL administrado.
- La utilidad calculada es utilidad bruta estimada según el costo por kilogramo registrado.
- La impresión Bluetooth depende de la compatibilidad de la impresora con iPhone,
  AirPrint, navegador o la app del fabricante.
- Realiza respaldos frecuentes.
