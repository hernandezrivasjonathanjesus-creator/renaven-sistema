@echo off
cd /d "C:\Users\Jonathan\Desktop\proyecto 22-05"
echo Iniciando el servidor Python...
start cmd /k python app.py

echo Esperando 5 segundos para que Flask arranque bien...
timeout /t 5 /nobreak >nul

cd /d "C:\Users\Jonathan\Downloads"
echo Iniciando Cloudflare Tunnel...
start "Cloudflare Tunnel" cloudflared.exe tunnel --url http://localhost:5000

echo =======================================================
echo  Todo iniciado. Revisa la ventana de Cloudflare.
echo  Busca la linea que dice: https://....trycloudflare.com
echo  Copiala y pegala en tu telefono!
echo =======================================================
pause