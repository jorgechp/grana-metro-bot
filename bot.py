"""
Bot de Telegram para consultar horarios en tiempo real del Metro de Granada.

Licencia: GNU General Public License v3.0
Autor: Jorge Chamorro Padial
"""

import json, os, requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN no definido en .env")

# Configuración de API
API_BASE = "https://movgr.apis.mianfg.me"
FAVORITOS_FILE = "favoritos.json"
favoritos = {}
paradas = {}
ultimo_mensaje_menu = {}

# Menú principal del bot (teclado persistente)
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🔍 Ver paradas"), KeyboardButton("⭐ Favoritas")],
        [KeyboardButton("📄 Información")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

def cargar_favoritos():
    """
    Carga las paradas favoritas desde el archivo JSON en disco.

    Returns:
        None
    """
    global favoritos
    if os.path.exists(FAVORITOS_FILE):
        with open(FAVORITOS_FILE, "r", encoding="utf-8") as f:
            favoritos = {int(k): set(v) for k, v in json.load(f).items()}
    else:
        favoritos = {}

def guardar_favoritos():
    """
    Guarda las paradas favoritas en un archivo JSON.

    Returns:
        None
    """
    with open(FAVORITOS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): list(v) for k, v in favoritos.items()}, f, ensure_ascii=False, indent=2)

def get_favoritos(user_id: int) -> set:
    """
    Obtiene el conjunto de paradas favoritas del usuario.

    Args:
        user_id: ID del usuario de Telegram.

    Returns:
        Conjunto de IDs de paradas favoritas.
    """
    return favoritos.get(user_id, set())

def toggle_favorito(user_id: int, parada_id: str) -> str:
    """
    Añade o elimina una parada de la lista de favoritas del usuario.

    Args:
        user_id: ID del usuario de Telegram.
        parada_id: ID de la parada.

    Returns:
        "added" si se añadió, "removed" si se eliminó, o "limit" si ya hay 5 favoritas.
    """
    favs = favoritos.setdefault(user_id, set())
    if parada_id in favs:
        favs.remove(parada_id)
        guardar_favoritos()
        return "removed"
    else:
        if len(favs) >= 5:
            return "limit"
        favs.add(parada_id)
        guardar_favoritos()
        return "added"

def cargar_paradas():
    """
    Carga todas las paradas del metro de Granada desde la API y las guarda globalmente.

    Returns:
        None
    """
    global paradas
    resp = requests.get(f"{API_BASE}/metro/paradas")
    resp.raise_for_status()
    data = resp.json()
    paradas = {p["id"]: p["nombre"] for p in data}

async def enviar_mensaje_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Envía el mensaje del menú principal eliminando el anterior si existe.

    Args:
        update: Objeto Update de Telegram.
        context: Objeto ContextTypes con información contextual.

    Returns:
        None
    """
    user_id = update.effective_user.id

    # Intentar eliminar el mensaje anterior si existe
    if user_id in ultimo_mensaje_menu:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=ultimo_mensaje_menu[user_id]
            )
        except Exception:
            pass  # Ignorar errores si el mensaje ya fue eliminado

    # Enviar nuevo mensaje y guardar su ID
    mensaje = await update.message.reply_text("¿Qué deseas hacer?", reply_markup=MAIN_MENU)
    ultimo_mensaje_menu[user_id] = mensaje.message_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra al usuario la lista de paradas para consultar trenes disponibles.

    Args:
        update: Objeto Update de Telegram con información del mensaje.
        context: Objeto ContextTypes con información contextual del bot.

    Returns:
        None
    """
    items = list(paradas.items())
    botones = []

    for i in range(0, len(items), 2):
        fila = []
        for pid, nombre in items[i:i+2]:
            fila.append(InlineKeyboardButton(nombre, callback_data=f"ver:{pid}"))
        botones.append(fila)

    await update.message.reply_text(
        "Selecciona una parada para ver los próximos trenes:",
        reply_markup=InlineKeyboardMarkup(botones)
    )
    await enviar_mensaje_menu(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestiona los botones inline: ver parada, añadir/quitar de favoritas o eliminar favorita.

    Args:
        update: Objeto Update que contiene la acción del usuario.
        context: Objeto ContextTypes que contiene contexto adicional.

    Returns:
        None
    """
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action, parada_id = query.data.split(":", 1)
    nombre = paradas.get(parada_id, "Desconocida")

    if action == "ver":
        r = requests.get(f"{API_BASE}/metro/llegadas/{parada_id}")
        if r.status_code != 200 or "proximos" not in r.json():
            await query.edit_message_text("Error al consultar datos.")
            return

        info = r.json()
        mensajes = [f"🚉 *{nombre}*"]
        for t in info["proximos"][:3]:
            mensajes.append(f"• En {t['minutos']} min → {t['direccion']}")

        fav = parada_id in get_favoritos(user_id)
        texto = "\n".join(mensajes) + ("\n⭐ Favorita" if fav else "")
        btn = InlineKeyboardButton(
            "⭐ Quitar favorita" if fav else "➕ Añadir favorita",
            callback_data=f"toggle:{parada_id}"
        )
        await query.edit_message_text(
            texto, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[btn]])
        )

    elif action == "toggle":
        resultado = toggle_favorito(user_id, parada_id)
        if resultado == "added":
            msg = f"✅ {nombre} añadida a favoritas."
        elif resultado == "removed":
            msg = f"❌ {nombre} quitada de favoritas."
        elif resultado == "limit":
            msg = (
                f"⚠️ Ya tienes 5 paradas favoritas.\n"
                f"Elimina una antes de añadir más."
            )
        else:
            msg = "⚠️ Error al actualizar favoritas."

        await query.edit_message_text(msg)
        mensaje = await query.message.reply_text("¿Qué deseas hacer?", reply_markup=MAIN_MENU)
        ultimo_mensaje_menu[user_id] = mensaje.message_id

    elif action == "del":
        get_favoritos(user_id).discard(parada_id)
        guardar_favoritos()
        await query.edit_message_text(f"❌ {paradas.get(parada_id, parada_id)} eliminada de tus favoritas.")
        mensaje = await query.message.reply_text("¿Qué deseas hacer?", reply_markup=MAIN_MENU)
        ultimo_mensaje_menu[user_id] = mensaje.message_id

async def favoritas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra las paradas favoritas del usuario, incluyendo horarios y opción de eliminar.

    Args:
        update: Objeto Update de Telegram con la petición del usuario.
        context: Objeto ContextTypes con el estado del contexto del bot.

    Returns:
        None
    """
    user_id = update.effective_user.id
    favs = get_favoritos(user_id)
    if not favs:
        await update.message.reply_text("No tienes favoritas aún. Usa '🔍 Ver paradas'.", reply_markup=MAIN_MENU)
        return

    for pid in favs:
        nombre = paradas.get(pid, pid)
        r = requests.get(f"{API_BASE}/metro/llegadas/{pid}")
        if r.status_code != 200:
            await update.message.reply_text(f"{nombre}: ⚠️ error", reply_markup=MAIN_MENU)
            continue

        info = r.json()
        lines = [f"🚉 *{nombre}*"]
        for t in info["proximos"][:2]:
            lines.append(f"• En {t['minutos']} min → {t['direccion']}")
        texto = "\n".join(lines)

        boton = InlineKeyboardButton(f"❌ Eliminar {nombre}", callback_data=f"del:{pid}")
        reply_markup = InlineKeyboardMarkup([[boton]])

        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=reply_markup)

    await enviar_mensaje_menu(update, context)

async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra información del bot, licencia, agradecimientos y enlace al código fuente.

    Args:
        update: Objeto Update con el mensaje del usuario.
        context: Objeto ContextTypes con información contextual.

    Returns:
        None
    """
    mensaje = (
        "📄 *Información del bot*\n\n"
        "👤 Autor: Tu Nombre Aquí\n"
        "📝 Licencia: GNU General Public License v3.0\n"
        "🙏 Gracias a Miguel Ángel Fernández por la API MovGR: [API pública](https://movgr.apis.mianfg.me)\n"
        "💻 Código fuente en GitHub: [Ver repositorio](https://github.com/jorgechp/grana-metro-bot)\n"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=MAIN_MENU)
    await enviar_mensaje_menu(update, context)

async def mensaje_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja los botones principales del menú persistente del bot.

    Args:
        update: Objeto Update con el mensaje del usuario.
        context: Objeto ContextTypes con información contextual.

    Returns:
        None
    """
    texto = update.message.text.lower()
    if "paradas" in texto:
        await start(update, context)
    elif "favoritas" in texto:
        await favoritas_cmd(update, context)
    elif "información" in texto or "info" in texto:
        await info_cmd(update, context)

if __name__ == "__main__":
    cargar_favoritos()
    cargar_paradas()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("favoritas", favoritas_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensaje_menu))
    app.run_polling()