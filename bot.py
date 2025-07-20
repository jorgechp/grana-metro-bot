"""
Bot de Telegram para consultar horarios en tiempo real del Metro de Granada,
gestionar favoritas y alertas de llegada con selección de umbral y dirección,
con notificaciones automáticas y visualización de la situación de los metros.

Licencia: GNU General Public License v3.0
Autor: Jorge Chamorro Padial
"""

import os
import json
import requests
import logging

from dotenv import load_dotenv
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

# ── Configuración básica ─────────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN no definido en .env")

API_BASE       = "https://movgr.apis.mianfg.me"
FAVORITOS_FILE = "favoritos.json"
ALERTAS_FILE   = "alertas.json"
NUM_TRENES     = 4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Datos en memoria ──────────────────────────────────────────────────────────
favoritos = {}  # user_id (int) → set(parada_id:str)
paradas    = {}  # parada_id (str) → nombre (str)
# alertas: user_id (str) → list of [parada_id (str), umbral (int), direccion (str)]
alertas    = {}

# ── Teclado persistente (reply keyboard) ────────────────────────────────────
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🔍 Ver paradas"), KeyboardButton("⭐ Favoritas")],
        [KeyboardButton("🚆 Situación de los metros"), KeyboardButton("📄 Información")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

# ── Persistencia JSON ─────────────────────────────────────────────────────────
def cargar_json(ruta, por_defecto):
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return por_defecto

def guardar_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)

def cargar_favoritos():
    global favoritos
    tmp = cargar_json(FAVORITOS_FILE, {})
    favoritos = {int(k): set(v) for k, v in tmp.items()}

def guardar_favoritos():
    tmp = {str(k): list(v) for k, v in favoritos.items()}
    guardar_json(FAVORITOS_FILE, tmp)

def cargar_alertas():
    global alertas
    alertas = cargar_json(ALERTAS_FILE, {})

def guardar_alertas():
    guardar_json(ALERTAS_FILE, alertas)

# ── Carga de paradas desde la API ────────────────────────────────────────────
def cargar_paradas():
    global paradas
    resp = requests.get(f"{API_BASE}/metro/paradas")
    resp.raise_for_status()
    datos = resp.json()
    paradas = {p["id"]: p["nombre"] for p in datos}

# ── Envío/actualización del menú persistente ─────────────────────────────────
async def enviar_mensaje_menu(obj, context: ContextTypes.DEFAULT_TYPE):
    """
    Envía o actualiza el menú principal en la parte inferior.
    obj puede ser Update o CallbackQuery.
    """
    # Extraer chat_id
    if hasattr(obj, "effective_chat") and obj.effective_chat:
        chat_id = obj.effective_chat.id
    else:
        # CallbackQuery
        chat_id = obj.message.chat.id

    old = context.user_data.get("menu_msg_id")
    if old:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old)
        except:
            pass

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text="¿Qué deseas hacer?",
        reply_markup=MAIN_MENU
    )
    context.user_data["menu_msg_id"] = sent.message_id

# ── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start o '🔍 Ver paradas': muestra la lista de paradas.
    """
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
    await enviar_mensaje_menu(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa callbacks inline: ver llegada, toggle favorito, eliminar favorito,
    iniciar alerta, seleccionar umbral y dirección.
    """
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split(":")

    action = parts[0]
    if action == "ver":
        return await ver_parada_detalle(query, context, parts[1])
    if action == "toggle":
        return await toggle_fav_callback(query, context, parts[1])
    if action == "del":
        return await del_fav_callback(query, context, parts[1])
    if action == "setalert":
        pid = parts[1]
        teclado = [[
            InlineKeyboardButton("2 min", callback_data=f"alertthr:{pid}:2"),
            InlineKeyboardButton("5 min", callback_data=f"alertthr:{pid}:5"),
            InlineKeyboardButton("8 min", callback_data=f"alertthr:{pid}:8"),
        ]]
        await query.edit_message_text(
            f"⏰ Elige umbral para alerta en *{paradas.get(pid)}*:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(teclado)
        )
        return
    if action == "alertthr":
        pid, umbral = parts[1], parts[2]
        teclado = [[
            InlineKeyboardButton("Hacia Armilla", callback_data=f"alertdir:{pid}:{umbral}:Armilla"),
            InlineKeyboardButton("Hacia Albolote", callback_data=f"alertdir:{pid}:{umbral}:Albolote"),
        ]]
        await query.edit_message_text(
            f"🚩 Elige sentido para alerta de *{paradas.get(pid)}* ≤{umbral} min:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(teclado)
        )
        return
    if action == "alertdir":
        pid, umbral, direccion = parts[1], parts[2], parts[3]
        alertas.setdefault(str(user_id), []).append([pid, int(umbral), direccion])
        guardar_alertas()
        await query.edit_message_text(
            f"✅ Alerta creada: *{paradas.get(pid)}* → *{direccion}* en ≤{umbral} min.",
            parse_mode="Markdown"
        )
        await enviar_mensaje_menu(query, context)
        return

# ── Funciones de parada ────────────────────────────────────────────────────────
async def ver_parada_detalle(q, context, pid: str):
    """
    Muestra próximos trenes e incluye botones Favorita y Alerta.
    """
    nombre = paradas.get(pid, pid)
    r = requests.get(f"{API_BASE}/metro/llegadas/{pid}")
    if r.status_code != 200:
        texto = f"❌ Error al consultar {nombre}."
    else:
        info = r.json()
        if not info.get("proximos"):
            texto = f"🚉 *{nombre}*\n_No hay trenes próximos._"
        else:
            lines = [f"🚉 *{nombre}*"]
            for t in info["proximos"][:NUM_TRENES]:
                lines.append(f"• En {t['minutos']} min → {t['direccion']}")
            texto = "\n".join(lines)

    fav = pid in favoritos.get(q.from_user.id, set())
    btn_fav   = InlineKeyboardButton("⭐ Quitar favorita" if fav else "➕ Añadir favorita",
                                     callback_data=f"toggle:{pid}")
    btn_alert = InlineKeyboardButton("⏰ Alerta", callback_data=f"setalert:{pid}")

    await q.edit_message_text(
        texto + ("\n⭐ Favorita" if fav else ""),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[btn_fav, btn_alert]])
    )
    await enviar_mensaje_menu(q, context)

async def toggle_fav_callback(q, context, pid: str):
    """Añade o quita una parada de favoritas."""
    user = q.from_user.id
    favs = favoritos.setdefault(user, set())
    if pid in favs:
        favs.remove(pid)
        msg = "❌ Parada eliminada de favoritas."
    else:
        if len(favs) >= 5:
            msg = "⚠️ Límite de 5 favoritas alcanzado."
        else:
            favs.add(pid)
            msg = "✅ Parada añadida a favoritas."
    guardar_favoritos()
    await q.edit_message_text(msg, reply_markup=MAIN_MENU)

async def del_fav_callback(q, context, pid: str):
    """Elimina una parada de favoritas."""
    user = q.from_user.id
    favoritos.get(user, set()).discard(pid)
    guardar_favoritos()
    await q.edit_message_text("❌ Favorita eliminada.", reply_markup=MAIN_MENU)

async def favoritas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra las paradas favoritas."""
    user = update.effective_user.id
    favs = favoritos.get(user, set())
    if not favs:
        await update.message.reply_text("No tienes favoritas aún.", reply_markup=MAIN_MENU)
        return
    for pid in favs:
        nombre = paradas.get(pid, pid)
        r = requests.get(f"{API_BASE}/metro/llegadas/{pid}")
        if r.status_code != 200:
            await update.message.reply_text(f"{nombre}: ⚠️ Error", reply_markup=MAIN_MENU)
        else:
            info = r.json()
            lines = [f"🚉 *{nombre}*"]
            for t in info.get("proximos", [])[:2]:
                lines.append(f"• En {t['minutos']} min → {t['direccion']}")
            btn = InlineKeyboardButton(f"❌ Eliminar {nombre}", callback_data=f"del:{pid}")
            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[btn]])
            )
    await enviar_mensaje_menu(update, context)

# ── Situación de los metros ───────────────────────────────────────────────────
async def estado_textual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra la situación de los metros en dos columnas:
    🚇 si tren < 3 min, solo minutos si ≥ 3 min, — si no hay tren.
    """
    chat_id = update.effective_chat.id
    resp = requests.get(f"{API_BASE}/metro/llegadas")
    resp.raise_for_status()
    datos = resp.json()
    lleg = {e["parada"]["id"]: e.get("proximos", []) for e in datos}
    orden = list(paradas.keys())

    teclado = []
    for pid in orden:
        nombre = paradas.get(pid, pid)
        prox = lleg.get(pid, [])
        t_arm = next((t["minutos"] for t in prox if t["direccion"] == "Armilla"), None)
        t_alb = next((t["minutos"] for t in prox if t["direccion"] == "Albolote"), None)
        txt_a = f"{nombre} {'🚇' if t_arm is not None and t_arm < 3 else ''}{t_arm or '—'}m"
        txt_b = f"{nombre} {'🚇' if t_alb is not None and t_alb < 3 else ''}{t_alb or '—'}m"
        teclado.append([
            InlineKeyboardButton(txt_a, callback_data=f"ver:{pid}"),
            InlineKeyboardButton(txt_b, callback_data=f"ver:{pid}")
        ])

    await context.bot.send_message(
        chat_id,
        "🚆 *Situación de los metros*\n"
        "_Izquierda: Hacia Armilla_  |  _Derecha: Hacia Albolote_\n",
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id,
        "Pulsa una parada para ver detalles:",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    await enviar_mensaje_menu(update, context)

# ── Info ─────────────────────────────────────────────────────────────────────
async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra información del bot."""
    await update.message.reply_text(
        "📄 *Información del bot*\n\n"
        "👤 Jorge Chamorro Padial\n"
        "📝 GNU GPL v3.0\n"
        "🙏 Gracias a la API MovGR\n"
        "💻 https://github.com/jorgechp/grana-metro-bot",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=MAIN_MENU
    )
    await enviar_mensaje_menu(update, context)

# ── JobQueue: comprobación de alertas (se dispara una sola vez) ─────────────
async def check_alertas(context: ContextTypes.DEFAULT_TYPE):
    mods = False
    nuevos = {}
    for user, subs in alertas.items():
        viva = []
        for pid, um, dirc in subs:
            try:
                r = requests.get(f"{API_BASE}/metro/llegadas/{pid}")
                r.raise_for_status()
                prox = r.json().get("proximos", [])
                if prox and prox[0]["direccion"] == dirc and prox[0]["minutos"] <= um:
                    await context.bot.send_message(
                        chat_id=int(user),
                        text=(
                            f"🚨 *Alerta*: {paradas.get(pid)} → {dirc} en "
                            f"{prox[0]['minutos']} min)."
                        ),
                        parse_mode="Markdown"
                    )
                    mods = True
                    continue
            except:
                pass
            viva.append([pid, um, dirc])
        if viva:
            nuevos[user] = viva
        else:
            mods = True
    if mods:
        alertas.clear()
        alertas.update(nuevos)
        guardar_alertas()

# ── Manejo de texto del menú persistente ────────────────────────────────────
async def mensaje_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt == "🔍 Ver paradas":
        await start(update, context)
    elif txt == "⭐ Favoritas":
        await favoritas_cmd(update, context)
    elif txt == "🚆 Situación de los metros":
        await estado_textual(update, context)
    elif txt == "📄 Información":
        await info_cmd(update, context)

# ── Arranque del bot ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    cargar_favoritos()
    cargar_paradas()
    cargar_alertas()

    app = ApplicationBuilder().token(TOKEN).build()

    # Comprobación de alertas cada 30 segundos, primer disparo inmediato
    app.job_queue.run_repeating(check_alertas, interval=30, first=0)

    # Registramos handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensaje_menu))

    logger.info("Bot arrancado. Esperando eventos…")
    app.run_polling()
