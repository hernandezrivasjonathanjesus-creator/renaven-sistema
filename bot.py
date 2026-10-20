import os
import time
import requests
import threading
from flask import Flask

app = Flask(__name__)

# EL TOKEN NUEVO QUE TE DIO BOTFATHER
TELEGRAM_TOKEN = "8734974003:AAGtM8_fFoSrHqFZUeRakLbkFbK5AVGHmY4"
TELEGRAM_CHAT_ID = "1623792128"

def escuchar_telegram():
    offset = None
    print("[OK] Bot de Telegram escuchando comandos desde archivo aparte...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            response = requests.get(url, params={"timeout": 30, "offset": offset}, timeout=35)
            data = response.json()
            if data.get('ok'):
                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    msg = update.get('message', {})
                    chat_id = str(msg.get('chat', {}).get('id', ''))
                    texto = msg.get('text', '').strip()
                    
                    if chat_id == TELEGRAM_CHAT_ID:
                        if texto == '/ayuda' or texto == '/start':
                            payload = {"chat_id": chat_id, "text": "🤖 <b>BOT RENAVEN ACTIVO</b>\n¡Ya estoy conectado! Escribe /inventario o /estado.", "parse_mode": "HTML"}
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=payload)
                        elif texto == '/inventario':
                            payload = {"chat_id": chat_id, "text": "📦 <b>INVENTARIO</b>\n(El inventario se conectará aquí pronto).", "parse_mode": "HTML"}
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=payload)
        except Exception as e:
            time.sleep(5)

# Arrancar el bot en segundo plano
threading.Thread(target=escuchar_telegram, daemon=True).start()

@app.route('/')
def home():
    return "Bot de Telegram corriendo perfectamente."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
