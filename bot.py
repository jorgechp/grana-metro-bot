"""
Bot de Telegram para consultar horarios en tiempo real del Metro de Granada,
con visualización del estado de la línea en dos columnas y emojis condicionales.

Licencia: GNU General Public License v3.0
Autor: Jorge Chamorro Padial
"""

import json
import os
import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv

API_BASE = "https://movgr.apis.mianfg.me"
FAVORITOS_FILE = "favoritos.json"
NUMERO_DE_TRENES_A_MOSTRAR = 4

# Memoria en ejecución
favoritos = {}
paradas = {}

# Teclado persistente con opción textual
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🔍 Ver paradas"), KeyboardButton("⭐ Favoritas")],
        [KeyboardButton("🚆 Ver toda la línea"), KeyboardButton("📄 Información")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

# Carga de token
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN no definido en .env")


def cargar_favoritos():
    """Carga las paradas favoritas desde un archivo JSON."""
    global favoritos
    if os.path.exists(FAVORITOS_FILE):
        with open(FAVORITOS_FILE, "r", encoding="utf-8") as f:
            favoritos = {int(k): set(v) for k, v in json.load(f).items()}
    else:
        favoritos = {}


def guardar_favoritos():
    """Guarda las paradas favoritas en un archivo JSON."""
    with open(FAVORITOS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): list(v) for k, v in favoritos.items()}, f, ensure_ascii=False, indent=2)


def get_favoritos(user_id: int) -> set:
    """
    Devuelve el conjunto de IDs de paradas favoritas de un usuario.

    Args:
        user_id (int): ID de Telegram del usuario.

    Returns:
        set: IDs de paradas favoritas.
    """
    return favoritos.get(user_id, set())


def toggle_favorito(user_id: int, parada_id: str) -> str:
    """
    Añade o elimina una parada de las favoritas del usuario.

    Args:
        user_id (int): ID de Telegram del usuario.
        parada_id (str): ID de la parada.

    Returns:
        str: "added", "removed" o "limit".
    """
    favs = favoritos.setdefault(user_id, set())
    if parada_id in favs:
        favs.remove(parada_id)
        guardar_favoritos()
        return "removed"
    if len(favs) >= 5:
        return "limit"
    favs.add(parada_id)
    guardar_favoritos()
    return "added"


def cargar_paradas():
    """Carga todas las paradas del metro desde la API MovGR."""
    global paradas
    resp = requests.get(f"{API_BASE}/metro/paradas")
    resp.raise_for_status()
    data = resp.json()
    paradas = {p["id"]: p["nombre"] for p in data}


async def enviar_mensaje_menu(chat_id: int, bot, user_data: dict):
    """
    Envía o actualiza el menú principal, eliminando el anterior si exista.

    Args:
        chat_id (int): ID del chat.
        bot: Instancia del bot.
        user_data (dict): context.user_data para guardar message_id.
    """
    old = user_data.get("menu_msg_id")
    if old:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old)
        except:
            pass
    sent = await bot.send_message(chat_id=chat_id, text="¿Qué deseas hacer?", reply_markup=MAIN_MENU)
    user_data["menu_msg_id"] = sent.message_id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de paradas como botones inline."""
    items = list(paradas.items())
    botones = []
    for i in range(0, len(items), 2):
        fila = [
            InlineKeyboardButton(nombre, callback_data=f"ver:{pid}")
            for pid, nombre in items[i:i+2]
        ]
        botones.append(fila)

    await update.message.reply_text(
        "Selecciona una parada para ver los próximos trenes:",
        reply_markup=InlineKeyboardMarkup(botones)
    )
    await enviar_mensaje_menu(update.effective_chat.id, context.bot, context.user_data)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja acciones de botones inline: ver llegadas, toggle favorito, eliminar favorito.
    """
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    action, pid = query.data.split(":", 1)
    nombre = paradas.get(pid, "Desconocida")

    if action == "ver":
        resp = requests.get(f"{API_BASE}/metro/llegadas/{pid}")
        if resp.status_code != 200:
            await query.edit_message_text("❌ Error al consultar datos.")
        else:
            info = resp.json()
            if not info.get("proximos"):
                texto = f"🚉 *{nombre}*\n_No hay trenes próximos._"
            else:
                lines = [f"🚉 *{nombre}*"]
                for t in info["proximos"][:NUMERO_DE_TRENES_A_MOSTRAR]:
                    lines.append(f"• En {t['minutos']} min → {t['direccion']}")
                texto = "\n".join(lines)

            fav = pid in get_favoritos(user_id)
            texto += "\n⭐ Favorita" if fav else ""
            btn = InlineKeyboardButton(
                "⭐ Quitar favorita" if fav else "➕ Añadir favorita",
                callback_data=f"toggle:{pid}"
            )
            await query.edit_message_text(texto, parse_mode="Markdown",
                                          reply_markup=InlineKeyboardMarkup([[btn]]))

    elif action == "toggle":
        res = toggle_favorito(user_id, pid)
        msg = {
            "added": "✅ Añadida a favoritas.",
            "removed": "❌ Eliminada de favoritas.",
            "limit": "⚠️ Límite de 5 favoritas alcanzado."
        }[res]
        await query.edit_message_text(msg)

    elif action == "del":
        get_favoritos(user_id).discard(pid)
        guardar_favoritos()
        await query.edit_message_text("❌ Favorita eliminada.")

    await enviar_mensaje_menu(chat_id, context.bot, context.user_data)


async def favoritas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra las paradas favoritas y permite eliminarlas."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    favs = get_favoritos(user_id)
    if not favs:
        await update.message.reply_text("No tienes favoritas aún.", reply_markup=MAIN_MENU)
        return

    for pid in favs:
        nombre = paradas.get(pid, pid)
        resp = requests.get(f"{API_BASE}/metro/llegadas/{pid}")
        if resp.status_code != 200:
            await update.message.reply_text(f"{nombre}: ⚠️ Error")
            continue
        info = resp.json()
        lines = [f"🚉 *{nombre}*"]
        for t in info.get("proximos", [])[:NUMERO_DE_TRENES_A_MOSTRAR]:
            lines.append(f"• En {t['minutos']} min → {t['direccion']}")
        texto = "\n".join(lines)
        btn = InlineKeyboardButton(f"❌ Eliminar {nombre}", callback_data=f"del:{pid}")
        await update.message.reply_text(texto, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup([[btn]]))

    await enviar_mensaje_menu(chat_id, context.bot, context.user_data)


async def estado_red_metro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra el estado de la línea en dos columnas:
    Izquierda  → Hacia Armilla (Albolote arriba → Armilla abajo)
    Derecha   → Hacia Albolote (Armilla arriba → Albolote abajo)
    Con 🚇 a la izquierda si el tren llega en < 3 min, o — si no hay tren próximo.
    Los minutos van entre paréntesis.
    """
    chat_id = update.effective_chat.id
    resp = requests.get(f"{API_BASE}/metro/llegadas")
    resp.raise_for_status()
    data = resp.json()

    # Mapear id → próximos
    lleg = {e["parada"]["id"]: e.get("proximos", []) for e in data}
    orden = list(paradas.keys())
    orden_rev = list(reversed(orden))

    def proximo(pid, direccion):
        for t in lleg.get(pid, []):
            if t["direccion"] == direccion:
                return t["minutos"]
        return None

    teclado = []
    for pid_a, pid_b in zip(orden, orden_rev):
        nombre_a = paradas.get(pid_a, pid_a)
        nombre_b = paradas.get(pid_b, pid_b)

        ma = proximo(pid_a, "Armilla")
        mb = proximo(pid_b, "Albolote")

        # Etiqueta columna izquierda (Hacia Armilla)
        if ma is None:
            text_a = f"{nombre_a} (—)"
        else:
            mins_a = f"({ma}m)"
            text_a = f"{'🚇 ' if ma < 3 else ''}{nombre_a} {mins_a}"

        # Etiqueta columna derecha (Hacia Albolote)
        if mb is None:
            text_b = f"{nombre_b} (—)"
        else:
            mins_b = f"({mb}m)"
            text_b = f"{'🚇 ' if mb < 3 else ''}{nombre_b} {mins_b}"

        btn_a = InlineKeyboardButton(text_a, callback_data=f"ver:{pid_a}")
        btn_b = InlineKeyboardButton(text_b, callback_data=f"ver:{pid_b}")
        teclado.append([btn_a, btn_b])

    header = (
        "🚆 *Estado de la línea*\n\n"
        "_Izquierda: Hacia Armilla_\n"
        "_Derecha:  Hacia Albolote_\n"
        "🚇  antes del nombre si < 3 min | (—) sin tren próximo"
    )
    await context.bot.send_message(chat_id, header, parse_mode="Markdown")
    await context.bot.send_message(
        chat_id,
        "Pulsa una parada para ver detalles:",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    await enviar_mensaje_menu(chat_id, context.bot, context.user_data)



async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra información del bot, autor y licencia."""
    texto = (
        "📄 *Información del bot*\n\n"
        "👤 Jorge Chamorro Padial\n"
        "📝 GNU GPL v3.0\n"
        "🙏 Gracias a Miguel Ángel Fernández por la API MovGR\n"
        "💻 https://github.com/jorgechp/grana-metro-bot"
    )
    await update.message.reply_text(texto, parse_mode="Markdown", disable_web_page_preview=True)
    await enviar_mensaje_menu(update.effective_chat.id, context.bot, context.user_data)


async def mensaje_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redirige según el texto pulsado en el menú persistente."""
    txt = update.message.text.lower()
    if "ver paradas" in txt:
        await start(update, context)
    elif "favoritas" in txt:
        await favoritas_cmd(update, context)
    elif "ver toda la línea" in txt:
        await estado_red_metro(update, context)
    elif "información" in txt or "info" in txt:
        await info_cmd(update, context)


if __name__ == "__main__":
    cargar_favoritos()
    cargar_paradas()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("favoritas", favoritas_cmd))
    app.add_handler(CommandHandler("linea", estado_red_metro))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensaje_menu))
    app.run_polling()
