@echo off
title Lanzador RENAVEN
:: Limpiar procesos anteriores
taskkill /f /im Sistema_Renaven.exe 2>nul

:: Iniciar servicios XAMPP
start "" /min C:\xampp\mysql_start.bat
timeout /t 5 /nobreak > nul

:: Lanzar el sistema
cd /d C:\Sistema_Renaven\
start "" Sistema_Renaven.exe

:: Abrir navegador
start http://renaven.local
exit