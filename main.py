import sqlite3
import logging
import re
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)

TOKEN = "8734443938:AAGOzsrGAx3y-w6tzZay-RAtkSzhPcFzJ7k"
CAPACIDADE_NOMINAL = 30.2

conn = sqlite3.connect("ev_monitor.db", check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS blocos (
    id TEXT PRIMARY KEY, data_inicio TEXT, data_fim TEXT,
    km_inicial REAL, km_final REAL, km_total REAL,
    soc_inicial INTEGER, soc_final INTEGER, soc_gasto INTEGER,
    energia_kwh REAL, km_kwh REAL, kwh_100km REAL,
    status TEXT, local TEXT, tipo TEXT, topo INTEGER DEFAULT 0
)""")
conn.commit()

logging.basicConfig(level=logging.INFO)
INICIANDO, FECHANDO = range(2)

def bloco_aberto():
    c = conn.execute("SELECT * FROM blocos WHERE status='aberto' LIMIT 1")
    return c.fetchone()

def gerar_id():
    return f"BLK-{datetime.now().strftime('%Y%m%d%H%M%S')}"

async def start(update, context):
    await update.message.reply_text(
        "⚡ *EV Monitor — JAC E-JS1*\n\n"
        "1. `/novo_bloco` — Inicia\n"
        "2. Faz a viagem\n"
        "3. `/fechar` — Registra recarga\n\n"
        "📊 /historico\n❌ /cancelar",
        parse_mode="Markdown"
    )

async def novo_bloco(update, context):
    if bloco_aberto():
        await update.message.reply_text("Já tem bloco aberto. Use /fechar ou /cancelar.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "📸 Digite: `SOC: 95  Km: 84450`",
        parse_mode="Markdown"
    )
    return INICIANDO

async def receber_inicio(update, context):
    t = update.message.text
    n = [int(s) for s in t.replace(',','').split() if s.isdigit()]
    soc, km = None, None
    for l in t.lower().split('\n'):
        if 'soc' in l:
            soc = next((int(p) for p in l.split() if p.isdigit()), None)
        if 'km' in l:
            km = next((int(float(p)) for p in l.replace(',','').split()
                      if p.replace('.','').isdigit()), None)
    if not soc and not km and len(n) >= 2:
        soc, km = n[0], n[1]
    if not soc or not km:
        await update.message.reply_text("Use: `SOC: 95  Km: 84450`")
        return INICIANDO
    bid = gerar_id()
    conn.execute("INSERT INTO blocos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (bid, datetime.now().isoformat(), None, km, None, 0,
                  soc, None, 0, None, 0, 0, 'aberto', None, None, 0))
    conn.commit()
    context.user_data['bid'] = bid
    context.user_data['km'] = km
    context.user_data['soc'] = soc
    await update.message.reply_text(
        f"✅ Bloco *{bid}* — SOC {soc}% | Km {km}\n\n"
        "Faça a viagem. Depois /fechar.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def fechar(update, context):
    b = bloco_aberto()
    if not b:
        await update.message.reply_text("Nenhum bloco aberto.")
        return ConversationHandler.END
    context.user_data['bid_f'] = b[0]
    context.user_data['km_i'] = b[3]
    context.user_data['soc_i'] = b[6]
    context.user_data['dados'] = {}
    await update.message.reply_text(
        "🔌 Digite os dados da recarga:\n"
        "`SOC inicio: 32  SOC fim: 100  kWh: 18.61  Local: Taguá`",
        parse_mode="Markdown"
    )
    return FECHANDO

async def receber_recarga(update, context):
    t = update.message.text
    d = context.user_data.get('dados', {})
    for l in t.lower().split('\n'):
        if 'soc' in l and 'inicio' in l:
            d['soc_ini'] = int(re.search(r'\d+', l).group())
        elif 'soc' in l and 'fim' in l:
            d['soc_fim'] = int(re.search(r'\d+', l).group())
        elif 'kwh' in l:
            for p in l.split():
                try: d['kwh'] = float(p.replace(',','.')); break
                except: pass
        elif 'local' in l and ':' in l:
            d['local'] = l.split(':')[1].strip()
    context.user_data['dados'] = d

    if not d.get('soc_ini') or not d.get('soc_fim') or not d.get('kwh'):
        await update.message.reply_text(
            "Falta dados. Use: `SOC inicio: 32  SOC fim: 100  kWh: 18.61`")
        return FECHANDO

    bid = context.user_data['bid_f']
    km_i = context.user_data['km_i']
    soc_i = context.user_data['soc_i']
    soc_ir = d['soc_ini']
    soc_fr = d['soc_fim']
    kwh = d['kwh']
    km_t = km_i - km_i
    c = conn.execute("SELECT km_inicial FROM blocos WHERE id=?", (bid,)).fetchone()
    km_t = km_i - c[0] if c else 0
    soc_g = soc_i - soc_ir
    km_k = round(km_t/kwh, 2) if kwh > 0 else 0
    kwh_100 = round((kwh/km_t)*100, 2) if km_t > 0 else 0
    topo = 1 if soc_fr >= 95 else 0

    conn.execute("""UPDATE blocos SET data_fim=?, km_final=?, km_total=?,
        soc_final=?, soc_gasto=?, energia_kwh=?, km_kwh=?, kwh_100km=?,
        status='fechado', local=?, topo=? WHERE id=?""",
        (datetime.now().isoformat(), km_i, km_t, soc_ir, soc_g,
         kwh, km_k, kwh_100, d.get('local'), topo, bid))
    conn.commit()

    soc_rec = soc_fr - soc_ir
    eru = round((kwh/CAPACIDADE_NOMINAL)*100, 1)
    desv = round(soc_rec - eru, 1)
    if abs(desv) <= 2: cls = 'coerente'
    elif abs(desv) <= 4: cls = 'aceitavel'
    elif abs(desv) <= 6: cls = 'atencao'
    else: cls = 'suspeita'

    await update.message.reply_text(
        f"✅ *Bloco encerrado!*\n\n"
        f"📏 Km: *{km_t}* | SOC {soc_i}% → {soc_ir}%\n"
        f"🔋 Recarga: {soc_ir}% → {soc_fr}%\n"
        f"⚡ kWh: *{kwh}* | 📊 *{km_k} km/kWh*\n"
        f"🔬 ERU: {eru}% | Desvio: {desv:+.1f} pp | *{cls}*",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def historico(update, context):
    c = conn.execute("SELECT * FROM blocos ORDER BY data_inicio DESC LIMIT 10")
    r = c.fetchall()
    if not r:
        await update.message.reply_text("Nenhum bloco ainda.")
        return
    msg = "📋 *Últimos blocos:*\n\n"
    for b in r:
        s = "✅" if b[12]=='fechado' else "🔄"
        d = b[1][:10] if b[1] else '?'
        km = b[5] or '—'
        e = f" | {b[9]} km/kWh" if b[9] else ''
        msg += f"{s} *{b[0]}* — {d} | {km} km{e}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cancelar(update, context):
    b = bloco_aberto()
    if b:
        conn.execute("UPDATE blocos SET status='cancelado' WHERE id=?", (b[0],))
        conn.commit()
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelado.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(TOKEN).build()
    cv = ConversationHandler(
        entry_points=[CommandHandler('novo_bloco', novo_bloco)],
        states={
            INICIANDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_inicio)],
            FECHANDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_recarga)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)],
    )
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('historico', historico))
    app.add_handler(cv)
    print("⚡ EV Monitor rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
