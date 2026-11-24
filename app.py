from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response, g, send_file
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail, Message
from flask_compress import Compress
from werkzeug.middleware.proxy_fix import ProxyFix
import psycopg2
import psycopg2.extras
from psycopg2 import pool, Error
import bcrypt
import os
import sys
import time
import secrets
import random
import string
import io
from datetime import datetime, timedelta
from functools import wraps
import json
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import logging
from logging.handlers import RotatingFileHandler
from collections import defaultdict
import platform
import psutil
import re
import threading
import requests
from bs4 import BeautifulSoup
import subprocess
import shutil
import socket

# ==================== CONFIGURACIÓN DE .ENV ====================
from dotenv import load_dotenv
load_dotenv()

_db_initialized = False
_telegram_bot_iniciado = False

# ==================== CONFIGURACIÓN DE TELEGRAM ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_CODIGO_REINICIO = os.getenv("TELEGRAM_CODIGO_REINICIO")
RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# ==================== VALIDACIÓN DE CONTRASEÑAS ====================
def validar_contraseña_segura(password):
    errores = []
    if len(password) < 16:
        errores.append("La contraseña debe tener mínimo 16 caracteres")
    if not re.search(r'[A-Z]', password):
        errores.append("Debe contener al menos una letra MAYÚSCULA (A-Z)")
    if not re.search(r'[a-z]', password):
        errores.append("Debe contener al menos una letra minúscula (a-z)")
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/]', password):
        errores.append("Debe contener al menos un carácter especial (!@#$%^&*()_+-=[]{}|;:,.<>?/)")
    return errores

def generar_contraseña_segura(longitud=16):
    mayusculas = string.ascii_uppercase
    minusculas = string.ascii_lowercase
    especiales = "!@#$%^&*()_+-=[]{}|;:,.<>?/"
    contraseña = [
        random.choice(mayusculas),
        random.choice(minusculas),
        random.choice(especiales),
        random.choice(string.digits)
    ]
    todos = mayusculas + minusculas + especiales + string.digits
    contraseña += random.choices(todos, k=longitud-4)
    random.shuffle(contraseña)
    return ''.join(contraseña)

# ==================== CONFIGURACIÓN DE LOGGING ====================
class JsonFormatter(logging.Formatter):
    def format(self, record):
        path = getattr(record, 'pathname', '').replace('\\', '/')
        log_entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "path": f"{path}:{record.lineno}",
            "message": record.getMessage()
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ==================== RUTAS PARA .EXE (PYINSTALLER) ====================
def resource_path(relative_path):
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)

if getattr(sys, 'frozen', False):
    base_dir    = os.path.dirname(sys.executable)
    bundle_dir  = sys._MEIPASS
else:
    base_dir    = os.path.dirname(os.path.abspath(__file__))
    bundle_dir  = base_dir

log_path_file = os.path.join(base_dir, 'renaven.log')
json_formatter = JsonFormatter()
try:
    file_handler = RotatingFileHandler(log_path_file, maxBytes=10485760, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(json_formatter)
    logger_handlers = [file_handler]
except Exception:
    logger_handlers = []

if sys.stdout is not None:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(json_formatter)
    logger_handlers.append(console_handler)

logger = logging.getLogger(__name__)
for h in logger_handlers:
    logger.addHandler(h)
logger.setLevel(logging.INFO)

template_folder = resource_path('templates')
static_folder   = resource_path('static')

app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
app.secret_key = 'renaven-super-secret-key-2024'

# ==================== PROXY FIX PARA RENDER (EVITA REDIRECT LOOP) ====================
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# ===================================================================================

# ==================== CONTEXT PROCESSORS ====================
@app.context_processor
def utility_processor():
    meses_nombres = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    return dict(meses_nombres=meses_nombres, now=datetime.now())

@app.context_processor
def caja_context():
    try:
        opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
        opening_balance_cero = float(opening_result['value']) if opening_result else 0.0
        return {'opening_balance_cero': opening_balance_cero}
    except:
        return {'opening_balance_cero': 0}

# ==================== COMPRESIÓN GZIP ====================
Compress(app)

# ==================== CONFIGURACIÓN DE POSTGRESQL (CONEXIÓN DIRECTA) ====================
DATABASE_URL = os.environ.get('DATABASE_URL')

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'connect_timeout': 30,
    'sslmode': 'disable',
}

# Ya no usamos un pool global. Cada consulta abre y cierra su propia conexión.
connection_pool = None

def ensure_database_exists():
    pass

def init_connection_pool():
    # Esta función ya no es necesaria, pero la dejamos por compatibilidad
    global connection_pool
    connection_pool = True
    logger.info("[OK] Modo de conexión directa (sin pool) activado")
    print("[OK] Modo de conexión directa (sin pool) activado")

def _mysql_to_pg(query):
    query = query.replace('`', '')
    if re.match(r'^\s*SHOW\s+TABLES\s*$', query, re.IGNORECASE):
        return "SELECT tablename AS \"Tables_in_db\" FROM pg_tables WHERE schemaname = 'public'"
    m = re.match(r'^\s*SHOW\s+COLUMNS\s+FROM\s+(\w+)(?:\s+LIKE\s+\'([^\']+)\')?\s*$', query, re.IGNORECASE)
    if m:
        table = m.group(1)
        col = m.group(2)
        if col:
            return "SELECT column_name AS \"Field\" FROM information_schema.columns WHERE table_name = '" + table + "' AND column_name = '" + col + "'"
        return "SELECT column_name AS \"Field\" FROM information_schema.columns WHERE table_name = '" + table + "'"
    if re.match(r'^\s*CREATE\s+TABLE', query, re.IGNORECASE):
        lineas = query.split('\n')
        nuevas = []
        for linea in lineas:
            stripped = linea.strip().rstrip(',')
            if re.match(r'^(INDEX|KEY|UNIQUE\s+KEY|FULLTEXT|SPATIAL)\s', stripped, re.IGNORECASE):
                continue
            nuevas.append(linea)
        query = '\n'.join(nuevas)
        query = re.sub(r',(\s*\))', r'\1', query)
    query = re.sub(r'\bINT\s+PRIMARY\s+KEY\s+AUTO_INCREMENT\b', 'SERIAL PRIMARY KEY', query, flags=re.IGNORECASE)
    query = re.sub(r'\bAUTO_INCREMENT\b', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\bTINYINT\b', 'SMALLINT', query, flags=re.IGNORECASE)
    query = re.sub(r'ENUM\s*\([^)]+\)', 'VARCHAR(50)', query, flags=re.IGNORECASE)
    query = re.sub(r'\bENGINE\s*=\s*\w+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\bDEFAULT\s+CHARSET\s*=\s*\w+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\bCOLLATE\s*=\s*\w+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\bON\s+UPDATE\s+CURRENT_TIMESTAMP\b', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\bDATETIME\b', 'TIMESTAMP', query, flags=re.IGNORECASE)
    if re.search(r'\bINSERT\s+IGNORE\b', query, re.IGNORECASE):
        query = re.sub(r'\bINSERT\s+IGNORE\b', 'INSERT', query, flags=re.IGNORECASE)
        if 'ON CONFLICT' not in query.upper():
            query = query.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    if re.search(r'ON\s+DUPLICATE\s+KEY\s+UPDATE', query, re.IGNORECASE):
        query = re.sub(r'ON\s+DUPLICATE\s+KEY\s+UPDATE\s+\w+\s*=\s*[\w\d]+', 'ON CONFLICT DO NOTHING', query, flags=re.IGNORECASE)
    return query

def get_db():
    # Abre una conexión NUEVA cada vez. Sin pool, sin herencia, sin SSL corrupto.
    try:
        if DATABASE_URL:
            db_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            if '?' not in db_url:
                db_url += '?sslmode=disable'
            elif 'sslmode' not in db_url:
                db_url += '&sslmode=disable'
            conn = psycopg2.connect(dsn=db_url)
        else:
            conn = psycopg2.connect(**DB_CONFIG)
        
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _orig = cursor.execute
        def _patched(q, p=None):
            return _orig(_mysql_to_pg(q), p)
        cursor.execute = _patched
        return conn, cursor
    except Exception as e:
        logger.error(f"Error de conexion PostgreSQL: {e}")
        raise

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    conn = None
    cursor = None
    try:
        conn, cursor = get_db()
        cursor.execute(query, params or ())
        if commit:
            conn.commit()
            return cursor.rowcount
        elif fetch_one:
            return cursor.fetchone()
        elif fetch_all:
            result = cursor.fetchall()
            return result if result is not None else []
        else:
            try:
                cursor.fetchall()
            except Exception:
                pass
            return True
    except Error as e:
        logger.error(f"Error en query: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        if fetch_all:
            return []
        raise
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()  # Cierra la conexión directamente
            except Exception:
                pass

# ==================== CACHÉ DE CONSULTAS ====================
_cache_exchange_rate = {'rate': None, 'timestamp': 0}
_cache_discount = {'percent': None, 'timestamp': 0}
CACHE_TTL = 60

def get_cached_exchange_rate():
    now = time.time()
    if _cache_exchange_rate['timestamp'] + CACHE_TTL < now:
        result = execute_query("SELECT value FROM config WHERE key_name = 'exchange_rate'", fetch_one=True)
        _cache_exchange_rate['rate'] = float(result['value']) if result else 36.50
        _cache_exchange_rate['timestamp'] = now
        logger.info(f"Cache actualizado: tasa = {_cache_exchange_rate['rate']}")
    return _cache_exchange_rate['rate']

def get_cached_discount():
    now = time.time()
    if _cache_discount['timestamp'] + CACHE_TTL < now:
        result = execute_query("SELECT value FROM config WHERE key_name = 'discount_percent'", fetch_one=True)
        _cache_discount['percent'] = float(result['value']) if result else 5.0
        _cache_discount['timestamp'] = now
    return _cache_discount['percent']

# ==================== RATE LIMITING ====================
_login_attempts = defaultdict(list)

def check_rate_limit(ip):
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < 300]
    if len(_login_attempts[ip]) >= 10:
        return False
    return True

def record_login_attempt(ip):
    _login_attempts[ip].append(time.time())

# ==================== TASA BCV EN TIEMPO REAL ====================
_bcv_rate_cache = {'rate': None, 'timestamp': 0, 'date': None}
BCV_CACHE_TTL = 21600

def fetch_bcv_rate_from_web():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        response = requests.get('https://www.bcv.org.ve/', headers=headers, timeout=20, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        texto_completo = soup.get_text()
        patrones = [
            r'USD\s*\n?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+))',
            r'Dólar\s*\n?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+))',
            r'Tipo de Cambio.*?USD.*?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+))',
            r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{4,}))\s*(?:Bs|Bolívares)',
        ]
        tasa_encontrada = None
        for patron in patrones:
            match = re.search(patron, texto_completo, re.IGNORECASE | re.DOTALL)
            if match:
                tasa_str = match.group(1).replace('.', '').replace(',', '.')
                try:
                    tasa_encontrada = float(tasa_str)
                    if tasa_encontrada > 0 and tasa_encontrada < 1000000:
                        break
                except:
                    continue
        fecha_valor = None
        fecha_patron = r'Fecha Valor:?\s*([A-Za-zéúíóáñü]+,\s*\d{1,2}\s+de\s+[A-Za-zéúíóáñü]+\s+de\s+\d{4})'
        fecha_match = re.search(fecha_patron, texto_completo, re.IGNORECASE)
        if fecha_match:
            fecha_valor = fecha_match.group(1)
        if tasa_encontrada:
            logger.info(f"[OK] Tasa BCV obtenida: {tasa_encontrada} Bs/USD - Fecha: {fecha_valor}")
            return tasa_encontrada, fecha_valor
        else:
            logger.warning("[WARN] No se pudo encontrar la tasa USD en el HTML del BCV")
            return None, None
    except requests.exceptions.Timeout:
        logger.error("[ERROR] Timeout al consultar BCV")
        return None, None
    except requests.exceptions.RequestException as e:
        logger.error(f"[ERROR] Error de conexion al BCV: {e}")
        return None, None
    except Exception as e:
        logger.error(f"[ERROR] Error inesperado al obtener tasa BCV: {e}")
        return None, None

def update_bcv_rate():
    global _bcv_rate_cache
    ahora = time.time()
    if _bcv_rate_cache['rate'] and (ahora - _bcv_rate_cache['timestamp'] < BCV_CACHE_TTL):
        logger.info(f"[CACHE] Usando tasa BCV en caché: {_bcv_rate_cache['rate']} Bs/USD")
        return True
    tasa, fecha = fetch_bcv_rate_from_web()
    if tasa and tasa > 0:
        tasa_anterior_result = execute_query("SELECT value FROM config WHERE key_name = 'exchange_rate'", fetch_one=True)
        tasa_anterior = float(tasa_anterior_result['value']) if tasa_anterior_result else 0
        execute_query("UPDATE config SET value = %s WHERE key_name = 'exchange_rate'", (str(tasa),), commit=True)
        global _cache_exchange_rate
        _cache_exchange_rate = {'rate': None, 'timestamp': 0}
        if abs(tasa - tasa_anterior) > 0.01:
            execute_query(
                """INSERT INTO exchange_rate_history
                (tasa_anterior, tasa_nueva, descuento, usuario, fecha)
                VALUES (%s, %s, %s, %s, %s)""",
                (tasa_anterior, tasa, 0, 'SISTEMA_BCV', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                commit=True
            )
            logger.info(f"[UPDATE] Tasa BCV actualizada: {tasa_anterior} -> {tasa} Bs/USD")
        _bcv_rate_cache = {'rate': tasa, 'timestamp': ahora, 'date': fecha}
        return True
    else:
        logger.warning("[WARN] No se pudo actualizar la tasa desde el BCV, manteniendo tasa actual")
        return False

# ==================== FUNCIONES CAPTCHA ====================
def generar_codigo_captcha(longitud=5):
    caracteres = string.ascii_uppercase + string.digits
    caracteres = caracteres.replace('0', '').replace('O', '').replace('1', '').replace('I', '')
    return ''.join(random.choices(caracteres, k=longitud))

def crear_imagen_captcha(codigo):
    ancho = 220
    alto = 70
    imagen = Image.new('RGB', (ancho, alto), color=(255, 255, 255))
    draw = ImageDraw.Draw(imagen)
    try:
        fuente = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
    except:
        try:
            fuente = ImageFont.truetype("arialbd.ttf", 42)
        except:
            fuente = ImageFont.load_default()
    colores_texto = [
        (0, 0, 0),
        (10, 40, 100),
        (139, 0, 0),
        (0, 100, 0),
        (75, 0, 130)
    ]
    longitud_texto = len(codigo)
    espacio_por_letra = (ancho - 50) // longitud_texto
    for i, char in enumerate(codigo):
        x = 25 + (i * espacio_por_letra) + random.randint(-5, 5)
        y = random.randint(10, 20)
        color = random.choice(colores_texto)
        draw.text((x, y), char, font=fuente, fill=color)
    for _ in range(random.randint(2, 4)):
        x1 = random.randint(0, ancho)
        y1 = random.randint(0, alto)
        x2 = random.randint(0, ancho)
        y2 = random.randint(0, alto)
        draw.line([(x1, y1), (x2, y2)], fill=(180, 180, 180), width=2)
    for _ in range(random.randint(20, 40)):
        x = random.randint(0, ancho)
        y = random.randint(0, alto)
        draw.point((x, y), fill=(160, 160, 160))
    return imagen

# ==================== FILTROS PERSONALIZADOS ====================
@app.template_filter('format_bs')
def format_bs(value):
    if value is None:
        return "0,00"
    try:
        num = float(value)
        partes = f"{num:,.2f}".split('.')
        entero = partes[0].replace(',', '.')
        return f"{entero},{partes[1]}"
    except (ValueError, TypeError):
        return "0,00"

@app.template_filter('format_usd')
def format_usd(value):
    if value is None:
        return "0.00"
    try:
        num = float(value)
        return f"{num:,.2f}"
    except (ValueError, TypeError):
        return "0.00"

@app.template_filter('format_usd_venezuela')
def format_usd_venezuela(value):
    if value is None:
        return "0,00"
    try:
        num = float(value)
        partes = f"{num:,.2f}".split('.')
        entero = partes[0].replace(',', '.')
        return f"{entero},{partes[1]}"
    except (ValueError, TypeError):
        return "0,00"

@app.template_filter('format_number')
def format_number(value):
    if value is None:
        return "0"
    try:
        num = float(value)
        if num == int(num):
            return f"{int(num):,}".replace(",", ".")
        else:
            return f"{num:,.2f}".replace(",", ".")
    except (ValueError, TypeError):
        return "0"

@app.template_filter('datetimeformat')
def datetimeformat(value, format='%d/%m/%Y %H:%M:%S'):
    if value is None:
        value = datetime.now()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.strftime(format)
    return value

def parse_venezuela_number(valor):
    if not valor:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        limpio = str(valor).replace('.', '').replace(',', '.')
        return float(limpio)
    except:
        return 0.0

# ==================== CONFIGURACIÓN DE CORREO Y COOKIES ====================
app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=465,               
    MAIL_USE_SSL=True,            
    MAIL_USE_TLS=False,           
    MAIL_USERNAME=os.getenv('MAIL_USERNAME'),
    MAIL_PASSWORD=os.getenv('MAIL_PASSWORD'),
    MAIL_DEFAULT_SENDER=os.getenv('MAIL_DEFAULT_SENDER'),
    
    # --- COOKIES SEGURAS (AHORA CON PROXYFIX FUNCIONAN BIEN) ---
    SESSION_COOKIE_SECURE=True, 
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=True, 
    REMEMBER_COOKIE_HTTPONLY=True,
    # -----------------------------------
    
    WTF_CSRF_ENABLED=True,
    WTF_CSRF_TIME_LIMIT=3600,
    COMPRESS_MIMETYPES=['text/html', 'text/css', 'text/javascript', 'application/json', 'application/javascript'],
    COMPRESS_LEVEL=6,
    COMPRESS_MIN_SIZE=500,
)

csrf = CSRFProtect()
csrf.init_app(app)
mail = Mail(app)

# ==================== RUTAS API DE CSRF ====================
@csrf.exempt
@app.route('/add_to_cart', methods=['POST'])
def add_to_cart_exempt():
    return add_to_cart()

@csrf.exempt
@app.route('/get_cart')
def get_cart_exempt():
    return get_cart()

@csrf.exempt
@app.route('/buscar_productos')
def buscar_productos_exempt():
    return buscar_productos()

@csrf.exempt
@app.route('/checkout', methods=['POST'])
def checkout_exempt():
    return checkout()

@csrf.exempt
@app.route('/presupuesto', methods=['POST'])
def presupuesto_exempt():
    return presupuesto()

@csrf.exempt
@app.route('/buscar_clientes')
def buscar_clientes_exempt():
    return buscar_clientes()

@csrf.exempt
@app.route('/guardar_cliente', methods=['POST'])
def guardar_cliente_exempt():
    return guardar_cliente()

@csrf.exempt
@app.route('/buscar_factura_devolucion')
def buscar_factura_devolucion_exempt():
    return buscar_factura_devolucion()

@csrf.exempt
@app.route('/procesar_devolucion', methods=['POST'])
def procesar_devolucion_exempt():
    return procesar_devolucion()

# ==================== MIDDLEWARE DE PERFORMANCE ====================
@app.before_request
def start_timer():
    g.start_time = time.time()

@app.after_request
def add_performance_headers(response):
    if hasattr(g, 'start_time'):
        elapsed_ms = (time.time() - g.start_time) * 1000
        response.headers['X-Response-Time-ms'] = str(int(elapsed_ms))
        if elapsed_ms > 7000:
            logger.warning(f"[WARN] LCP excedido: {elapsed_ms:.0f}ms en {request.path}")
    
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    if response.status_code == 200:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        
    return response

# ==================== HEALTH CHECK ====================
@app.route('/health')
def health_check():
    try:
        conn, cursor = get_db()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        db_status = "unhealthy"
    return jsonify({
        'status': 'healthy' if db_status == 'healthy' else 'degraded',
        'database': db_status,
        'timestamp': datetime.now().isoformat(),
        'version': '3.0.0'
    })

# ==================== METRICS ====================
@app.route('/metrics')
def metrics():
    if session.get('role') != 'Dueño' and 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 403
    try:
        uptime = time.time() - g.get('start_time', time.time())
        memoria = psutil.virtual_memory()
        return jsonify({
            'sistema': {
                'versión': '3.0.0',
                'python': platform.python_version(),
                'servidor': 'Flask',
                'uptime_segundos': round(uptime),
                'hostname': platform.node()
            },
            'cache': {
                'tasa_actual': _cache_exchange_rate['rate'],
                'descuento_actual': _cache_discount['percent'],
                'tasa_ultima_actualizacion': _cache_exchange_rate['timestamp'],
                'ttl_segundos': CACHE_TTL
            },
            'seguridad': {
                'intentos_login': {ip: len(attempts) for ip, attempts in list(_login_attempts.items())[:10]},
                'total_intentos_activos': sum(len(attempts) for attempts in _login_attempts.values())
            },
            'base_datos': {
                'pool_conexiones': 0,
                'pool_activo': False
            },
            'rendimiento': {
                'memoria_uso_porcentaje': memoria.percent,
                'memoria_disponible_mb': round(memoria.available / (1024 * 1024), 2)
            },
            'bcv_rate': {
                'tasa_actual': _bcv_rate_cache['rate'],
                'ultima_actualizacion': _bcv_rate_cache['timestamp'],
                'fecha_tasa': _bcv_rate_cache['date']
            },
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error en metrics: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== DECORADORES ====================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Debe iniciar sesión', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def permission_required(perm):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') == 'Dueño':
                return f(*args, **kwargs)
            try:
                result = execute_query("SELECT permissions FROM roles WHERE name = %s", (session.get('role'),), fetch_one=True)
                if result:
                    perms = json.loads(result['permissions'])
                    if perms.get(perm, False):
                        return f(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error en permiso: {e}")
            flash('No tiene permiso para esta acción', 'danger')
            return redirect(url_for('dashboard'))
        return decorated
    return decorator

# ==================== FUNCIONES DE CAJA ====================
def registrar_movimiento_caja(tipo, monto_bs, monto_usd, concepto, referencia_tipo=None,
                              referencia_id=None, metodo='efectivo', usuario=None, nota='', tasa=None):
    if tasa is None:
        tasa = get_cached_exchange_rate()
    if usuario is None:
        usuario = session.get('username', 'SISTEMA')
    try:
        try:
            execute_query("SELECT 1 FROM movimientos_caja LIMIT 1", fetch_one=True)
        except:
            execute_query("""
                CREATE TABLE IF NOT EXISTS movimientos_caja (
                    id SERIAL PRIMARY KEY,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tipo VARCHAR(20) NOT NULL,
                    monto_bs DECIMAL(10,2) NOT NULL,
                    monto_usd DECIMAL(10,2) NOT NULL,
                    concepto VARCHAR(255) NOT NULL,
                    referencia_tipo VARCHAR(50),
                    referencia_id INT,
                    metodo VARCHAR(50),
                    usuario VARCHAR(100),
                    nota TEXT,
                    tasa_usd DECIMAL(10,4)
                )
            """, commit=True)
        query = """INSERT INTO movimientos_caja
                   (tipo, monto_bs, monto_usd, concepto, referencia_tipo, referencia_id,
                    metodo, usuario, nota, tasa_usd, fecha)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"""
        execute_query(query, (tipo, monto_bs, monto_usd, concepto, referencia_tipo,
                              referencia_id, metodo, usuario, nota, tasa), commit=True)
        actualizar_saldo_caja(tipo, monto_bs, monto_usd)
        logger.info(f"[CAJA] {tipo}: {concepto} - Bs.{monto_bs:.2f} / ${monto_usd:.2f}")
        return True
    except Exception as e:
        logger.error(f"Error en registrar_movimiento_caja: {e}")
        return False

def actualizar_saldo_caja(tipo, monto_bs, monto_usd):
    signo = 1 if tipo == 'ingreso' else -1
    try:
        try:
            execute_query("SELECT 1 FROM saldo_caja LIMIT 1", fetch_one=True)
        except:
            execute_query("""
                CREATE TABLE IF NOT EXISTS saldo_caja (
                    id INT PRIMARY KEY CHECK (id = 1),
                    saldo_bs DECIMAL(10,2) DEFAULT 0,
                    saldo_usd DECIMAL(10,2) DEFAULT 0,
                    ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """, commit=True)
            execute_query("INSERT INTO saldo_caja (id, saldo_bs, saldo_usd) VALUES (1, 0, 0) ON CONFLICT DO NOTHING", commit=True)
        query = """
            UPDATE saldo_caja
            SET saldo_bs = saldo_bs + %s,
                saldo_usd = saldo_usd + %s,
                ultima_actualizacion = NOW()
            WHERE id = 1
        """
        execute_query(query, (signo * monto_bs, signo * monto_usd), commit=True)
    except Exception as e:
        logger.error(f"Error en actualizar_saldo_caja: {e}")

def obtener_saldo_actual():
    try:
        try:
            result = execute_query("SELECT saldo_bs, saldo_usd FROM saldo_caja WHERE id = 1", fetch_one=True)
        except:
            execute_query("""
                CREATE TABLE IF NOT EXISTS saldo_caja (
                    id INT PRIMARY KEY CHECK (id = 1),
                    saldo_bs DECIMAL(10,2) DEFAULT 0,
                    saldo_usd DECIMAL(10,2) DEFAULT 0,
                    ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """, commit=True)
            execute_query("INSERT INTO saldo_caja (id, saldo_bs, saldo_usd) VALUES (1, 0, 0) ON CONFLICT DO NOTHING", commit=True)
            result = execute_query("SELECT saldo_bs, saldo_usd FROM saldo_caja WHERE id = 1", fetch_one=True)
        if not result:
            return {'bs': 0, 'usd': 0}
        saldo_bs = float(result['saldo_bs']) if result['saldo_bs'] else 0
        saldo_usd = float(result['saldo_usd']) if result['saldo_usd'] else 0
        if saldo_bs > 1000000000:
            saldo_bs = 0
            execute_query("UPDATE saldo_caja SET saldo_bs = 0 WHERE id = 1", commit=True)
        if saldo_usd > 1000000:
            saldo_usd = 0
            execute_query("UPDATE saldo_caja SET saldo_usd = 0 WHERE id = 1", commit=True)
        return {'bs': saldo_bs, 'usd': saldo_usd}
    except Exception as e:
        logger.error(f"Error en obtener_saldo_actual: {e}")
        return {'bs': 0, 'usd': 0}

def obtener_movimientos_caja(limite=100, tipo=None, desde=None, hasta=None):
    try:
        try:
            execute_query("SELECT 1 FROM movimientos_caja LIMIT 1", fetch_one=True)
        except:
            return []
        query = "SELECT * FROM movimientos_caja"
        params = []
        condiciones = []
        if tipo:
            condiciones.append("tipo = %s")
            params.append(tipo)
        if desde:
            condiciones.append("DATE(fecha) >= %s")
            params.append(desde)
        if hasta:
            condiciones.append("DATE(fecha) <= %s")
            params.append(hasta)
        if condiciones:
            query += " WHERE " + " AND ".join(condiciones)
        query += " ORDER BY fecha DESC LIMIT %s"
        params.append(limite)
        return execute_query(query, tuple(params), fetch_all=True)
    except Exception as e:
        logger.error(f"Error en obtener_movimientos_caja: {e}")
        return []

def obtener_proximo_return_number():
    try:
        try:
            execute_query("SELECT 1 FROM returns LIMIT 1", fetch_one=True)
        except:
            return 1
        max_result = execute_query("SELECT COALESCE(MAX(return_number), 0) as max_return FROM returns", fetch_one=True)
        max_return = int(max_result['max_return']) if max_result else 0
        config_result = execute_query("SELECT value FROM config WHERE key_name = 'next_return'", fetch_one=True)
        config_value = int(config_result['value']) if config_result else 1
        next_return = max(max_return + 1, config_value)
        execute_query("UPDATE config SET value = %s WHERE key_name = 'next_return'", (str(next_return),), commit=True)
        return next_return
    except Exception as e:
        logger.error(f"Error obteniendo próximo número de devolución: {e}")
        result = execute_query("SELECT COALESCE(MAX(return_number), 0) + 1 as next_return FROM returns", fetch_one=True)
        return int(result['next_return']) if result else 1

# ==================== INTEGRACIÓN TELEGRAM Y GEOLOCALIZACIÓN ====================
def enviar_telegram(mensaje, parse_mode='HTML'):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "TU_TOKEN_AQUI":
        logger.warning("[TELEGRAM] Token no configurado. Mensaje no enviado.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": parse_mode
        }
        respuesta = requests.post(url, data=payload, timeout=10)
        if respuesta.status_code == 200:
            logger.info("[TELEGRAM] Mensaje enviado correctamente")
            return True
        else:
            logger.error(f"[TELEGRAM] Error al enviar: {respuesta.text}")
            return False
    except Exception as e:
        logger.error(f"[TELEGRAM] Excepción al enviar: {e}")
        return False

def obtener_ubicacion_ip(ip):
    try:
        if not ip or ip in ('127.0.0.1', 'localhost', '::1'):
            return {'pais': 'Local', 'ciudad': 'Local', 'region': 'Local', 'isp': 'Red local'}
        url = "http://ip-api.com/json/" + str(ip) + "?fields=status,country,regionName,city,isp&lang=es"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get('status') == 'success':
            return {
                'pais': data.get('country', 'Desconocido'),
                'ciudad': data.get('city', 'Desconocida'),
                'region': data.get('regionName', 'Desconocida'),
                'isp': data.get('isp', 'Desconocido')
            }
    except Exception as e:
        logger.error(f"Error obteniendo ubicacion de IP {ip}: {e}")
    return None

def obtener_navegador():
    try:
        ua = request.headers.get('User-Agent', '')
        if 'Edg/' in ua: nav = 'Edge'
        elif 'OPR/' in ua or 'Opera' in ua: nav = 'Opera'
        elif 'Firefox/' in ua: nav = 'Firefox'
        elif 'Chrome/' in ua: nav = 'Chrome'
        elif 'Safari/' in ua: nav = 'Safari'
        else: nav = 'Desconocido'
        
        if 'Windows' in ua: so = 'Windows'
        elif 'Mac OS' in ua or 'Macintosh' in ua: so = 'macOS'
        elif 'Android' in ua: so = 'Android'
        elif 'iPhone' in ua or 'iPad' in ua: so = 'iOS'
        elif 'Linux' in ua: so = 'Linux'
        else: so = 'Desconocido'
        return nav + " en " + so
    except:
        return "Desconocido"

def enviar_login_telegram(username, rol, ip, ubicacion, navegador):
    try:
        if ubicacion:
            ubicacion_txt = ubicacion['ciudad'] + ", " + ubicacion['region'] + ", " + ubicacion['pais']
            isp_txt = ubicacion['isp']
        else:
            ubicacion_txt = "No disponible"
            isp_txt = "No disponible"
        mensaje = (
            f"🔔 <b>NUEVO INICIO DE SESIÓN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Usuario:</b> {username}\n"
            f"🔑 <b>Rol:</b> {rol}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>IP:</b> <code>{ip}</code>\n"
            f"📍 <b>Ubicación:</b> {ubicacion_txt}\n"
            f"🏢 <b>ISP:</b> {isp_txt}\n"
            f"🖥️ <b>Navegador:</b> {navegador}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        )
        enviar_telegram(mensaje)
    except Exception as e:
        logger.error(f"Error enviando login a Telegram: {e}")

def enviar_cierre_caja_telegram(total_usd, total_bs, efectivo_bs, efectivo_usd,
                                 zelle, pago_movil, transferencia, tarjeta,
                                 apertura, diferencia, cajero, supervisor, tasa):
    fecha_actual = datetime.now().strftime('%d/%m/%Y %I:%M %p')
    if diferencia > 0.01:
        estado = f"⚠️ SOBRANTE: Bs. {diferencia:,.2f}"
    elif diferencia < -0.01:
        estado = f"❌ FALTANTE: Bs. {diferencia:,.2f}"
    else:
        estado = "✅ CUADRADO PERFECTO"
    mensaje = (
        f"🧾 <b>CIERRE DE CAJA - RENAVEN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>Fecha:</b> {fecha_actual}\n"
        f"👤 <b>Cajero:</b> {cajero}\n"
        f"👔 <b>Supervisor:</b> {supervisor}\n"
        f"💱 <b>Tasa:</b> Bs. {tasa:,.2f}/USD\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Total Vendido:</b>\n"
        f"   • Bs. {total_bs:,.2f}\n"
        f"   • ${total_usd:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Desglose de Pagos:</b>\n"
        f"   • Efectivo Bs: Bs. {efectivo_bs:,.2f}\n"
        f"   • Efectivo USD: ${efectivo_usd:,.2f}\n"
        f"   • Zelle: ${zelle:,.2f}\n"
        f"   • Pago Móvil: Bs. {pago_movil:,.2f}\n"
        f"   • Transferencia: Bs. {transferencia:,.2f}\n"
        f"   • Tarjeta: Bs. {tarjeta:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Fondo Apertura:</b> Bs. {apertura:,.2f}\n"
        f"📊 <b>Resultado:</b> {estado}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ <i>Sistema RENAVEN</i>"
    )
    return enviar_telegram(mensaje)

def _procesar_comando_telegram(update):
    try:
        mensaje = update.get('message', {})
        chat_id = str(mensaje.get('chat', {}).get('id', ''))
        texto = mensaje.get('text', '').strip()
        if chat_id != TELEGRAM_CHAT_ID:
            logger.warning(f"[TELEGRAM] Comando ignorado de chat no autorizado: {chat_id}")
            return

        if texto.startswith('/reiniciar'):
            partes = texto.split()
            if len(partes) == 2 and partes[1] == TELEGRAM_CODIGO_REINICIO:
                enviar_telegram("🔄 <b>Reiniciando el sistema RENAVEN en Render...</b>\n⏳ Esto tardará aproximadamente 2-3 minutos.")
                time.sleep(2)
                try:
                    respuesta = requests.get(RENDER_DEPLOY_HOOK, timeout=30)
                    if respuesta.status_code in (200, 202):
                        enviar_telegram("✅ <b>Deploy Hook ejecutado correctamente</b>\n🔄 Render está reiniciando el servicio.\n⏱️ Espera 2-3 minutos y vuelve a entrar al sistema.")
                    else:
                        enviar_telegram(f"⚠️ El Deploy Hook respondió con código {respuesta.status_code}. Revisa los logs de Render.")
                except Exception as e:
                    enviar_telegram(f"❌ Error al contactar Render: {str(e)}")
            else:
                enviar_telegram("❌ <b>Código incorrecto</b>\nUso correcto:\n<code>/reiniciar Katherine2026Seguro</code>")

        elif texto == '/estado':
            try:
                saldo = obtener_saldo_actual()
                rate = get_cached_exchange_rate()
                enviar_telegram(
                    f"📊 <b>ESTADO DEL SISTEMA RENAVEN</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💱 <b>Tasa BCV:</b> Bs. {rate:,.2f}\n"
                    f"💰 <b>Saldo Caja Bs:</b> Bs. {saldo['bs']:,.2f}\n"
                    f"💵 <b>Saldo Caja USD:</b> ${saldo['usd']:,.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
                )
            except Exception as e:
                enviar_telegram(f"⚠️ Error al consultar estado: {str(e)}")

        elif texto == '/ayuda' or texto == '/start':
            enviar_telegram(
                "🤖 <b>COMANDOS DISPONIBLES - RENAVEN</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "📊 <code>/estado</code>\n"
                "   Ver saldo de caja y tasa BCV\n\n"
                "🔄 <code>/reiniciar [código]</code>\n"
                "   Reiniciar el servidor en Render\n\n"
                "❓ <code>/ayuda</code>\n"
                "   Ver esta lista de comandos\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⚙️ <i>Sistema RENAVEN</i>"
            )
    except Exception as e:
        logger.error(f"[TELEGRAM] Error procesando comando: {e}")

def _escuchar_telegram():
    global _telegram_bot_iniciado
    if _telegram_bot_iniciado:
        return
    _telegram_bot_iniciado = True
    offset = None
    logger.info("[TELEGRAM] Iniciando escucha de comandos...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": offset}
            response = requests.get(url, params=params, timeout=35)
            data = response.json()
            if data.get('ok'):
                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    _procesar_comando_telegram(update)
        except Exception as e:
            logger.debug(f"[TELEGRAM] Polling error (reintentando): {e}")
            time.sleep(5)

# ==================== API TASA BCV ====================
@app.route('/api/bcv-rate')
def api_bcv_rate():
    try:
        ahora = time.time()
        if _bcv_rate_cache['rate'] and (ahora - _bcv_rate_cache['timestamp'] < BCV_CACHE_TTL):
            return jsonify({
                'success': True,
                'rate': _bcv_rate_cache['rate'],
                'date': _bcv_rate_cache['date'],
                'source': 'bcv',
                'cached': True,
                'last_update': datetime.fromtimestamp(_bcv_rate_cache['timestamp']).isoformat()
            })
        current_rate = get_cached_exchange_rate()
        def background_update():
            with app.app_context():
                update_bcv_rate()
        threading.Thread(target=background_update, daemon=True).start()
        return jsonify({
            'success': True,
            'rate': current_rate,
            'date': _bcv_rate_cache.get('date'),
            'source': 'database',
            'cached': False,
            'last_update': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error en api_bcv_rate: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'rate': get_cached_exchange_rate()
        }), 500

@app.route('/api/force-update-bcv')
@login_required
@permission_required('exchange_rate')
def force_update_bcv():
    success = update_bcv_rate()
    if success:
        new_rate = get_cached_exchange_rate()
        flash(f'[OK] Tasa BCV actualizada manualmente a Bs. {new_rate:.4f}', 'success')
    else:
        flash('[ERROR] No se pudo actualizar la tasa desde el BCV. Verifique su conexión a internet.', 'danger')
    return redirect(url_for('exchange_rate'))

# ==================== VERIFICAR COLUMNAS ====================
def verificar_columnas_cash_closures(cursor):
    try:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'cash_closures' AND column_name = 'devoluciones_bs'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE cash_closures ADD COLUMN devoluciones_bs DECIMAL(10,2) DEFAULT 0")
            logger.info("[DB] Columna devoluciones_bs agregada a cash_closures")
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'cash_closures' AND column_name = 'devoluciones_usd'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE cash_closures ADD COLUMN devoluciones_usd DECIMAL(10,2) DEFAULT 0")
            logger.info("[DB] Columna devoluciones_usd agregada a cash_closures")
    except Exception as e:
        logger.warning(f"No se pudieron verificar/agregar columnas en cash_closures: {e}")

def verificar_columnas_products(cursor):
    try:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'almacen'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE products ADD COLUMN almacen VARCHAR(50) DEFAULT 'Tienda 1'")
            logger.info("[DB] Columna almacen agregada a products")
    except Exception as e:
        logger.warning(f"No se pudo verificar/agregar columna almacen en products: {e}")

def verificar_columnas_sales(cursor):
    try:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sales' AND column_name = 'almacen'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE sales ADD COLUMN almacen VARCHAR(50) DEFAULT 'Tienda 1'")
            logger.info("[DB] Columna almacen agregada a sales")
    except Exception as e:
        logger.warning(f"No se pudo verificar/agregar columna almacen en sales: {e}")

def verificar_columna_empresa(cursor):
    """
    Agrega la columna empresa_id a las tablas necesarias si no existe.
    Esto permite el sistema Multi-Empresa.
    """
    try:
        tablas = ['products', 'sales', 'returns', 'users', 'cash_closures', 'clientes_frecuentes']
        for tabla in tablas:
            cursor.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{tabla}' AND column_name = 'empresa_id'")
            if not cursor.fetchone():
                cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN empresa_id INT DEFAULT 1")
                logger.info(f"[DB] Columna empresa_id agregada a {tabla}")
    except Exception as e:
        logger.warning(f"No se pudo agregar empresa_id: {e}")

# ==================== INICIALIZAR BASE DE DATOS ====================
def init_db():
    global _db_initialized
    if _db_initialized:
        logger.info("Base de datos ya estaba inicializada, omitiendo...")
        return True
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(255),
            password VARCHAR(255) NOT NULL,
            role VARCHAR(50) NOT NULL,
            empresa_id INT DEFAULT 1,
            failed_attempts INT DEFAULT 0,
            locked_until INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS roles (
            name VARCHAR(50) PRIMARY KEY,
            permissions TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            barcode VARCHAR(100),
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100),
            unit VARCHAR(50),
            sale_type VARCHAR(20) DEFAULT 'unit',
            almacen VARCHAR(50) DEFAULT 'Tienda 1',
            cost DECIMAL(10,2) DEFAULT 0,
            margin DECIMAL(10,2) DEFAULT 0,
            price DECIMAL(10,2) DEFAULT 0,
            stock DECIMAL(10,2) DEFAULT 0,
            arrival_date VARCHAR(50),
            stock_status VARCHAR(20) DEFAULT 'disponible',
            activo SMALLINT DEFAULT 1,
            empresa_id INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS categories (
            name VARCHAR(100) PRIMARY KEY
        )""",
        """CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            invoice_number INT NOT NULL,
            date VARCHAR(50),
            user_id INT,
            client_id VARCHAR(50),
            client_name VARCHAR(255),
            client_phone VARCHAR(50),
            client_address TEXT,
            items TEXT,
            subtotal DECIMAL(10,2),
            discount DECIMAL(10,2),
            tax DECIMAL(10,2),
            total_usd DECIMAL(10,2),
            total_bs DECIMAL(10,2),
            almacen VARCHAR(50) DEFAULT 'Tienda 1',
            payment_method TEXT,
            payment_details TEXT,
            pago_efectivo_bs DECIMAL(10,2) DEFAULT 0,
            pago_efectivo_usd DECIMAL(10,2) DEFAULT 0,
            pago_zelle DECIMAL(10,2) DEFAULT 0,
            pago_movil DECIMAL(10,2) DEFAULT 0,
            pago_transferencia DECIMAL(10,2) DEFAULT 0,
            pago_tarjeta DECIMAL(10,2) DEFAULT 0,
            cambio_usd DECIMAL(10,2) DEFAULT 0,
            cambio_bs DECIMAL(10,2) DEFAULT 0,
            notes TEXT,
            empresa_id INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS purchases (
            id SERIAL PRIMARY KEY,
            supplier_invoice VARCHAR(100),
            supplier_name VARCHAR(255),
            date VARCHAR(50),
            arrival_date VARCHAR(50),
            product_id INT,
            product_name VARCHAR(255),
            quantity DECIMAL(10,2),
            unit_cost DECIMAL(10,2),
            total_cost DECIMAL(10,2)
        )""",
        """CREATE TABLE IF NOT EXISTS cash_closures (
            id SERIAL PRIMARY KEY,
            closure_date VARCHAR(50),
            user_id INT,
            user_name VARCHAR(100),
            cashier_name VARCHAR(100),
            supervisor_username VARCHAR(100),
            opening_balance DECIMAL(10,2),
            sales_total_bs DECIMAL(10,2),
            sales_total_usd DECIMAL(10,2),
            devoluciones_total_bs DECIMAL(10,2) DEFAULT 0,
            devoluciones_total_usd DECIMAL(10,2) DEFAULT 0,
            total_neto_bs DECIMAL(10,2),
            total_neto_usd DECIMAL(10,2),
            cash_bs_delivered DECIMAL(10,2),
            cash_usd_delivered DECIMAL(10,2),
            zelle_delivered DECIMAL(10,2),
            pago_movil_delivered DECIMAL(10,2),
            transferencia_delivered DECIMAL(10,2),
            tarjeta_delivered DECIMAL(10,2),
            difference DECIMAL(10,2),
            notes TEXT,
            empresa_id INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS config (
            key_name VARCHAR(100) PRIMARY KEY,
            value TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS company (
            id INT PRIMARY KEY CHECK (id = 1),
            name VARCHAR(255),
            rif VARCHAR(50),
            address TEXT,
            phone VARCHAR(50),
            email VARCHAR(255),
            parish VARCHAR(100),
            city VARCHAR(100),
            state VARCHAR(100)
        )""",
        """CREATE TABLE IF NOT EXISTS user_logs (
            id SERIAL PRIMARY KEY,
            user_id INT,
            username VARCHAR(100),
            action VARCHAR(255),
            details TEXT,
            ip_address VARCHAR(50),
            date VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY,
            user_id INT,
            token VARCHAR(255),
            expires INT,
            used SMALLINT DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS clientes_frecuentes (
            id SERIAL PRIMARY KEY,
            cedula VARCHAR(50) UNIQUE NOT NULL,
            nombre VARCHAR(255) NOT NULL,
            telefono VARCHAR(50),
            direccion TEXT NOT NULL,
            ultima_compra VARCHAR(50),
            total_compras INT DEFAULT 1,
            monto_total DECIMAL(10,2) DEFAULT 0,
            empresa_id INT DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS deleted_products (
            id SERIAL PRIMARY KEY,
            original_id INT NOT NULL,
            barcode VARCHAR(100),
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100),
            unit VARCHAR(50),
            stock DECIMAL(10,2) DEFAULT 0,
            cost DECIMAL(10,2) DEFAULT 0,
            price DECIMAL(10,2) DEFAULT 0,
            margin DECIMAL(10,2) DEFAULT 0,
            arrival_date DATE,
            motivo_eliminacion TEXT NOT NULL,
            usuario_elimino VARCHAR(100) NOT NULL,
            fecha_eliminacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS returns (
            id SERIAL PRIMARY KEY,
            return_number INT UNIQUE NOT NULL,
            original_invoice INT NOT NULL,
            return_date VARCHAR(50),
            user_id INT,
            client_id VARCHAR(50),
            client_name VARCHAR(255),
            items TEXT,
            subtotal DECIMAL(10,2),
            total_usd DECIMAL(10,2),
            total_bs DECIMAL(10,2),
            reason TEXT,
            empresa_id INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS exchange_rate_history (
            id SERIAL PRIMARY KEY,
            tasa_anterior DECIMAL(10,2),
            tasa_nueva DECIMAL(10,2),
            descuento INT,
            usuario VARCHAR(100),
            fecha VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS cash_opening (
            id SERIAL PRIMARY KEY,
            opening_date VARCHAR(50),
            user_id INT,
            user_name VARCHAR(100),
            opening_balance DECIMAL(10,2),
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS movimientos_caja (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            tipo VARCHAR(20) NOT NULL,
            monto_bs DECIMAL(10,2) NOT NULL,
            monto_usd DECIMAL(10,2) NOT NULL,
            concepto VARCHAR(255) NOT NULL,
            referencia_tipo VARCHAR(50),
            referencia_id INT,
            metodo VARCHAR(50),
            usuario VARCHAR(100),
            nota TEXT,
            tasa_usd DECIMAL(10,4)
        )""",
        """CREATE TABLE IF NOT EXISTS saldo_caja (
            id INT PRIMARY KEY CHECK (id = 1),
            saldo_bs DECIMAL(10,2) DEFAULT 0,
            saldo_usd DECIMAL(10,2) DEFAULT 0,
            ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS movimientos_devolucion (
            id SERIAL PRIMARY KEY,
            devolucion_id INT NOT NULL,
            producto_id INT NOT NULL,
            cantidad DECIMAL(10,2) NOT NULL,
            precio_unitario DECIMAL(10,2) NOT NULL,
            subtotal DECIMAL(10,2) NOT NULL,
            iva DECIMAL(10,2) NOT NULL,
            total_usd DECIMAL(10,2) NOT NULL,
            total_bs DECIMAL(10,2) NOT NULL,
            motivo TEXT NOT NULL,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS notas_credito (
            id SERIAL PRIMARY KEY,
            numero VARCHAR(50) UNIQUE NOT NULL,
            cliente_id VARCHAR(50) NOT NULL,
            cliente_nombre VARCHAR(255) NOT NULL,
            monto_total DECIMAL(10,2) NOT NULL,
            monto_usado DECIMAL(10,2) DEFAULT 0,
            fecha_emision TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fecha_vencimiento TIMESTAMP,
            activo SMALLINT DEFAULT 1,
            creado_por VARCHAR(100),
            notas TEXT
        )""",
    ]
    conn = None
    cursor = None
    try:
        conn, cursor = get_db()
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = cursor.fetchall()
        if tables and len(tables) > 0:
            logger.info("Las tablas ya existen, omitiendo creación...")
            _db_initialized = True
            verificar_columnas_cash_closures(cursor)
            verificar_columnas_products(cursor)
            verificar_columnas_sales(cursor)
            verificar_columna_empresa(cursor) # <--- NUEVA VERIFICACIÓN
            conn.commit()
            return True
        for query in queries:
            try:
                cursor.execute(query)
            except Exception as e:
                logger.warning(f"Error creando tabla: {e}")
                conn.rollback()
        conn.commit()
        insert_initial_data(cursor)
        conn.commit()
        cursor.execute("INSERT INTO config (key_name, value) VALUES ('next_return', '1') ON CONFLICT DO NOTHING")
        conn.commit()
        cursor.execute("INSERT INTO saldo_caja (id, saldo_bs, saldo_usd) VALUES (1, 0, 0) ON CONFLICT DO NOTHING")
        conn.commit()
        _db_initialized = True
        logger.info("Base de datos PostgreSQL inicializada correctamente")
        return True
    except Exception as e:
        logger.error(f"Error al inicializar DB: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()  # Cierra la conexión directamente
            except Exception:
                pass

def insert_initial_data(cursor):
    cursor.execute("SELECT COUNT(*) as count FROM users")
    result = cursor.fetchone()
    if result and result['count'] > 0:
        logger.info("Los datos iniciales ya existen, omitiendo inserción...")
        return
    categorias = ["Herramientas", "Pinturas", "Electricidad", "Fontanería", "Construcción", "Jardinería"]
    for cat in categorias:
        cursor.execute("INSERT INTO categories (name) VALUES (%s) ON CONFLICT DO NOTHING", (cat,))
    admin_perms = '{"inventory":true,"edit_products":true,"buy":true,"sell":true,"users":true,"roles":true,"reports":true,"exchange_rate":true,"cash_closure":true,"view_sales":true}'
    cajero_perms = '{"inventory":true,"edit_products":false,"buy":false,"sell":true,"users":false,"roles":false,"reports":false,"exchange_rate":false,"cash_closure":false,"view_sales":false}'
    almacenista_perms = '{"inventory":true,"edit_products":true,"buy":true,"sell":false,"users":false,"roles":false,"reports":false,"exchange_rate":false,"cash_closure":false,"view_sales":false}'
    supervisor_perms = '{"inventory":true,"edit_products":false,"buy":false,"sell":true,"users":false,"roles":false,"reports":true,"exchange_rate":false,"cash_closure":true,"view_sales":true}'
    roles = [
        ('Dueño', admin_perms),
        ('Cajera', cajero_perms),
        ('Almacenista', almacenista_perms),
        ('Supervisor', supervisor_perms)
    ]
    for name, perms in roles:
        cursor.execute("INSERT INTO roles (name, permissions) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING", (name, perms))
    usuarios = [
        ('dueño', 'admin@renaven.com', bcrypt.hashpw('Admin123!@#Renaven'.encode(), bcrypt.gensalt()).decode(), 'Dueño', 1),
        ('supervisor', 'supervisor@renaven.com', bcrypt.hashpw('Super2024!@#Seguro'.encode(), bcrypt.gensalt()).decode(), 'Supervisor', 1),
        ('cajera', 'cajera@renaven.com', bcrypt.hashpw('Cajera$2024Segura'.encode(), bcrypt.gensalt()).decode(), 'Cajera', 1),
        ('almacen', 'almacen@renaven.com', bcrypt.hashpw('Almacen*2024Safe'.encode(), bcrypt.gensalt()).decode(), 'Almacenista', 1),
        ('jonathan', 'hernandezrivasjonathanjesus@gmail.com', bcrypt.hashpw('Jonathan$2024Secure!'.encode(), bcrypt.gensalt()).decode(), 'Dueño', 1)
    ]
    for username, email, pwd, role, empresa_id in usuarios:
        cursor.execute("INSERT INTO users (username, email, password, role, empresa_id) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (username) DO NOTHING",
                    (username, email, pwd, role, empresa_id))
    cursor.execute("INSERT INTO company (id, name, rif, address, phone, email, parish, city, state) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                ('RENAVEN OAKMONT, C.A.', 'J-41323725-3', 'CALLE 1 ANDRES BELLO N°01-06, SECTOR FRANCISCO DE MIRANDA II', '0412-2520053', 'renaven@correo.com', 'PUNTA CARDON', 'PUNTO FIJO', 'FALCON'))
    config_data = [
        ('exchange_rate', '36.50'),
        ('discount_percent', '5'),
        ('next_invoice', '1'),
        ('next_return', '1'),
        ('opening_balance', '0')
    ]
    for key, value in config_data:
        cursor.execute("INSERT INTO config (key_name, value) VALUES (%s, %s) ON CONFLICT (key_name) DO NOTHING", (key, value))

# ==================== FUNCIONES AUXILIARES ====================
def log_action(user_id, username, action, details="", ip_address=""):
    try:
        query = """INSERT INTO user_logs (user_id, username, action, details, ip_address, date)
                VALUES (%s, %s, %s, %s, %s, %s)"""
        execute_query(query, (user_id, username, action, details, ip_address or request.remote_addr,
                        datetime.now().isoformat()), commit=True)
    except Exception as e:
        logger.error(f"Error al registrar: {e}")

def send_reset_email(user_email, username, reset_link):
    BREVO_API_KEY = os.environ.get('BREVO_API_KEY')
    
    if not BREVO_API_KEY:
        logger.error("BREVO_API_KEY no configurada")
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #001C47 0%, #6BB6EC 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                <h2>RENAVEN OAKMONT, C.A.</h2>
                <p>Sistema de Facturación</p>
            </div>
            <div style="padding: 20px; background: #f8f9fa; border-radius: 0 0 10px 10px;">
                <h3>Hola {username},</h3>
                <p>Hemos recibido una solicitud para restablecer la contraseña.</p>
                <p><strong>Requisitos de la nueva contraseña:</strong></p>
                <ul>
                    <li>Mínimo 16 caracteres</li>
                    <li>Al menos una letra MAYÚSCULA (A-Z)</li>
                    <li>Al menos una letra minúscula (a-z)</li>
                    <li>Al menos un carácter especial (!@#$%^&*()_+-=[]{{}}|;:,.<>?/)</li>
                </ul>
                <div style="text-align: center; margin: 20px 0;">
                    <a href="{reset_link}" style="background: #001C47; color: white; padding: 12px 24px; text-decoration: none; border-radius: 50px;">Restablecer Contraseña</a>
                </div>
                <p><small>Este enlace expirará en 1 hora</small></p>
                <p><small>Si no solicitaste este cambio, ignora este correo.</small></p>
            </div>
            <div style="text-align: center; margin-top: 20px; font-size: 12px; color: #666;">
                <p>RENAVEN OAKMONT, C.A. - Sistema de Facturación</p>
            </div>
        </div>
    </body>
    </html>
    """

    payload = {
        "sender": {
            "name": "RENAVEN",
            "email": "hernandezrivasjonathanjesus@gmail.com"
        },
        "to": [{"email": user_email, "name": username}],
        "subject": "Recuperación de Contraseña - RENAVEN",
        "htmlContent": html_content
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 201:
            logger.info(f"Correo enviado exitosamente a {user_email} vía Brevo")
            return True
        else:
            logger.error(f"Error Brevo: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error al enviar correo a {user_email}: {e}")
        return False

def limpiar_o_reactivar_productos_agotados():
    try:
        productos_agotados = execute_query("""
            SELECT id, name, barcode, stock FROM products
            WHERE stock <= 0 AND activo = 1
        """, fetch_all=True)
        eliminados = 0
        for p in productos_agotados:
            execute_query("""
                INSERT INTO deleted_products (original_id, barcode, name, category, unit, stock, cost, price, margin, arrival_date, motivo_eliminacion, usuario_elimino, fecha_eliminacion)
                SELECT id, barcode, name, category, unit, stock, cost, price, margin, arrival_date, 'Producto agotado - Eliminación automática', 'Sistema', NOW()
                FROM products WHERE id = %s
            """, (p['id'],), commit=True)
            execute_query("DELETE FROM products WHERE id = %s", (p['id'],), commit=True)
            eliminados += 1
        if eliminados > 0:
            logger.info(f"Eliminados {eliminados} productos agotados")
        return eliminados
    except Exception as e:
        logger.error(f"Error en limpiar_o_reactivar_productos_agotados: {e}")
        return 0

# ==================== RUTAS DE AUTENTICACIÓN ====================
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/captcha/image')
def captcha_image():
    codigo = generar_codigo_captcha(5)
    session['captcha_text'] = codigo
    imagen = crear_imagen_captcha(codigo)
    buffer = io.BytesIO()
    imagen.save(buffer, format='PNG')
    buffer.seek(0)
    return make_response(buffer.getvalue()), 200, {'Content-Type': 'image/png'}

@app.route('/login', methods=['GET', 'POST'])
def login():
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        flash('[ERROR] Demasiados intentos. Espere 5 minutos antes de volver a intentar.', 'danger')
        timestamp = random.randint(1, 999999)
        return render_template('login.html', timestamp=timestamp)
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        captcha_user = request.form.get('captcha_code', '').upper()
        captcha_real = session.get('captcha_text', '')
        if not captcha_user or captcha_user != captcha_real:
            record_login_attempt(client_ip)
            flash('[ERROR] Código CAPTCHA incorrecto. Por favor, intente nuevamente.', 'danger')
            session.pop('captcha_text', None)
            timestamp = random.randint(1, 999999)
            return render_template('login.html', timestamp=timestamp)
        try:
            user = execute_query("SELECT * FROM users WHERE username = %s", (username,), fetch_one=True)
            if not user:
                record_login_attempt(client_ip)
                flash('[ERROR] Credenciales incorrectas', 'danger')
                session.pop('captcha_text', None)
                timestamp = random.randint(1, 999999)
                return render_template('login.html', timestamp=timestamp)
            if user['locked_until'] and user['locked_until'] > int(time.time()):
                flash('[WARN] Cuenta bloqueada por 15 minutos. Intenta más tarde.', 'danger')
                session.pop('captcha_text', None)
                timestamp = random.randint(1, 999999)
                return render_template('login.html', timestamp=timestamp)
            if bcrypt.checkpw(password.encode(), user['password'].encode()):
                execute_query("UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE id = %s", (user['id'],), commit=True)
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['empresa_id'] = user.get('empresa_id', 1) # <--- NUEVA LÍNEA
                session.permanent = True
                log_action(user['id'], username, 'LOGIN_EXITOSO', f"Rol: {user['role']} - Empresa: {user.get('empresa_id', 1)}")
                flash(f'[OK] Bienvenido {username}', 'success')
                session.pop('captcha_text', None)

                # ===== AVISO A TELEGRAM CON UBICACION =====
                try:
                    forwarded = request.headers.get('X-Forwarded-For')
                    if forwarded:
                        client_ip = forwarded.split(',')[0].strip()
                    ubicacion = obtener_ubicacion_ip(client_ip)
                    navegador = obtener_navegador()
                    threading.Thread(
                        target=enviar_login_telegram,
                        args=(user['username'], user['role'], client_ip, ubicacion, navegador),
                        daemon=True
                    ).start()
                except Exception as e:
                    logger.error(f"Error enviando aviso login Telegram: {e}")
                # ==========================================

                return redirect(url_for('dashboard'))
            else:
                attempts = user['failed_attempts'] + 1
                locked = int(time.time()) + 900 if attempts >= 3 else 0
                execute_query("UPDATE users SET failed_attempts = %s, locked_until = %s WHERE id = %s", (attempts, locked, user['id']), commit=True)
                log_action(user['id'], username, 'LOGIN_FALLIDO', f"Intento {attempts}/3")
                record_login_attempt(client_ip)
                flash(f'[ERROR] Credenciales incorrectas. Intento {attempts}/3', 'danger')
                session.pop('captcha_text', None)
                timestamp = random.randint(1, 999999)
                return render_template('login.html', timestamp=timestamp)
        except Exception as e:
            logger.error(f"Error en login: {e}")
            flash(f'[ERROR] Error al iniciar sesión: {str(e)}', 'danger')
            session.pop('captcha_text', None)
            timestamp = random.randint(1, 999999)
            return render_template('login.html', timestamp=timestamp)
    timestamp = random.randint(1, 999999)
    return render_template('login.html', timestamp=timestamp)

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_action(session['user_id'], session['username'], 'LOGOUT', 'Sesión cerrada')
    session.clear()
    flash('[OK] Sesión cerrada', 'success')
    return redirect(url_for('login'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        if not email:
            flash('[ERROR] Debe ingresar un correo electrónico', 'danger')
            return render_template('forgot_password.html')
        try:
            user = execute_query("SELECT * FROM users WHERE email = %s", (email,), fetch_one=True)
            if user:
                token = secrets.token_urlsafe(32)
                expires = int(time.time()) + 3600
                execute_query("INSERT INTO password_resets (user_id, token, expires) VALUES (%s, %s, %s)", (user['id'], token, expires), commit=True)
                reset_link = url_for('reset_password', token=token, _external=True)
                if send_reset_email(email, user['username'], reset_link):
                    flash('[OK] Se ha enviado un enlace de recuperación a tu correo electrónico', 'success')
                    log_action(user['id'], user['username'], 'RESET_REQUEST', f"Correo enviado a {email}")
                else:
                    flash('[ERROR] No se pudo enviar el correo. Verifica tu conexión o contacta al administrador.', 'danger')
            else:
                flash('[WARN] No existe una cuenta asociada a este correo electrónico', 'warning')
        except Exception as e:
            logger.error(f"Error en forgot_password: {e}")
            flash(f'[ERROR] Error: {str(e)}', 'danger')
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        reset = execute_query("SELECT * FROM password_resets WHERE token = %s AND used = 0 AND expires > %s", (token, int(time.time())), fetch_one=True)
        if not reset:
            flash('[ERROR] Enlace inválido o expirado', 'danger')
            return redirect(url_for('login'))
        if request.method == 'POST':
            new_password = request.form['password']
            errores = validar_contraseña_segura(new_password)
            if errores:
                for error in errores:
                    flash(f'[ERROR] {error}', 'danger')
                return render_template('reset_password.html', token=token)
            hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt())
            hashed_str = hashed.decode()
            user = execute_query("SELECT id, username, email FROM users WHERE id = %s", (reset['user_id'],), fetch_one=True)
            execute_query("UPDATE users SET password = %s WHERE id = %s", (hashed_str, reset['user_id']), commit=True)
            execute_query("UPDATE password_resets SET used = 1 WHERE id = %s", (reset['id'],), commit=True)
            flash('[OK] Contraseña actualizada correctamente. Ya puede iniciar sesión.', 'success')
            log_action(reset['user_id'], user['username'], 'PASSWORD_RESET', 'Contraseña restablecida correctamente')
            return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Error en reset_password: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return render_template('reset_password.html', token=token)

# ==================== PRODUCTO MÁS VENDIDO DEL MES ====================
def obtener_producto_mas_vendido_mes(month, year):
    try:
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"
        ventas = execute_query("""
            SELECT items FROM sales
            WHERE date >= %s AND date < %s AND invoice_number > 0
        """, (start, end), fetch_all=True)
        if not ventas:
            return None, 0, 0.0
        productos_acumulados = defaultdict(lambda: {'cantidad': 0.0, 'ingresos': 0.0})
        for venta in ventas:
            if not venta.get('items'):
                continue
            try:
                items = json.loads(venta['items']) if isinstance(venta['items'], str) else venta['items']
            except:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                nombre = (item.get('name') or '').strip().upper()
                if not nombre:
                    continue
                cantidad = float(item.get('quantity', 0) or 0)
                precio = float(item.get('price', 0) or 0)
                productos_acumulados[nombre]['cantidad'] += cantidad
                productos_acumulados[nombre]['ingresos'] += cantidad * precio
        if not productos_acumulados:
            return None, 0, 0.0
        ganador = max(productos_acumulados.items(), key=lambda x: x[1]['cantidad'])
        return ganador[0], ganador[1]['cantidad'], ganador[1]['ingresos']
    except Exception as e:
        logger.error(f"Error en obtener_producto_mas_vendido_mes: {e}")
        return None, 0, 0.0

# ==================== DASHBOARD ====================
@app.route('/dashboard')
@login_required
def dashboard():
    try:
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        rate = get_cached_exchange_rate()
        discount_percent = get_cached_discount()
        opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
        opening_balance = float(opening_result['value']) if opening_result else 0.0
        saldo = obtener_saldo_actual()
        role = session.get('role')
        result = execute_query("SELECT permissions FROM roles WHERE name = %s", (role,), fetch_one=True)
        can_view_sales = False
        if result:
            perms = json.loads(result['permissions'])
            can_view_sales = perms.get('view_sales', False)
        today = datetime.now()
        month = today.month
        year = today.year
        total_ventas_usd = total_ventas_bs = total_dia_usd = total_dia_bs = 0
        if can_view_sales:
            start = f"{year}-{month:02d}-01"
            if month == 12:
                end = f"{year+1}-01-01"
            else:
                end = f"{year}-{month+1:02d}-01"
            sales_month = execute_query("SELECT COALESCE(SUM(total_usd),0) as total_usd, COALESCE(SUM(total_bs),0) as total_bs FROM sales WHERE date >= %s AND date < %s AND invoice_number > 0", (start, end), fetch_one=True)
            if sales_month:
                total_ventas_usd = float(sales_month['total_usd']) if sales_month['total_usd'] else 0
                total_ventas_bs = float(sales_month['total_bs']) if sales_month['total_bs'] else 0
            today_str = today.strftime('%Y-%m-%d')
            sales_day = execute_query("SELECT COALESCE(SUM(total_usd),0) as total_usd, COALESCE(SUM(total_bs),0) as total_bs FROM sales WHERE date LIKE %s AND invoice_number > 0", (today_str + '%',), fetch_one=True)
            if sales_day:
                total_dia_usd = float(sales_day['total_usd']) if sales_day['total_usd'] else 0
                total_dia_bs = float(sales_day['total_bs']) if sales_day['total_bs'] else 0
        top_product_name = None
        top_product_qty = 0
        top_product_total = 0.0
        if role == 'Dueño':
            top_product_name, top_product_qty, top_product_total = obtener_producto_mas_vendido_mes(month, year)
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        current_date = today.strftime('%d/%m/%Y %H:%M:%S')
        return render_template('dashboard.html',
                              company=company,
                              exchange_rate=rate,
                              discount_percent=discount_percent,
                              opening_balance=opening_balance,
                              total_ventas_mes_usd=total_ventas_usd,
                              total_ventas_mes_bs=total_ventas_bs,
                              total_ventas_dia_usd=total_dia_usd,
                              total_ventas_dia_bs=total_dia_bs,
                              mes_actual=meses[month-1],
                              can_view_sales=can_view_sales,
                              saldo_bs=saldo['bs'],
                              saldo_usd=saldo['usd'],
                              datetime=datetime,
                              current_date=current_date,
                              top_product_name=top_product_name,
                              top_product_qty=top_product_qty,
                              top_product_total=top_product_total)
    except Exception as e:
        logger.error(f"Error en dashboard: {e}")
        flash(f'[ERROR] Error al cargar dashboard: {str(e)}', 'danger')
        return render_template('dashboard.html',
                              saldo_bs=0, saldo_usd=0, company=None, exchange_rate=40,
                              discount_percent=5, opening_balance=0,
                              total_ventas_mes_usd=0, total_ventas_mes_bs=0,
                              total_ventas_dia_usd=0, total_ventas_dia_bs=0,
                              mes_actual='', can_view_sales=False, datetime=datetime,
                              current_date=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                              top_product_name=None, top_product_qty=0, top_product_total=0.0)

# ==================== API SALDO ACTUAL ====================
@app.route('/api/saldo_actual')
@login_required
def api_saldo_actual():
    try:
        saldo = obtener_saldo_actual()
        return jsonify({'success': True, 'saldo_bs': saldo['bs'], 'saldo_usd': saldo['usd']})
    except Exception as e:
        logger.error(f"Error en api_saldo_actual: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== NOTA DE ENTREGA ====================
@app.route('/nota_entrega')
@login_required
@permission_required('sell')
def nota_entrega():
    try:
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        # Categorías filtradas (si la tabla categories tuviera empresa_id, se filtraría aquí)
        categories = execute_query("SELECT name FROM categories ORDER BY name", fetch_all=True)
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        exchange_rate = get_cached_exchange_rate()
        discount_percent = get_cached_discount()
        saldo = obtener_saldo_actual()
    except Exception as e:
        logger.error(f"Error en facturacion: {e}")
        categories = []
        company = None
        exchange_rate = 36.50
        discount_percent = 5.0
        saldo = {'bs': 0, 'usd': 0}
    return render_template('facturacion.html',
                          categories=categories, company=company,
                          exchange_rate=exchange_rate, discount_percent=discount_percent,
                          saldo_bs=saldo['bs'], saldo_usd=saldo['usd'])

def buscar_productos():
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    almacen = request.args.get('almacen', '')
    try:
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = "SELECT id, name, price, stock, unit, sale_type, barcode, almacen FROM products WHERE stock > 0 AND activo = 1"
        params = []
        
        if user_role != 'Dueño':
            query += " AND empresa_id = %s"
            params.append(user_empresa)
            
        if search:
            query += " AND name LIKE %s"
            params.append(f'%{search.upper()}%')
        if category and category != 'Todas':
            query += " AND category = %s"
            params.append(category)
        if almacen:
            query += " AND almacen = %s"
            params.append(almacen)
        query += " ORDER BY name"
        products = execute_query(query, tuple(params) if params else None, fetch_all=True)
        for p in products:
            p['price'] = float(p['price']) if p['price'] else 0
            p['stock'] = float(p['stock']) if p['stock'] else 0
            p['sale_type'] = p.get('sale_type', 'unit')
        return jsonify(products)
    except Exception as e:
        logger.error(f"Error en buscar_productos: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/buscar_productos')
def buscar_productos_route():
    return buscar_productos()

@app.route('/api/facturas')
@login_required
@permission_required('view_sales')
def api_facturas():
    try:
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = """SELECT s.invoice_number, s.date, s.client_name, s.client_id, s.subtotal, s.tax, s.total_usd, s.total_bs, s.payment_method, s.items FROM sales s WHERE s.invoice_number > 0"""
        
        if user_role != 'Dueño':
            query += f" AND s.empresa_id = {user_empresa}"
            
        query += " ORDER BY s.id DESC LIMIT 500"
        
        sales = execute_query(query, fetch_all=True)
        for sale in sales:
            if sale.get('items'):
                try:
                    sale['items_list'] = json.loads(sale['items']) if isinstance(sale['items'], str) else sale['items']
                except:
                    sale['items_list'] = []
            else:
                sale['items_list'] = []
            sale['subtotal'] = float(sale['subtotal']) if sale.get('subtotal') else 0
            sale['tax'] = float(sale['tax']) if sale.get('tax') else 0
            sale['total_usd'] = float(sale['total_usd']) if sale.get('total_usd') else 0
            sale['total_bs'] = float(sale['total_bs']) if sale.get('total_bs') else 0
        return jsonify(sales)
    except Exception as e:
        logger.error(f"Error en api_facturas: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== IMPRIMIR TICKET DE NOTA DE ENTREGA ====================
@app.route('/imprimir_ticket', methods=['POST'])
@login_required
@permission_required('sell')
def imprimir_ticket():
    try:
        # Recibimos los datos que manda el JavaScript desde facturacion.html
        data = request.get_json()
        
        # Aquí armamos el objeto 'nota' con los datos que nos manda la pantalla
        nota = {
            'numero': data.get('numero', 'NE-000000'),
            'fecha': data.get('fecha', ''),
            'cliente': {
                'nombre': data.get('cliente_nombre', 'Cliente Genérico'),
                'telefono': data.get('cliente_telefono', '')
            },
            'items': data.get('items', []),
            'total': data.get('total', '0.00'),
            'observaciones': data.get('observaciones', '')
        }
        
        # Traemos los datos de la empresa de la base de datos para el encabezado
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        
        # Renderizamos el HTML del ticket
        return render_template('nota_entrega_ticket.html', nota=nota, empresa=company)
        
    except Exception as e:
        logger.error(f"Error al generar ticket: {e}")
        return jsonify({'error': str(e)}), 500
        
# ==================== CLIENTES FRECUENTES ====================
def buscar_clientes():
    search = request.args.get('search', '')
    try:
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = "SELECT id, cedula, nombre, telefono, direccion FROM clientes_frecuentes WHERE 1=1"
        params = []
        
        if user_role != 'Dueño':
            query += " AND empresa_id = %s"
            params.append(user_empresa)
            
        if search:
            query += " AND (cedula LIKE %s OR nombre LIKE %s)"
            params.extend([f'%{search}%', f'%{search.upper()}%'])
            
        query += " ORDER BY ultima_compra DESC LIMIT 20"
        
        clientes = execute_query(query, tuple(params) if params else None, fetch_all=True)
        return jsonify(clientes)
    except Exception as e:
        logger.error(f"Error en buscar_clientes: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/buscar_clientes')
def buscar_clientes_route():
    return buscar_clientes()

def guardar_cliente():
    data = request.json
    cedula = data.get('cedula', '').strip()
    nombre = data.get('nombre', '').strip().upper()
    telefono = data.get('telefono', '').strip()
    direccion = data.get('direccion', '').strip().upper()
    if not cedula or not nombre or not direccion:
        return jsonify({'error': 'Datos incompletos'}), 400
    try:
        empresa_id = session.get('empresa_id', 1)
        existing = execute_query("SELECT id FROM clientes_frecuentes WHERE cedula = %s AND empresa_id = %s", (cedula, empresa_id), fetch_one=True)
        if existing:
            execute_query("""UPDATE clientes_frecuentes SET nombre = %s, telefono = %s, direccion = %s, ultima_compra = %s, total_compras = total_compras + 1 WHERE cedula = %s AND empresa_id = %s""", (nombre, telefono, direccion, datetime.now().isoformat(), cedula, empresa_id), commit=True)
        else:
            execute_query("""INSERT INTO clientes_frecuentes (cedula, nombre, telefono, direccion, ultima_compra, empresa_id) VALUES (%s, %s, %s, %s, %s, %s)""", (cedula, nombre, telefono, direccion, datetime.now().isoformat(), empresa_id), commit=True)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error en guardar_cliente: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/guardar_cliente', methods=['POST'])
def guardar_cliente_route():
    return guardar_cliente()

# ==================== DEVOLUCIONES ====================
@app.route('/buscar_factura_devolucion', methods=['GET'])
def buscar_factura_devolucion():
    invoice_num = request.args.get('invoice', '')
    if not invoice_num:
        return jsonify({'error': 'Número de factura requerido'}), 400
    try:
        sale = execute_query("""
            SELECT id, invoice_number, date, client_name, client_id,
                   client_phone, client_address, items, total_usd, total_bs,
                   user_id, subtotal, tax, discount
            FROM sales
            WHERE invoice_number = %s AND invoice_number > 0
        """, (invoice_num,), fetch_one=True)
        if not sale:
            return jsonify({'error': f'Factura N° {invoice_num} no encontrada'}), 404
        subtotal = float(sale.get('subtotal', 0) or 0)
        discount = float(sale.get('discount', 0) or 0)
        total_usd_guardado = float(sale.get('total_usd', 0) or 0)
        
        if total_usd_guardado > 0:
            total_factura = total_usd_guardado
        else:
            total_factura = subtotal - discount
            if total_factura <= 0:
                total_factura = subtotal - discount
                
        existing_returns = execute_query("""
            SELECT COUNT(*) as count, COALESCE(SUM(total_usd),0) as total_devuelto
            FROM returns WHERE original_invoice = %s
        """, (invoice_num,), fetch_one=True)
        total_devuelto = float(existing_returns.get('total_devuelto', 0) or 0)
        count_returns = int(existing_returns.get('count', 0) or 0)
        disponible = max(0, total_factura - total_devuelto)
        items = []
        if sale.get('items'):
            try:
                items = json.loads(sale['items']) if isinstance(sale['items'], str) else sale['items']
            except:
                items = []
        for item in items:
            item['price'] = float(item.get('price', 0))
            item['quantity'] = float(item.get('quantity', 0))
            item['available_return'] = item['quantity']
        return jsonify({
            'sale': {
                'id': sale['id'], 'invoice_number': sale['invoice_number'],
                'date': sale['date'], 'client_name': sale['client_name'],
                'client_id': sale['client_id'], 'total_usd': total_factura,
                'total_bs': float(sale.get('total_bs', 0) or 0),
                'subtotal': subtotal, 'tax': 0, 'discount': discount
            },
            'items': items, 'total_devuelto': total_devuelto,
            'count_returns': count_returns, 'disponible': disponible
        })
    except Exception as e:
        logger.error(f"Error en buscar_factura_devolucion: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/procesar_devolucion', methods=['POST'])
def procesar_devolucion():
    data = request.json
    invoice_number = data.get('invoice_number')
    items = data.get('items', [])
    reason = data.get('reason', '')
    if not invoice_number or not items:
        return jsonify({'error': 'Datos incompletos'}), 400
    try:
        sale = execute_query("SELECT * FROM sales WHERE invoice_number = %s AND invoice_number > 0",
                            (invoice_number,), fetch_one=True)
        if not sale:
            return jsonify({'error': 'Factura no encontrada'}), 404
        total_usd_guardado = float(sale.get('total_usd', 0) or 0)
        subtotal = float(sale.get('subtotal', 0) or 0)
        discount = float(sale.get('discount', 0) or 0)
        
        if total_usd_guardado > 0:
            total_factura = total_usd_guardado
        else:
            total_factura = subtotal - discount
            
        existing_returns = execute_query("""
            SELECT COALESCE(SUM(total_usd),0) as total_devuelto
            FROM returns WHERE original_invoice = %s
        """, (invoice_number,), fetch_one=True)
        total_devuelto = float(existing_returns.get('total_devuelto', 0) or 0)
        disponible = max(0, total_factura - total_devuelto)
        subtotal_devolucion = sum(float(item['price']) * float(item['quantity']) for item in items)
        
        iva_devolucion = 0
        total_usd_devolucion = subtotal_devolucion
        
        if total_usd_devolucion > disponible + 0.01:
            return jsonify({
                'error': f'El monto a devolver (${total_usd_devolucion:.2f}) excede el disponible (${disponible:.2f})',
                'disponible': disponible, 'subtotal': subtotal_devolucion,
                'total_usd': total_usd_devolucion
            }), 400
        rate = get_cached_exchange_rate()
        total_bs_devolucion = total_usd_devolucion * rate
        next_return = obtener_proximo_return_number()
        items_json = json.dumps(items)
        empresa_id = session.get('empresa_id', 1)
        
        execute_query("""
            INSERT INTO returns
            (return_number, original_invoice, return_date, user_id,
             client_id, client_name, items, subtotal, total_usd, total_bs, reason, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (next_return, invoice_number, datetime.now().isoformat(), session.get('user_id', 1),
              sale['client_id'], sale['client_name'], items_json,
              subtotal_devolucion, total_usd_devolucion, total_bs_devolucion, reason, empresa_id), commit=True)
        devolucion = execute_query("SELECT id FROM returns WHERE return_number = %s",
                                  (next_return,), fetch_one=True)
        devolucion_id = devolucion['id'] if devolucion else None
        registrar_movimiento_caja(
            tipo='egreso', monto_bs=total_bs_devolucion, monto_usd=total_usd_devolucion,
            concepto=f'Devolución N° {next_return} - Factura N° {invoice_number}',
            referencia_tipo='devolucion', referencia_id=next_return, metodo='efectivo',
            usuario=session.get('username', 'SISTEMA'),
            nota=f"Devolución de factura N° {invoice_number}. Motivo: {reason}",
            tasa=rate
        )
        if devolucion_id:
            for item in items:
                subtotal_item = float(item['price']) * float(item['quantity'])
                iva_item = 0
                total_item_usd = subtotal_item
                total_item_bs = total_item_usd * rate
                execute_query("""
                    INSERT INTO movimientos_devolucion
                    (devolucion_id, producto_id, cantidad, precio_unitario,
                     subtotal, iva, total_usd, total_bs, motivo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (devolucion_id, item['id'], item['quantity'], item['price'],
                      subtotal_item, iva_item, total_item_usd, total_item_bs, reason), commit=True)
        for item in items:
            product = execute_query("SELECT id, name, stock, activo FROM products WHERE id = %s",
                                   (item['id'],), fetch_one=True)
            if product:
                if product.get('activo') == 0:
                    execute_query("UPDATE products SET activo = 1 WHERE id = %s",
                                 (item['id'],), commit=True)
                new_stock = float(product['stock']) + float(item['quantity'])
                if new_stock <= 0:
                    new_status = 'AGOTADO'
                elif new_stock <= 5:
                    new_status = 'STOCK BAJO'
                else:
                    new_status = 'DISPONIBLE'
                execute_query("UPDATE products SET stock = %s, stock_status = %s, activo = 1 WHERE id = %s",
                             (new_stock, new_status, item['id']), commit=True)
        fecha_actual = datetime.now().isoformat()
        items_negativos_json = json.dumps([{**item, 'quantity': -float(item['quantity'])} for item in items])
        execute_query("""
            INSERT INTO sales
            (invoice_number, date, user_id, client_id, client_name,
             client_phone, client_address, items, subtotal, discount, tax,
             total_usd, total_bs, payment_method, payment_details,
             pago_efectivo_bs, pago_efectivo_usd, pago_zelle, pago_movil,
             pago_transferencia, pago_tarjeta, cambio_usd, cambio_bs, notes, empresa_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (-next_return, fecha_actual, session.get('user_id', 1), sale['client_id'],
              sale['client_name'], sale.get('client_phone', '') or '', sale.get('client_address', '') or '',
              items_negativos_json, -subtotal_devolucion, 0, 0,
              -total_usd_devolucion, -total_bs_devolucion,
              f"DEVOLUCIÓN N° {next_return} - {reason}",
              json.dumps({"devolucion": True, "return_number": next_return}),
              -total_bs_devolucion, 0, 0, 0, 0, 0, 0, 0,
              f"Devolución de factura N° {invoice_number}", empresa_id), commit=True)
        log_action(session.get('user_id', 1), session.get('username', 'SISTEMA'), 'RETURN',
                   f"Devolución N° {next_return} - Factura N° {invoice_number} - Total: -${total_usd_devolucion:.2f} (sin IVA)")
        saldo = obtener_saldo_actual()
        return jsonify({
            'success': True, 'return_number': next_return,
            'total_usd': total_usd_devolucion, 'total_bs': total_bs_devolucion,
            'saldo_bs': saldo['bs'], 'saldo_usd': saldo['usd'],
            'message': f'Devolución N° {next_return} procesada. Se devolvieron ${total_usd_devolucion:.2f} USD (sin IVA)'
        })
    except Exception as e:
        logger.error(f"Error en procesar_devolucion: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/buscar_nota_credito', methods=['GET'])
def buscar_nota_credito():
    nc_number = request.args.get('nc')
    if not nc_number:
        return jsonify({'error': 'Número de nota de devolución requerido'}), 400
    try:
        ret = execute_query("""
            SELECT return_number, original_invoice, client_name, total_usd, created_at
            FROM returns WHERE return_number = %s
        """, (nc_number,), fetch_one=True)
        if not ret:
            return jsonify({'error': f'Nota de devolución N° {nc_number} no encontrada'}), 404
        usado = execute_query("""
            SELECT COALESCE(SUM(total_usd), 0) as usado
            FROM sales
            WHERE payment_details LIKE %s AND total_usd < 0
        """, (f'%return_number":{nc_number}%',), fetch_one=True)
        usado = float(usado.get('usado', 0) or 0)
        total = float(ret.get('total_usd', 0) or 0)
        disponible = max(0, total - usado)
        return jsonify({
            'success': True, 'numero': ret['return_number'],
            'cliente': ret['client_name'], 'cliente_nombre': ret['client_name'],
            'monto_total': total, 'monto_usado': usado, 'disponible': disponible,
            'fecha_emision': ret['created_at'].strftime('%Y-%m-%d') if ret.get('created_at') else ''
        })
    except Exception as e:
        logger.error(f"Error en buscar_nota_credito: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/historial_devoluciones')
@login_required
@permission_required('view_sales')
def historial_devoluciones():
    try:
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = """
            SELECT r.*, u.username as user_name
            FROM returns r
            LEFT JOIN users u ON r.user_id = u.id
        """
        
        if user_role != 'Dueño':
            query += f" WHERE r.empresa_id = {user_empresa}"
            
        query += " ORDER BY r.created_at DESC LIMIT 100"
        
        returns = execute_query(query, fetch_all=True)
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        for ret in returns:
            try:
                ret['items_list'] = json.loads(ret['items']) if ret['items'] else []
            except:
                ret['items_list'] = []
            ret['total_usd'] = float(ret.get('total_usd', 0) or 0)
            ret['total_bs'] = float(ret.get('total_bs', 0) or 0)
            ret['return_number'] = int(ret.get('return_number', 0) or 0)
            ret['original_invoice'] = int(ret.get('original_invoice', 0) or 0)
    except Exception as e:
        logger.error(f"Error en historial_devoluciones: {e}")
        returns = []
        company = None
    return render_template('historial_devoluciones.html', returns=returns, company=company, datetime=datetime)

@app.route('/imprimir_devolucion/<int:return_number>')
@login_required
def imprimir_devolucion(return_number):
    try:
        devolucion = execute_query("""
            SELECT r.*, u.username as user_name
            FROM returns r
            LEFT JOIN users u ON r.user_id = u.id
            WHERE r.return_number = %s
        """, (return_number,), fetch_one=True)
        if not devolucion:
            flash('Devolución no encontrada', 'danger')
            return redirect(url_for('historial_devoluciones'))
        sale_original = execute_query("""
            SELECT client_phone, client_address
            FROM sales WHERE invoice_number = %s AND invoice_number > 0
        """, (devolucion['original_invoice'],), fetch_one=True)
        items = json.loads(devolucion['items']) if devolucion['items'] else []
        
        tasa_iva = 0
        subtotal_usd = 0.0
        for item in items:
            precio = float(item.get('price', 0))
            cantidad = float(item.get('quantity', 0))
            subtotal_usd += precio * cantidad
        iva_usd = 0
        total_usd = subtotal_usd
        
        for item in items:
            precio = float(item.get('price', 0))
            cantidad = float(item.get('quantity', 0))
            item['price_without_tax'] = precio
            item['tax_amount'] = 0
            item['total_with_tax'] = precio * cantidad
            if 'sale_type' not in item:
                product = execute_query("SELECT sale_type FROM products WHERE id = %s", (item.get('id'),), fetch_one=True)
                item['sale_type'] = product['sale_type'] if product else 'unit'
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        rate = get_cached_exchange_rate()
        total_bs = total_usd * rate
        saldo = obtener_saldo_actual()
        return render_template('devolucion.html',
                               return_number=devolucion['return_number'],
                               original_invoice=devolucion['original_invoice'],
                               client_name=devolucion['client_name'],
                               client_id=devolucion['client_id'],
                               client_phone=sale_original['client_phone'] if sale_original else 'No registrado',
                               client_address=sale_original['client_address'] if sale_original else 'No especificada',
                               items=items, subtotal_usd=subtotal_usd, iva_usd=iva_usd,
                               total_usd=total_usd, total_bs=total_bs,
                               tasa_iva=int(tasa_iva), reason=devolucion['reason'],
                               current_date=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                               company=company, session=session,
                               saldo_bs=saldo['bs'], saldo_usd=saldo['usd'])
    except Exception as e:
        logger.error(f"Error en imprimir_devolucion: {e}")
        import traceback
        traceback.print_exc()
        flash(f'Error al cargar devolución: {str(e)}', 'danger')
        return redirect(url_for('historial_devoluciones'))

# ==================== INVENTARIO ====================
@app.route('/inventory')
@login_required
@permission_required('inventory')
def inventory():
    try:
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = "SELECT id, barcode, name, category, unit, sale_type, COALESCE(cost,0) as cost, COALESCE(margin,0) as margin, COALESCE(price,0) as price, COALESCE(stock,0) as stock, COALESCE(almacen, 'Tienda 1') as almacen, COALESCE(arrival_date,'') as arrival_date, CASE WHEN stock <= 0 THEN 'AGOTADO' WHEN stock <= 5 THEN 'STOCK BAJO' ELSE 'DISPONIBLE' END as stock_status, empresa_id FROM products WHERE activo = 1"
        
        # Si NO es Dueño, solo ve los de su empresa
        if user_role != 'Dueño':
            query += f" AND empresa_id = {user_empresa}"
            
        query += " ORDER BY stock ASC, name"
        products = execute_query(query, fetch_all=True)
        
        categories = execute_query("SELECT name FROM categories ORDER BY name", fetch_all=True)
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        for p in products:
            p['cost'] = float(p['cost']) if p['cost'] else 0
            p['price'] = float(p['price']) if p['price'] else 0
            p['stock'] = float(p['stock']) if p['stock'] else 0
            p['sale_type'] = p.get('sale_type', 'unit')
    except Exception as e:
        logger.error(f"Error en inventory: {e}")
        flash(f'Error al cargar inventario: {e}', 'danger')
        products = []
        categories = []
        company = None
    return render_template('inventory.html', products=products, categories=categories, company=company)

@app.route('/add_product', methods=['POST'])
@login_required
@permission_required('edit_products')
def add_product():
    try:
        name = request.form['name'].upper()
        barcode = request.form.get('barcode', '').strip()
        category = request.form['category']
        unit = request.form['unit']
        sale_type = request.form.get('sale_type', 'unit')
        almacen = request.form.get('almacen', 'Tienda 1')
        cost = float(request.form['cost'])
        margin = float(request.form['margin'])
        stock = float(request.form['stock'])
        arrival_date = request.form.get('arrival_date', '')
        price = cost * (1 + margin / 100)
        empresa_id = session.get('empresa_id', 1) # <--- NUEVO
        
        if barcode:
            existing = execute_query("SELECT id, name, stock, activo FROM products WHERE barcode = %s AND empresa_id = %s", (barcode, empresa_id), fetch_one=True)
            if existing:
                new_stock = float(existing['stock']) + stock
                status = 'AGOTADO' if new_stock <= 0 else 'STOCK BAJO' if new_stock <= 5 else 'DISPONIBLE'
                execute_query("UPDATE products SET name = %s, category = %s, unit = %s, sale_type = %s, almacen = %s, cost = %s, margin = %s, price = %s, stock = stock + %s, arrival_date = %s, activo = 1, stock_status = %s WHERE barcode = %s AND empresa_id = %s", (name, category, unit, sale_type, almacen, cost, margin, price, stock, arrival_date, status, barcode, empresa_id), commit=True)
                flash(f'[OK] Producto {name} reactivado con stock actualizado en {almacen}', 'success')
            else:
                status = 'AGOTADO' if stock <= 0 else 'STOCK BAJO' if stock <= 5 else 'DISPONIBLE'
                execute_query("INSERT INTO products (barcode, name, category, unit, sale_type, almacen, cost, margin, price, stock, arrival_date, stock_status, activo, empresa_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)", (barcode, name, category, unit, sale_type, almacen, cost, margin, price, stock, arrival_date, status, empresa_id), commit=True)
                flash(f'[OK] Producto {name} agregado correctamente a {almacen}', 'success')
        else:
            status = 'AGOTADO' if stock <= 0 else 'STOCK BAJO' if stock <= 5 else 'DISPONIBLE'
            execute_query("INSERT INTO products (barcode, name, category, unit, sale_type, almacen, cost, margin, price, stock, arrival_date, stock_status, activo, empresa_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)", (barcode, name, category, unit, sale_type, almacen, cost, margin, price, stock, arrival_date, status, empresa_id), commit=True)
            flash(f'[OK] Producto {name} agregado correctamente a {almacen}', 'success')
        log_action(session['user_id'], session['username'], 'ADD_PRODUCT', f"Producto: {name} - Almacén: {almacen} - Empresa: {empresa_id}")
    except Exception as e:
        logger.error(f"Error en add_product: {e}")
        flash(f'[ERROR] Error al agregar producto: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

@app.route('/add_category', methods=['POST'])
@login_required
def add_category():
    name = request.form['category_name'].strip().capitalize()
    if name:
        try:
            execute_query("INSERT INTO categories (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,), commit=True)
            flash(f'[OK] Categoría {name} agregada', 'success')
        except Exception as e:
            logger.error(f"Error en add_category: {e}")
            flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

@app.route('/limpiar_agotados')
@login_required
@permission_required('edit_products')
def limpiar_agotados():
    eliminados = limpiar_o_reactivar_productos_agotados()
    if eliminados > 0:
        flash(f'[OK] Se eliminaron {eliminados} productos agotados automáticamente', 'success')
    else:
        flash('[INFO] No se encontraron productos agotados', 'info')
    return redirect(url_for('inventory'))

# ==================== CIERRE DIARIO / CAJA ====================
@app.route('/daily_closure')
@login_required
@permission_required('reports')
def daily_closure():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        sales = execute_query("""
            SELECT s.*, u.username as user_name
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.date LIKE %s AND s.invoice_number > 0
            ORDER BY s.id DESC
        """, (today + '%',), fetch_all=True)
        total_efectivo_bs = total_efectivo_usd = total_zelle = total_pago_movil = 0
        total_transferencia = total_tarjeta = total_usd = total_bs = 0
        for sale in sales:
            total_efectivo_bs += float(sale.get('pago_efectivo_bs') or 0)
            total_efectivo_usd += float(sale.get('pago_efectivo_usd') or 0)
            total_zelle += float(sale.get('pago_zelle') or 0)
            total_pago_movil += float(sale.get('pago_movil') or 0)
            total_transferencia += float(sale.get('pago_transferencia') or 0)
            total_tarjeta += float(sale.get('pago_tarjeta') or 0)
            total_usd += float(sale.get('total_usd') or 0)
            total_bs += float(sale.get('total_bs') or 0)
        return render_template('daily_closure.html',
                              sales=sales, total_efectivo_bs=total_efectivo_bs,
                              total_efectivo_usd=total_efectivo_usd, total_zelle=total_zelle,
                              total_pago_movil=total_pago_movil, total_transferencia=total_transferencia,
                              total_tarjeta=total_tarjeta, total_usd=total_usd, total_bs=total_bs,
                              today=today)
    except Exception as e:
        logger.error(f"Error en daily_closure: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
        return render_template('daily_closure.html', sales=[], total_efectivo_bs=0,
                              total_efectivo_usd=0, total_zelle=0, total_pago_movil=0,
                              total_transferencia=0, total_tarjeta=0, total_usd=0, total_bs=0, today='')

# ==================== CIERRE DE CAJA ====================
@app.route('/cash_closure', methods=['GET', 'POST'])
@login_required
def cash_closure():
    if request.method == 'POST':
        try:
            opening_balance = float(request.form.get('opening_balance', 0))
            total_efectivo_bs = float(request.form.get('total_efectivo_bs', 0))
            total_efectivo_usd = float(request.form.get('total_efectivo_usd', 0))
            total_zelle = float(request.form.get('total_zelle', 0))
            total_pago_movil = float(request.form.get('total_pago_movil', 0))
            total_transferencia = float(request.form.get('total_transferencia', 0))
            total_tarjeta = float(request.form.get('total_tarjeta', 0))
            total_usd = float(request.form.get('total_usd', 0))
            total_bs = float(request.form.get('total_bs', 0))
            diferencia = float(request.form.get('difference', 0))
            cajero = request.form.get('cashier_name', session.get('username', ''))
            supervisor = request.form.get('supervisor_username', '')
            notes = request.form.get('notes', '')
            empresa_id = session.get('empresa_id', 1)
            
            user = execute_query("SELECT id FROM users WHERE username = %s", (session.get('username'),), fetch_one=True)
            user_id = user['id'] if user else None
            
            query = """
                INSERT INTO cash_closures
                (closure_date, user_id, user_name, cashier_name, supervisor_username,
                 opening_balance, sales_total_bs, sales_total_usd, devoluciones_total_bs, devoluciones_total_usd,
                 total_neto_bs, total_neto_usd, cash_bs_delivered, cash_usd_delivered,
                 zelle_delivered, pago_movil_delivered, transferencia_delivered, tarjeta_delivered,
                 difference, notes, empresa_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            execute_query(query, (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id, session.get('username'), cajero, supervisor,
                opening_balance, total_bs, total_usd, 0, 0,
                total_bs, total_usd, total_efectivo_bs, total_efectivo_usd,
                total_zelle, total_pago_movil, total_transferencia, total_tarjeta,
                diferencia, notes, empresa_id
            ), commit=True)
            
            enviar_cierre_caja_telegram(
                total_usd, total_bs, total_efectivo_bs, total_efectivo_usd,
                total_zelle, total_pago_movil, total_transferencia, total_tarjeta,
                opening_balance, diferencia, cajero, supervisor, get_cached_exchange_rate()
            )
            
            flash('[OK] Cierre de caja registrado correctamente', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Error en cash_closure POST: {e}")
            flash(f'[ERROR] Error al registrar cierre: {str(e)}', 'danger')
            return redirect(url_for('cash_closure'))
    
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        sales = execute_query("""
            SELECT s.*, u.username as user_name
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.date LIKE %s AND s.invoice_number > 0
            ORDER BY s.id DESC
        """, (today + '%',), fetch_all=True)
        total_efectivo_bs = total_efectivo_usd = total_zelle = total_pago_movil = 0
        total_transferencia = total_tarjeta = total_usd = total_bs = 0
        for sale in sales:
            total_efectivo_bs += float(sale.get('pago_efectivo_bs') or 0)
            total_efectivo_usd += float(sale.get('pago_efectivo_usd') or 0)
            total_zelle += float(sale.get('pago_zelle') or 0)
            total_pago_movil += float(sale.get('pago_movil') or 0)
            total_transferencia += float(sale.get('pago_transferencia') or 0)
            total_tarjeta += float(sale.get('pago_tarjeta') or 0)
            total_usd += float(sale.get('total_usd') or 0)
            total_bs += float(sale.get('total_bs') or 0)
        opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
        opening_balance = float(opening_result['value']) if opening_result else 0.0
        rate = get_cached_exchange_rate()
        return render_template('cash_closure.html',
                              sales=sales, total_efectivo_bs=total_efectivo_bs,
                              total_efectivo_usd=total_efectivo_usd, total_zelle=total_zelle,
                              total_pago_movil=total_pago_movil, total_transferencia=total_transferencia,
                              total_tarjeta=total_tarjeta, total_usd=total_usd, total_bs=total_bs,
                              opening_balance=opening_balance, exchange_rate=rate, today=today)
    except Exception as e:
        logger.error(f"Error en cash_closure GET: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
        return render_template('cash_closure.html', sales=[], total_efectivo_bs=0,
                              total_efectivo_usd=0, total_zelle=0, total_pago_movil=0,
                              total_transferencia=0, total_tarjeta=0, total_usd=0, total_bs=0,
                              opening_balance=0, exchange_rate=36.5, today='')

# ==================== APERTURA DE CAJA ====================
@app.route('/opening_balance', methods=['GET', 'POST'])
@login_required
@permission_required('cash_closure')
def opening_balance():
    if request.method == 'POST':
        try:
            opening_balance = float(request.form.get('opening_balance', 0))
            notes = request.form.get('notes', '')
            user_id = session.get('user_id')
            execute_query("""
                INSERT INTO cash_opening
                (opening_date, user_id, user_name, opening_balance, notes)
                VALUES (%s, %s, %s, %s, %s)
            """, (datetime.now().isoformat(), user_id, session.get('username'), opening_balance, notes), commit=True)
            execute_query("UPDATE config SET value = %s WHERE key_name = 'opening_balance'", (str(opening_balance),), commit=True)
            flash('[OK] Apertura de caja registrada', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Error en opening_balance POST: {e}")
            flash(f'[ERROR] Error: {str(e)}', 'danger')
    try:
        opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
        opening_balance = float(opening_result['value']) if opening_result else 0.0
        return render_template('opening_balance.html', opening_balance=opening_balance)
    except Exception as e:
        logger.error(f"Error en opening_balance GET: {e}")
        return render_template('opening_balance.html', opening_balance=0)

# ==================== VENTAS DEL MES ====================
@app.route('/ventas_mes')
@login_required
@permission_required('view_sales')
def ventas_mes():
    try:
        today = datetime.now()
        month = today.month
        year = today.year
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"
            
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = "SELECT * FROM sales WHERE date >= %s AND date < %s AND invoice_number > 0"
        params = [start, end]
        
        if user_role != 'Dueño':
            query += f" AND empresa_id = {user_empresa}"
            
        query += " ORDER BY date DESC"
        
        sales = execute_query(query, tuple(params), fetch_all=True)
        total_usd = sum(float(s.get('total_usd') or 0) for s in sales)
        total_bs = sum(float(s.get('total_bs') or 0) for s in sales)
        return render_template('ventas_mes.html', sales=sales, total_usd=total_usd, total_bs=total_bs, mes=month, year=year)
    except Exception as e:
        logger.error(f"Error en ventas_mes: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
        return render_template('ventas_mes.html', sales=[], total_usd=0, total_bs=0, mes=0, year=0)

# ==================== GANANCIAS ====================
@app.route('/monthly_profit')
@login_required
@permission_required('reports')
def monthly_profit():
    try:
        today = datetime.now()
        month = today.month
        year = today.year
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"
            
        user_role = session.get('role')
        user_empresa = session.get('empresa_id', 1)
        
        query = """
            SELECT s.*, s.items
            FROM sales s
            WHERE s.date >= %s AND s.date < %s AND s.invoice_number > 0
        """
        params = [start, end]
        
        if user_role != 'Dueño':
            query += f" AND s.empresa_id = {user_empresa}"
            
        query += " ORDER BY s.date DESC"
        
        sales = execute_query(query, tuple(params), fetch_all=True)
        total_ingresos_usd = 0
        total_costos_usd = 0
        ventas_detalle = []
        
        for sale in sales:
            if not sale.get('items'):
                continue
            try:
                items = json.loads(sale['items']) if isinstance(sale['items'], str) else sale['items']
            except:
                continue
            if not isinstance(items, list):
                continue
            venta_total = float(sale.get('total_usd', 0) or 0)
            costo_venta = 0
            for item in items:
                product_id = item.get('id')
                cantidad = float(item.get('quantity', 0) or 0)
                if product_id and cantidad > 0:
                    product = execute_query("SELECT cost FROM products WHERE id = %s", (product_id,), fetch_one=True)
                    if product:
                        costo_venta += float(product['cost'] or 0) * cantidad
            total_ingresos_usd += venta_total
            total_costos_usd += costo_venta
            ventas_detalle.append({
                'invoice_number': sale['invoice_number'],
                'date': sale['date'],
                'client_name': sale['client_name'],
                'total_usd': venta_total,
                'costo_usd': costo_venta,
                'ganancia_usd': venta_total - costo_venta
            })
        ganancia_total_usd = total_ingresos_usd - total_costos_usd
        margen_porcentaje = (ganancia_total_usd / total_ingresos_usd * 100) if total_ingresos_usd > 0 else 0
        return render_template('monthly_profit.html',
                              ventas_detalle=ventas_detalle,
                              total_ingresos_usd=total_ingresos_usd,
                              total_costos_usd=total_costos_usd,
                              ganancia_total_usd=ganancia_total_usd,
                              margen_porcentaje=margen_porcentaje,
                              mes=month, year=year)
    except Exception as e:
        logger.error(f"Error en monthly_profit: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
        return render_template('monthly_profit.html', ventas_detalle=[], total_ingresos_usd=0,
                              total_costos_usd=0, ganancia_total_usd=0, margen_porcentaje=0,
                              mes=0, year=0)

# ==================== ROLES Y USUARIOS ====================
@app.route('/manage_roles', methods=['GET', 'POST'])
@login_required
@permission_required('roles')
def manage_roles():
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            permissions = {
                'inventory': 'inventory' in request.form,
                'edit_products': 'edit_products' in request.form,
                'buy': 'buy' in request.form,
                'sell': 'sell' in request.form,
                'users': 'users' in request.form,
                'roles': 'roles' in request.form,
                'reports': 'reports' in request.form,
                'exchange_rate': 'exchange_rate' in request.form,
                'cash_closure': 'cash_closure' in request.form,
                'view_sales': 'view_sales' in request.form
            }
            perms_json = json.dumps(permissions)
            existing = execute_query("SELECT name FROM roles WHERE name = %s", (name,), fetch_one=True)
            if existing:
                execute_query("UPDATE roles SET permissions = %s WHERE name = %s", (perms_json, name), commit=True)
                flash(f'[OK] Rol {name} actualizado', 'success')
            else:
                execute_query("INSERT INTO roles (name, permissions) VALUES (%s, %s)", (name, perms_json), commit=True)
                flash(f'[OK] Rol {name} creado', 'success')
        except Exception as e:
            logger.error(f"Error en manage_roles POST: {e}")
            flash(f'[ERROR] Error: {str(e)}', 'danger')
        return redirect(url_for('manage_roles'))
    try:
        roles = execute_query("SELECT name, permissions FROM roles ORDER BY name", fetch_all=True)
        for role in roles:
            try:
                role['permissions_dict'] = json.loads(role['permissions']) if role['permissions'] else {}
            except:
                role['permissions_dict'] = {}
    except Exception as e:
        logger.error(f"Error en manage_roles GET: {e}")
        roles = []
    return render_template('manage_roles.html', roles=roles)

@app.route('/manage_users', methods=['GET', 'POST'])
@login_required
@permission_required('users')
def manage_users():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            email = request.form.get('email')
            password = request.form.get('password')
            role = request.form.get('role')
            empresa_id = int(request.form.get('empresa_id', 1))
            if not username or not password or not role:
                flash('[ERROR] Usuario, contraseña y rol son obligatorios', 'danger')
                return redirect(url_for('manage_users'))
            errores = validar_contraseña_segura(password)
            if errores:
                for error in errores:
                    flash(f'[ERROR] {error}', 'danger')
                return redirect(url_for('manage_users'))
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            existing = execute_query("SELECT id FROM users WHERE username = %s", (username,), fetch_one=True)
            if existing:
                flash('[ERROR] El nombre de usuario ya existe', 'danger')
            else:
                execute_query("INSERT INTO users (username, email, password, role, empresa_id) VALUES (%s, %s, %s, %s, %s)",
                             (username, email, hashed, role, empresa_id), commit=True)
                flash(f'[OK] Usuario {username} creado correctamente', 'success')
                log_action(session['user_id'], session['username'], 'ADD_USER', f"Usuario: {username}, Rol: {role}, Empresa: {empresa_id}")
        except Exception as e:
            logger.error(f"Error en manage_users POST: {e}")
            flash(f'[ERROR] Error: {str(e)}', 'danger')
        return redirect(url_for('manage_users'))
    try:
        users = execute_query("SELECT id, username, email, role, empresa_id, failed_attempts, locked_until FROM users ORDER BY id", fetch_all=True)
        roles = execute_query("SELECT name FROM roles ORDER BY name", fetch_all=True)
    except Exception as e:
        logger.error(f"Error en manage_users GET: {e}")
        users = []
        roles = []
    return render_template('manage_users.html', users=users, roles=roles)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
@permission_required('users')
def delete_user(user_id):
    try:
        if user_id == session.get('user_id'):
            flash('[ERROR] No puede eliminar su propio usuario', 'danger')
            return redirect(url_for('manage_users'))
        execute_query("DELETE FROM users WHERE id = %s", (user_id,), commit=True)
        flash('[OK] Usuario eliminado correctamente', 'success')
        log_action(session['user_id'], session['username'], 'DELETE_USER', f"Usuario ID: {user_id}")
    except Exception as e:
        logger.error(f"Error en delete_user: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('manage_users'))

# ==================== TASA DE CAMBIO ====================
@app.route('/exchange_rate', methods=['GET', 'POST'])
@login_required
@permission_required('exchange_rate')
def exchange_rate():
    if request.method == 'POST':
        try:
            new_rate = float(request.form.get('new_rate', 0))
            if new_rate <= 0:
                flash('[ERROR] La tasa debe ser mayor a cero', 'danger')
                return redirect(url_for('exchange_rate'))
            old_result = execute_query("SELECT value FROM config WHERE key_name = 'exchange_rate'", fetch_one=True)
            old_rate = float(old_result['value']) if old_result else 0
            execute_query("UPDATE config SET value = %s WHERE key_name = 'exchange_rate'", (str(new_rate),), commit=True)
            global _cache_exchange_rate
            _cache_exchange_rate = {'rate': None, 'timestamp': 0}
            execute_query(
                """INSERT INTO exchange_rate_history
                (tasa_anterior, tasa_nueva, descuento, usuario, fecha)
                VALUES (%s, %s, %s, %s, %s)""",
                (old_rate, new_rate, 0, session['username'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                commit=True
            )
            flash(f'[OK] Tasa actualizada: {old_rate} → {new_rate} Bs/USD', 'success')
            log_action(session['user_id'], session['username'], 'UPDATE_RATE', f"Tasa: {old_rate} → {new_rate}")
        except Exception as e:
            logger.error(f"Error en exchange_rate POST: {e}")
            flash(f'[ERROR] Error: {str(e)}', 'danger')
        return redirect(url_for('exchange_rate'))
    try:
        rate = get_cached_exchange_rate()
        history = execute_query("SELECT * FROM exchange_rate_history ORDER BY created_at DESC LIMIT 20", fetch_all=True)
    except Exception as e:
        logger.error(f"Error en exchange_rate GET: {e}")
        rate = 36.50
        history = []
    return render_template('exchange_rate.html', exchange_rate=rate, history=history)

# ==================== BITÁCORA ====================
@app.route('/bitacora')
@login_required
@permission_required('reports')
def bitacora():
    try:
        logs = execute_query("SELECT * FROM user_logs ORDER BY created_at DESC LIMIT 100", fetch_all=True)
    except Exception as e:
        logger.error(f"Error en bitacora: {e}")
        logs = []
    return render_template('bitacora.html', logs=logs)

# ==================== PRODUCTOS ELIMINADOS ====================
@app.route('/deleted_products')
@login_required
@permission_required('edit_products')
def deleted_products():
    try:
        products = execute_query("SELECT * FROM deleted_products ORDER BY fecha_eliminacion DESC", fetch_all=True)
    except Exception as e:
        logger.error(f"Error en deleted_products: {e}")
        products = []
    return render_template('deleted_products.html', products=products)

@app.route('/restore_product/<int:product_id>', methods=['POST'])
@login_required
@permission_required('edit_products')
def restore_product(product_id):
    try:
        product = execute_query("SELECT * FROM deleted_products WHERE id = %s", (product_id,), fetch_one=True)
        if not product:
            flash('[ERROR] Producto no encontrado', 'danger')
            return redirect(url_for('deleted_products'))
        execute_query("""
            INSERT INTO products
            (barcode, name, category, unit, stock, cost, price, margin, arrival_date, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
        """, (product['barcode'], product['name'], product['category'], product['unit'],
              product['stock'], product['cost'], product['price'], product['margin'],
              product['arrival_date']), commit=True)
        execute_query("DELETE FROM deleted_products WHERE id = %s", (product_id,), commit=True)
        flash(f'[OK] Producto {product["name"]} restaurado', 'success')
        log_action(session['user_id'], session['username'], 'RESTORE_PRODUCT', f"Producto: {product['name']}")
    except Exception as e:
        logger.error(f"Error en restore_product: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('deleted_products'))

# ==================== EDITAR PRODUCTO ====================
@app.route('/edit_product', methods=['POST'])
@login_required
@permission_required('edit_products')
def edit_product():
    try:
        product_id = request.form.get('product_id')
        name = request.form['name'].upper()
        barcode = request.form.get('barcode', '').strip()
        category = request.form['category']
        unit = request.form['unit']
        sale_type = request.form.get('sale_type', 'unit')
        almacen = request.form.get('almacen', 'Tienda 1')
        cost = float(request.form['cost'])
        margin = float(request.form['margin'])
        price = float(request.form['price'])
        stock = float(request.form['stock'])
        arrival_date = request.form.get('arrival_date', '')
        status = 'AGOTADO' if stock <= 0 else 'STOCK BAJO' if stock <= 5 else 'DISPONIBLE'
        execute_query("""
            UPDATE products
            SET name = %s, barcode = %s, category = %s, unit = %s, sale_type = %s,
                almacen = %s, cost = %s, margin = %s, price = %s, stock = %s,
                arrival_date = %s, stock_status = %s
            WHERE id = %s
        """, (name, barcode, category, unit, sale_type, almacen, cost, margin, price, stock, arrival_date, status, product_id), commit=True)
        flash(f'[OK] Producto {name} actualizado correctamente', 'success')
        log_action(session['user_id'], session['username'], 'EDIT_PRODUCT', f"Producto: {name}")
    except Exception as e:
        logger.error(f"Error en edit_product: {e}")
        flash(f'[ERROR] Error al actualizar producto: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

# ==================== ELIMINAR PRODUCTO ====================
@app.route('/delete_product', methods=['POST'])
@login_required
@permission_required('edit_products')
def delete_product():
    try:
        product_id = request.form.get('product_id')
        motivo = request.form.get('motivo_eliminacion', '').strip()
        if not motivo:
            flash('[ERROR] El motivo de eliminación es obligatorio', 'danger')
            return redirect(url_for('inventory'))
        product = execute_query("SELECT * FROM products WHERE id = %s", (product_id,), fetch_one=True)
        if not product:
            flash('[ERROR] Producto no encontrado', 'danger')
            return redirect(url_for('inventory'))
        execute_query("""
            INSERT INTO deleted_products
            (original_id, barcode, name, category, unit, stock, cost, price, margin, arrival_date, motivo_eliminacion, usuario_elimino)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (product['id'], product['barcode'], product['name'], product['category'],
              product['unit'], product['stock'], product['cost'], product['price'],
              product['margin'], product['arrival_date'], motivo, session['username']), commit=True)
        execute_query("DELETE FROM products WHERE id = %s", (product_id,), commit=True)
        flash(f'[OK] Producto {product["name"]} eliminado correctamente', 'success')
        log_action(session['user_id'], session['username'], 'DELETE_PRODUCT', f"Producto: {product['name']} - Motivo: {motivo}")
    except Exception as e:
        logger.error(f"Error en delete_product: {e}")
        flash(f'[ERROR] Error al eliminar producto: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

# ==================== CORREGIR STOCKS NEGATIVOS ====================
@app.route('/corregir_stocks_negativos')
@login_required
@permission_required('edit_products')
def corregir_stocks_negativos():
    try:
        execute_query("UPDATE products SET stock = 0, stock_status = 'AGOTADO' WHERE stock < 0", commit=True)
        flash('[OK] Stocks negativos corregidos a 0', 'success')
        log_action(session['user_id'], session['username'], 'FIX_NEGATIVE_STOCKS', 'Stocks negativos corregidos')
    except Exception as e:
        logger.error(f"Error en corregir_stocks_negativos: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

# ==================== INICIO ====================
if __name__ == '__main__':
    with app.app_context():
        init_db()
        if TELEGRAM_TOKEN and TELEGRAM_TOKEN != "TU_TOKEN_AQUI":
            threading.Thread(target=_escuchar_telegram, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
