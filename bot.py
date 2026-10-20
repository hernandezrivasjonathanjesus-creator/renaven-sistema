import os
import time
import requests
import threading
from flask import Flask

app = Flask(__name__)

TELEGRAM_TOKEN = "8734974003:AAGbAcQusTuUKq8yj63nky8jX4BKEudsvKo"
TELEGRAM_CHAT_ID = "1623792128"

def escuchar_telegram():
    offset = None
    print("[OK] Bot de Telegram escuchando comandos...", flush=True)
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
                        if texto in ['/start', '/ayuda']:
                            payload = {"chat_id": chat_id, "text": "🤖 <b>BOT RENAVEN ACTIVO</b>\n¡Ya estoy conectado!", "parse_mode": "HTML"}
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=payload)
            else:
                time.sleep(2)
        except Exception:
            time.sleep(5)

threading.Thread(target=escuchar_telegram, daemon=True).start()

@app.route('/')
def home():
    return "Bot encendido"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
