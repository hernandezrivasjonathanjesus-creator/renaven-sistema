from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response, g, send_file
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail, Message
from flask_compress import Compress
import mysql.connector
from mysql.connector import Error, pooling
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
from dotenv import load_dotenv
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

_db_initialized = False

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
    """Ruta válida en desarrollo y dentro del .exe de PyInstaller."""
    try:
        base = sys._MEIPASS  # carpeta temporal donde PyInstaller extrae los datas
    except AttributeError:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)

if getattr(sys, 'frozen', False):
    # Estamos dentro del .exe
    base_dir    = os.path.dirname(sys.executable)   # junto al .exe (persistente)
    bundle_dir  = sys._MEIPASS                       # dentro del .exe (temporal)
else:
    base_dir    = os.path.dirname(os.path.abspath(__file__))
    bundle_dir  = base_dir

# Log en carpeta persistente (junto al .exe en modo frozen)
log_path_file = os.path.join(base_dir, 'renaven.log')
json_formatter = JsonFormatter()
try:
    file_handler = RotatingFileHandler(log_path_file, maxBytes=10485760, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(json_formatter)
    logger_handlers = [file_handler]
except Exception:
    logger_handlers = []

# En modo .exe (console=False), sys.stdout puede ser None
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

# MySQL: motor dentro del bundle, datos persistentes junto al .exe
MYSQL_DIR      = os.path.join(bundle_dir, 'mysql_server')
MYSQL_DATA_DIR = os.path.join(base_dir, 'mysql_data')

app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
app.secret_key = 'renaven-super-secret-key-2024'

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

# ==================== CONFIGURACIÓN DE MYSQL ====================
DB_CONFIG = {
    'host': 'sql10.freesqldatabase.com',
    'port': 3306,
    'database': 'sql1083865',
    'user': 'sql1083865',
    'password': 'bsyx1YZ58X',
    'autocommit': False,
    'use_pure': True,
    'connection_timeout': 30,
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci',
    'consume_results': True
}

connection_pool = None

def ensure_database_exists():
    """Crea la base de datos si no existe (evita error 1049 al arrancar el .exe)."""
    cfg = {
        "host":     DB_CONFIG.get("host", "127.0.0.1"),
        "port":     DB_CONFIG.get("port", 3306),
        "user":     DB_CONFIG.get("user", "root"),
        "password": DB_CONFIG.get("password", ""),
        "use_pure": True,
        "connection_timeout": 30,
    }
    db_name = DB_CONFIG["database"]
    try:
        conn = mysql.connector.connect(**cfg)
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Base de datos '{db_name}' verificada/creada.")
    except Exception as e:
        print(f"[ERROR] No se pudo crear/verificar la DB: {e}")
        raise

def init_connection_pool():
    global connection_pool
    try:
        connection_pool = pooling.MySQLConnectionPool(
            pool_name="renaven_pool",
            pool_size=10,
            pool_reset_session=True,
            **DB_CONFIG
        )
        logger.info("[OK] Pool de conexiones MySQL iniciado (10 conexiones)")
        print("[OK] Pool de conexiones MySQL iniciado")
    except Error as e:
        logger.error(f"Error al crear pool: {e}")
        raise

def get_db():
    try:
        conn = connection_pool.get_connection()
        cursor = conn.cursor(dictionary=True, buffered=True)
        return conn, cursor
    except Error as e:
        logger.error(f"Error de conexión MySQL: {e}")
        raise

def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    conn = None
    cursor = None
    try:
        conn, cursor = get_db()
        cursor.execute(query, params or ())
        if commit:
            conn.commit()
            try:
                while cursor.nextset():
                    pass
            except:
                pass
            return cursor.rowcount
        elif fetch_one:
            result = cursor.fetchone()
            try:
                while cursor.nextset():
                    pass
            except:
                pass
            return result
        elif fetch_all:
            result = cursor.fetchall()
            if result is None:
                result = []
            try:
                while cursor.nextset():
                    pass
            except:
                pass
            return result
        else:
            try:
                cursor.fetchall()
                while cursor.nextset():
                    pass
            except:
                pass
            return True
    except Error as e:
        logger.error(f"Error en query: {e}")
        if fetch_all:
            return []
        raise
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
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
    ancho = 130
    alto = 40
    imagen = Image.new('RGB', (ancho, alto), color=(255, 255, 255))
    draw = ImageDraw.Draw(imagen)
    try:
        fuente = ImageFont.truetype("arial.ttf", 18)
    except:
        fuente = ImageFont.load_default()
    colores = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255),
        (255, 165, 0), (128, 0, 128), (255, 192, 203)
    ]
    x_inicial = 8
    espacio = (ancho - 16) // len(codigo)
    for i, char in enumerate(codigo):
        x = x_inicial + (i * espacio) + random.randint(-3, 3)
        y = random.randint(8, 22)
        color = random.choice(colores)
        draw.text((x, y), char, font=fuente, fill=color)
    for _ in range(random.randint(3, 6)):
        x1 = random.randint(0, ancho)
        y1 = random.randint(0, alto)
        x2 = random.randint(0, ancho)
        y2 = random.randint(0, alto)
        draw.line([(x1, y1), (x2, y2)], fill=(100, 100, 100), width=1)
    for _ in range(random.randint(50, 150)):
        x = random.randint(0, ancho)
        y = random.randint(0, alto)
        draw.point((x, y), fill=random.choice(colores))
    imagen = imagen.filter(ImageFilter.SMOOTH)
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

# ==================== CONFIGURACIÓN DE CORREO ====================
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', 'hernandezrivasjonathanjesus@gmail.com')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', 'nkgw nchu sxqf muli')
MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'hernandezrivasjonathanjesus@gmail.com')

app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=MAIL_USERNAME,
    MAIL_PASSWORD=MAIL_PASSWORD,
    MAIL_DEFAULT_SENDER=MAIL_DEFAULT_SENDER,
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=False,
    REMEMBER_COOKIE_HTTPONLY=True,
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
                'pool_conexiones': 10,
                'pool_activo': connection_pool is not None
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
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                    tipo ENUM('ingreso', 'egreso') NOT NULL,
                    monto_bs DECIMAL(10,2) NOT NULL,
                    monto_usd DECIMAL(10,2) NOT NULL,
                    concepto VARCHAR(255) NOT NULL,
                    referencia_tipo VARCHAR(50),
                    referencia_id INT,
                    metodo VARCHAR(50),
                    usuario VARCHAR(100),
                    nota TEXT,
                    tasa_usd DECIMAL(10,4),
                    INDEX idx_fecha (fecha),
                    INDEX idx_tipo (tipo),
                    INDEX idx_referencia (referencia_tipo, referencia_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
                    ultima_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """, commit=True)
            execute_query("INSERT INTO saldo_caja (id, saldo_bs, saldo_usd) VALUES (1, 0, 0) ON DUPLICATE KEY UPDATE id=1", commit=True)
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
                    ultima_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """, commit=True)
            execute_query("INSERT INTO saldo_caja (id, saldo_bs, saldo_usd) VALUES (1, 0, 0) ON DUPLICATE KEY UPDATE id=1", commit=True)
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
            execute_query("""
                CREATE TABLE IF NOT EXISTS movimientos_caja (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                    tipo ENUM('ingreso', 'egreso') NOT NULL,
                    monto_bs DECIMAL(10,2) NOT NULL,
                    monto_usd DECIMAL(10,2) NOT NULL,
                    concepto VARCHAR(255) NOT NULL,
                    referencia_tipo VARCHAR(50),
                    referencia_id INT,
                    metodo VARCHAR(50),
                    usuario VARCHAR(100),
                    nota TEXT,
                    tasa_usd DECIMAL(10,4),
                    INDEX idx_fecha (fecha),
                    INDEX idx_tipo (tipo),
                    INDEX idx_referencia (referencia_tipo, referencia_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """, commit=True)
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

# ==================== FUNCIÓN PARA VERIFICAR COLUMNAS EN CASH_CLOSURES ====================
def verificar_columnas_cash_closures(cursor):
    """Agrega columnas faltantes a la tabla cash_closures si no existen."""
    try:
        cursor.execute("SHOW COLUMNS FROM cash_closures LIKE 'devoluciones_bs'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE cash_closures ADD COLUMN devoluciones_bs DECIMAL(10,2) DEFAULT 0")
            logger.info("[DB] Columna devoluciones_bs agregada a cash_closures")
        cursor.execute("SHOW COLUMNS FROM cash_closures LIKE 'devoluciones_usd'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE cash_closures ADD COLUMN devoluciones_usd DECIMAL(10,2) DEFAULT 0")
            logger.info("[DB] Columna devoluciones_usd agregada a cash_closures")
        cursor.execute("COMMIT")
    except Exception as e:
        logger.warning(f"No se pudieron verificar/agregar columnas en cash_closures: {e}")

# ==================== INICIALIZAR BASE DE DATOS ====================
def init_db():
    global _db_initialized
    if _db_initialized:
        logger.info("Base de datos ya estaba inicializada, omitiendo...")
        return True
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(255),
            password VARCHAR(255) NOT NULL,
            role VARCHAR(50) NOT NULL,
            failed_attempts INT DEFAULT 0,
            locked_until INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS roles (
            name VARCHAR(50) PRIMARY KEY,
            permissions TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS products (
            id INT PRIMARY KEY AUTO_INCREMENT,
            barcode VARCHAR(100),
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100),
            unit VARCHAR(50),
            sale_type VARCHAR(20) DEFAULT 'unit',
            cost DECIMAL(10,2) DEFAULT 0,
            margin DECIMAL(10,2) DEFAULT 0,
            price DECIMAL(10,2) DEFAULT 0,
            stock DECIMAL(10,2) DEFAULT 0,
            arrival_date VARCHAR(50),
            stock_status VARCHAR(20) DEFAULT 'disponible',
            activo TINYINT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_name (name),
            INDEX idx_category (category),
            INDEX idx_barcode (barcode),
            INDEX idx_stock (stock)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS categories (
            name VARCHAR(100) PRIMARY KEY
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS sales (
            id INT PRIMARY KEY AUTO_INCREMENT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_date (date),
            INDEX idx_invoice (invoice_number),
            INDEX idx_user (user_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS purchases (
            id INT PRIMARY KEY AUTO_INCREMENT,
            supplier_invoice VARCHAR(100),
            supplier_name VARCHAR(255),
            date VARCHAR(50),
            arrival_date VARCHAR(50),
            product_id INT,
            product_name VARCHAR(255),
            quantity DECIMAL(10,2),
            unit_cost DECIMAL(10,2),
            total_cost DECIMAL(10,2)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS cash_closures (
            id INT PRIMARY KEY AUTO_INCREMENT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS config (
            key_name VARCHAR(100) PRIMARY KEY,
            value TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS user_logs (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            username VARCHAR(100),
            action VARCHAR(255),
            details TEXT,
            ip_address VARCHAR(50),
            date VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_date (date),
            INDEX idx_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS password_resets (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            token VARCHAR(255),
            expires INT,
            used TINYINT DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS clientes_frecuentes (
            id INT PRIMARY KEY AUTO_INCREMENT,
            cedula VARCHAR(50) UNIQUE NOT NULL,
            nombre VARCHAR(255) NOT NULL,
            telefono VARCHAR(50),
            direccion TEXT NOT NULL,
            ultima_compra VARCHAR(50),
            total_compras INT DEFAULT 1,
            monto_total DECIMAL(10,2) DEFAULT 0,
            INDEX idx_cedula (cedula),
            INDEX idx_nombre (nombre)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS deleted_products (
            id INT PRIMARY KEY AUTO_INCREMENT,
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
            fecha_eliminacion DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS returns (
            id INT PRIMARY KEY AUTO_INCREMENT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_original_invoice (original_invoice),
            INDEX idx_return_number (return_number),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS exchange_rate_history (
            id INT PRIMARY KEY AUTO_INCREMENT,
            tasa_anterior DECIMAL(10,2),
            tasa_nueva DECIMAL(10,2),
            descuento INT,
            usuario VARCHAR(100),
            fecha VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS cash_opening (
            id INT PRIMARY KEY AUTO_INCREMENT,
            opening_date VARCHAR(50),
            user_id INT,
            user_name VARCHAR(100),
            opening_balance DECIMAL(10,2),
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS movimientos_caja (
            id INT PRIMARY KEY AUTO_INCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo ENUM('ingreso', 'egreso') NOT NULL,
            monto_bs DECIMAL(10,2) NOT NULL,
            monto_usd DECIMAL(10,2) NOT NULL,
            concepto VARCHAR(255) NOT NULL,
            referencia_tipo VARCHAR(50),
            referencia_id INT,
            metodo VARCHAR(50),
            usuario VARCHAR(100),
            nota TEXT,
            tasa_usd DECIMAL(10,4),
            INDEX idx_fecha (fecha),
            INDEX idx_tipo (tipo),
            INDEX idx_referencia (referencia_tipo, referencia_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS saldo_caja (
            id INT PRIMARY KEY CHECK (id = 1),
            saldo_bs DECIMAL(10,2) DEFAULT 0,
            saldo_usd DECIMAL(10,2) DEFAULT 0,
            ultima_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS movimientos_devolucion (
            id INT PRIMARY KEY AUTO_INCREMENT,
            devolucion_id INT NOT NULL,
            producto_id INT NOT NULL,
            cantidad DECIMAL(10,2) NOT NULL,
            precio_unitario DECIMAL(10,2) NOT NULL,
            subtotal DECIMAL(10,2) NOT NULL,
            iva DECIMAL(10,2) NOT NULL,
            total_usd DECIMAL(10,2) NOT NULL,
            total_bs DECIMAL(10,2) NOT NULL,
            motivo TEXT NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (devolucion_id) REFERENCES returns(id) ON DELETE CASCADE,
            INDEX idx_devolucion (devolucion_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS notas_credito (
            id INT PRIMARY KEY AUTO_INCREMENT,
            numero VARCHAR(50) UNIQUE NOT NULL,
            cliente_id VARCHAR(50) NOT NULL,
            cliente_nombre VARCHAR(255) NOT NULL,
            monto_total DECIMAL(10,2) NOT NULL,
            monto_usado DECIMAL(10,2) DEFAULT 0,
            fecha_emision DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_vencimiento DATETIME,
            activo TINYINT DEFAULT 1,
            creado_por VARCHAR(100),
            notas TEXT,
            INDEX idx_numero (numero),
            INDEX idx_cliente (cliente_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    ]
    conn = None
    cursor = None
    try:
        conn, cursor = get_db()
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        if tables and len(tables) > 0:
            logger.info("Las tablas ya existen, omitiendo creación...")
            _db_initialized = True
            verificar_columnas_cash_closures(cursor)
            return True
        for query in queries:
            cursor.execute(query)
        conn.commit()
        insert_initial_data(cursor)
        conn.commit()
        cursor.execute("INSERT IGNORE INTO config (key_name, value) VALUES ('next_return', '1')")
        conn.commit()
        cursor.execute("INSERT IGNORE INTO saldo_caja (id, saldo_bs, saldo_usd) VALUES (1, 0, 0)")
        conn.commit()
        _db_initialized = True
        logger.info("Base de datos MySQL inicializada correctamente")
        return True
    except Error as e:
        logger.error(f"Error al inicializar DB: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def insert_initial_data(cursor):
    cursor.execute("SELECT COUNT(*) as count FROM users")
    result = cursor.fetchone()
    if result and result['count'] > 0:
        logger.info("Los datos iniciales ya existen, omitiendo inserción...")
        return
    categorias = ["Herramientas", "Pinturas", "Electricidad", "Fontanería", "Construcción", "Jardinería"]
    for cat in categorias:
        cursor.execute("INSERT IGNORE INTO categories (name) VALUES (%s)", (cat,))
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
        cursor.execute("INSERT IGNORE INTO roles (name, permissions) VALUES (%s, %s)", (name, perms))
    usuarios = [
        ('dueño', 'admin@renaven.com', bcrypt.hashpw('Admin123!@#Renaven'.encode(), bcrypt.gensalt()).decode(), 'Dueño'),
        ('supervisor', 'supervisor@renaven.com', bcrypt.hashpw('Super2024!@#Seguro'.encode(), bcrypt.gensalt()).decode(), 'Supervisor'),
        ('cajera', 'cajera@renaven.com', bcrypt.hashpw('Cajera$2024Segura'.encode(), bcrypt.gensalt()).decode(), 'Cajera'),
        ('almacen', 'almacen@renaven.com', bcrypt.hashpw('Almacen*2024Safe'.encode(), bcrypt.gensalt()).decode(), 'Almacenista'),
        ('jonathan', 'hernandezrivasjonathanjesus@gmail.com', bcrypt.hashpw('Jonathan$2024Secure!'.encode(), bcrypt.gensalt()).decode(), 'Dueño')
    ]
    for username, email, pwd, role in usuarios:
        cursor.execute("INSERT IGNORE INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)",
                    (username, email, pwd, role))
    cursor.execute("INSERT IGNORE INTO company (id, name, rif, address, phone, email, parish, city, state) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s)",
                ('RENAVEN OAKMONT, C.A.', 'J-41323725-3', 'CALLE 1 ANDRES BELLO N°01-06, SECTOR FRANCISCO DE MIRANDA II', '(0412) 123-4567', 'renaven@correo.com', 'PUNTA CARDON', 'PUNTO FIJO', 'FALCON'))
    config_data = [
        ('exchange_rate', '36.50'),
        ('discount_percent', '5'),
        ('next_invoice', '1'),
        ('next_return', '1'),
        ('opening_balance', '0')
    ]
    for key, value in config_data:
        cursor.execute("INSERT IGNORE INTO config (key_name, value) VALUES (%s, %s)", (key, value))

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
    try:
        msg = Message(
            subject="Recuperación de Contraseña - RENAVEN",
            recipients=[user_email],
            html=f"""
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
        )
        mail.send(msg)
        logger.info(f"Correo enviado exitosamente a {user_email}")
        return True
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

# ==================== FUNCIÓN PARA ASEGURAR MYSQL (ADAPTADA PARA .EXE) ====================
def asegurar_mysql():
    """Arranca MySQL portable. El motor está en BUNDLE, los datos en BASE_DIR (junto al .exe)."""
    mysql_dir      = MYSQL_DIR
    mysql_exe_path = os.path.join(mysql_dir, 'bin', 'mysqld.exe')
    mysql_install  = os.path.join(mysql_dir, 'bin', 'mysql_install_db.exe')
    data_dir       = MYSQL_DATA_DIR
    my_ini_path    = os.path.join(mysql_dir, 'my.ini')

    print("🔍 Verificando motor MySQL portable...")
    print(f"   Motor : {mysql_dir}")
    print(f"   Datos : {data_dir}")

    if not os.path.exists(mysql_exe_path):
        print(f"❌ No se encontró mysqld.exe en: {mysql_exe_path}")
        return False

    def puerto_ocupado(host='127.0.0.1', port=3306, timeout=1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    if puerto_ocupado('127.0.0.1', 3306):
        print("✅ Puerto 3306 ya está ocupado → MySQL ya está corriendo.")
        return True

    # ---- Preparar datadir persistente ----
    if not os.path.exists(data_dir):
        plantilla = os.path.join(mysql_dir, 'data')
        if os.path.exists(plantilla):
            print(f"📁 Copiando datadir inicial a {data_dir} ...")
            try:
                shutil.copytree(plantilla, data_dir)
            except Exception as e:
                print(f"❌ Error copiando datadir: {e}")
                return False
        else:
            os.makedirs(data_dir, exist_ok=True)
            print("🛠️  Inicializando base de datos (primera ejecución)...")
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                env = os.environ.copy()
                env['MYSQLD_PARENT_PID'] = '1'

                if os.path.exists(mysql_install):
                    resultado = subprocess.run(
                        [mysql_install, f'--basedir={mysql_dir}', f'--datadir={data_dir}'],
                        shell=False, capture_output=True, text=True,
                        startupinfo=startupinfo, cwd=os.path.join(mysql_dir, 'bin'),
                        env=env, timeout=120
                    )
                else:
                    resultado = subprocess.run(
                        [mysql_exe_path, '--initialize-insecure',
                         f'--basedir={mysql_dir}', f'--datadir={data_dir}'],
                        shell=False, capture_output=True, text=True,
                        startupinfo=startupinfo, cwd=mysql_dir,
                        env=env, timeout=120
                    )
                if resultado.returncode != 0:
                    print(f"⚠️ Código: {resultado.returncode}")
                    if resultado.stdout: print(resultado.stdout[:500])
                    if resultado.stderr: print(resultado.stderr[:500])
                    if not os.listdir(data_dir):
                        print("❌ Datadir vacío, abortando.")
                        return False
            except Exception as e:
                print(f"❌ Error al inicializar: {e}")
                return False

    # ---- my.ini dinámico ----
    if not os.path.exists(my_ini_path):
        try:
            basedir_unix = mysql_dir.replace('\\', '/')
            datadir_unix = data_dir.replace('\\', '/')
            with open(my_ini_path, 'w', encoding='utf-8') as f:
                f.write(f"""[mysqld]
port=3306
basedir={basedir_unix}
datadir={datadir_unix}
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
default-storage-engine=InnoDB
skip-name-resolve
max_allowed_packet=64M
sql_mode=NO_ENGINE_SUBSTITUTION

[client]
port=3306
default-character-set=utf8mb4
""")
        except Exception as e:
            print(f"❌ No se pudo crear my.ini: {e}")
            return False

    # ---- Arrancar mysqld silenciosamente ----
    print("🚀 Iniciando servidor MySQL Portable...")
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        DETACHED_PROCESS        = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW         = 0x08000000

        proceso = subprocess.Popen(
            [mysql_exe_path, f'--defaults-file={my_ini_path}', f'--datadir={data_dir}'],
            startupinfo=startupinfo,
            cwd=mysql_dir,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True
        )
        print(f"   PID del proceso MySQL: {proceso.pid}")
        for intento in range(40):
            time.sleep(0.5)
            if puerto_ocupado('127.0.0.1', 3306):
                print(f"✅ MySQL Portable en funcionamiento ({(intento+1)*0.5:.1f}s).")
                return True
        print("⚠️  MySQL no respondió en 20 segundos.")
        return False
    except Exception as e:
        print(f"❌ Error al ejecutar mysqld.exe: {e}")
        return False

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
                session.permanent = True
                log_action(user['id'], username, 'LOGIN_EXITOSO', f"Rol: {user['role']}")
                flash(f'[OK] Bienvenido {username}', 'success')
                session.pop('captcha_text', None)
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

# ==================== FUNCIÓN AUXILIAR: PRODUCTO MÁS VENDIDO DEL MES ====================
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
        nombre_ganador = ganador[0]
        qty_ganador = ganador[1]['cantidad']
        ingresos_ganador = ganador[1]['ingresos']

        return nombre_ganador, qty_ganador, ingresos_ganador

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
                              saldo_bs=0,
                              saldo_usd=0,
                              company=None,
                              exchange_rate=40,
                              discount_percent=5,
                              opening_balance=0,
                              total_ventas_mes_usd=0,
                              total_ventas_mes_bs=0,
                              total_ventas_dia_usd=0,
                              total_ventas_dia_bs=0,
                              mes_actual='',
                              can_view_sales=False,
                              datetime=datetime,
                              current_date=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                              top_product_name=None,
                              top_product_qty=0,
                              top_product_total=0.0)

# ==================== API SALDO ACTUAL ====================
@app.route('/api/saldo_actual')
@login_required
def api_saldo_actual():
    try:
        saldo = obtener_saldo_actual()
        return jsonify({
            'success': True,
            'saldo_bs': saldo['bs'],
            'saldo_usd': saldo['usd']
        })
    except Exception as e:
        logger.error(f"Error en api_saldo_actual: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==================== FACTURACIÓN ====================
@app.route('/facturacion')
@login_required
@permission_required('sell')
def facturacion():
    try:
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
                          categories=categories,
                          company=company,
                          exchange_rate=exchange_rate,
                          discount_percent=discount_percent,
                          saldo_bs=saldo['bs'],
                          saldo_usd=saldo['usd'])

def buscar_productos():
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    try:
        query = "SELECT id, name, price, stock, unit, sale_type, barcode FROM products WHERE stock > 0 AND activo = 1"
        params = []
        if search:
            query += " AND name LIKE %s"
            params.append(f'%{search.upper()}%')
        if category and category != 'Todas':
            query += " AND category = %s"
            params.append(category)
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
        sales = execute_query("""SELECT s.invoice_number, s.date, s.client_name, s.client_id, s.subtotal, s.tax, s.total_usd, s.total_bs, s.payment_method, s.items FROM sales s WHERE s.invoice_number > 0 ORDER BY s.id DESC LIMIT 500""", fetch_all=True)
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

# ==================== CLIENTES FRECUENTES ====================
def buscar_clientes():
    search = request.args.get('search', '')
    try:
        if search:
            query = "SELECT id, cedula, nombre, telefono, direccion FROM clientes_frecuentes WHERE cedula LIKE %s OR nombre LIKE %s ORDER BY ultima_compra DESC LIMIT 10"
            params = (f'%{search}%', f'%{search.upper()}%')
            clientes = execute_query(query, params, fetch_all=True)
        else:
            clientes = execute_query("SELECT id, cedula, nombre, telefono, direccion FROM clientes_frecuentes ORDER BY ultima_compra DESC LIMIT 20", fetch_all=True)
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
        existing = execute_query("SELECT id FROM clientes_frecuentes WHERE cedula = %s", (cedula,), fetch_one=True)
        if existing:
            execute_query("""UPDATE clientes_frecuentes SET nombre = %s, telefono = %s, direccion = %s, ultima_compra = %s, total_compras = total_compras + 1 WHERE cedula = %s""", (nombre, telefono, direccion, datetime.now().isoformat(), cedula), commit=True)
        else:
            execute_query("""INSERT INTO clientes_frecuentes (cedula, nombre, telefono, direccion, ultima_compra) VALUES (%s, %s, %s, %s, %s)""", (cedula, nombre, telefono, direccion, datetime.now().isoformat()), commit=True)
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
        tax = float(sale.get('tax', 0) or 0)
        discount = float(sale.get('discount', 0) or 0)
        total_usd_guardado = float(sale.get('total_usd', 0) or 0)

        if total_usd_guardado > 0:
            total_factura = total_usd_guardado
        else:
            iva_calculado = subtotal * 0.16
            total_factura = subtotal - discount + iva_calculado
            if total_factura <= 0:
                total_factura = subtotal - discount + (subtotal * 0.16)

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
                'id': sale['id'],
                'invoice_number': sale['invoice_number'],
                'date': sale['date'],
                'client_name': sale['client_name'],
                'client_id': sale['client_id'],
                'total_usd': total_factura,
                'total_bs': float(sale.get('total_bs', 0) or 0),
                'subtotal': subtotal,
                'tax': tax,
                'discount': discount
            },
            'items': items,
            'total_devuelto': total_devuelto,
            'count_returns': count_returns,
            'disponible': disponible
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
            iva = subtotal * 0.16
            total_factura = subtotal - discount + iva
            if total_factura <= 0:
                total_factura = subtotal - discount + (subtotal * 0.16)

        existing_returns = execute_query("""
            SELECT COALESCE(SUM(total_usd),0) as total_devuelto
            FROM returns WHERE original_invoice = %s
        """, (invoice_number,), fetch_one=True)
        total_devuelto = float(existing_returns.get('total_devuelto', 0) or 0)
        disponible = max(0, total_factura - total_devuelto)

        subtotal_devolucion = sum(float(item['price']) * float(item['quantity']) for item in items)
        iva_devolucion = subtotal_devolucion * 0.16
        total_usd_devolucion = subtotal_devolucion + iva_devolucion

        if total_usd_devolucion > disponible + 0.01:
            return jsonify({
                'error': f'El monto a devolver (${total_usd_devolucion:.2f}) excede el disponible (${disponible:.2f})',
                'disponible': disponible,
                'subtotal': subtotal_devolucion,
                'total_usd': total_usd_devolucion
            }), 400

        rate = get_cached_exchange_rate()
        total_bs_devolucion = total_usd_devolucion * rate
        next_return = obtener_proximo_return_number()
        items_json = json.dumps(items)

        execute_query("""
            INSERT INTO returns
            (return_number, original_invoice, return_date, user_id,
             client_id, client_name, items, subtotal, total_usd, total_bs, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (next_return, invoice_number, datetime.now().isoformat(), session.get('user_id', 1),
              sale['client_id'], sale['client_name'], items_json,
              subtotal_devolucion, total_usd_devolucion, total_bs_devolucion, reason), commit=True)

        devolucion = execute_query("SELECT id FROM returns WHERE return_number = %s",
                                  (next_return,), fetch_one=True)
        devolucion_id = devolucion['id'] if devolucion else None

        registrar_movimiento_caja(
            tipo='egreso',
            monto_bs=total_bs_devolucion,
            monto_usd=total_usd_devolucion,
            concepto=f'Devolución N° {next_return} - Factura N° {invoice_number}',
            referencia_tipo='devolucion',
            referencia_id=next_return,
            metodo='efectivo',
            usuario=session.get('username', 'SISTEMA'),
            nota=f"Devolución de factura N° {invoice_number}. Motivo: {reason}",
            tasa=rate
        )

        if devolucion_id:
            for item in items:
                subtotal_item = float(item['price']) * float(item['quantity'])
                iva_item = subtotal_item * 0.16
                total_item_usd = subtotal_item + iva_item
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
             pago_transferencia, pago_tarjeta, cambio_usd, cambio_bs, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (-next_return, fecha_actual, session.get('user_id', 1), sale['client_id'],
              sale['client_name'], sale.get('client_phone', '') or '', sale.get('client_address', '') or '',
              items_negativos_json, -subtotal_devolucion, 0, -iva_devolucion,
              -total_usd_devolucion, -total_bs_devolucion,
              f"DEVOLUCIÓN N° {next_return} - {reason}",
              json.dumps({"devolucion": True, "return_number": next_return}),
              -total_bs_devolucion, 0, 0, 0, 0, 0, 0, 0,
              f"Devolución de factura N° {invoice_number}"), commit=True)

        log_action(session.get('user_id', 1), session.get('username', 'SISTEMA'), 'RETURN',
                   f"Devolución N° {next_return} - Factura N° {invoice_number} - Total: -${total_usd_devolucion:.2f} (incluye IVA)")

        saldo = obtener_saldo_actual()

        return jsonify({
            'success': True,
            'return_number': next_return,
            'total_usd': total_usd_devolucion,
            'total_bs': total_bs_devolucion,
            'saldo_bs': saldo['bs'],
            'saldo_usd': saldo['usd'],
            'message': f'Devolución N° {next_return} procesada. Se devolvieron ${total_usd_devolucion:.2f} USD (incluye IVA)'
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
            FROM returns
            WHERE return_number = %s
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
            'success': True,
            'numero': ret['return_number'],
            'cliente': ret['client_name'],
            'cliente_nombre': ret['client_name'],
            'monto_total': total,
            'monto_usado': usado,
            'disponible': disponible,
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
        returns = execute_query("""
            SELECT r.*, u.username as user_name
            FROM returns r
            LEFT JOIN users u ON r.user_id = u.id
            ORDER BY r.created_at DESC
            LIMIT 100
        """, fetch_all=True)
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
            FROM sales
            WHERE invoice_number = %s AND invoice_number > 0
        """, (devolucion['original_invoice'],), fetch_one=True)

        items = json.loads(devolucion['items']) if devolucion['items'] else []

        tasa_iva = 0.16
        subtotal_usd = 0.0
        for item in items:
            precio = float(item.get('price', 0))
            cantidad = float(item.get('quantity', 0))
            subtotal_usd += precio * cantidad

        iva_usd = subtotal_usd * tasa_iva
        total_usd = subtotal_usd + iva_usd

        for item in items:
            precio = float(item.get('price', 0))
            cantidad = float(item.get('quantity', 0))
            item['price_without_tax'] = precio
            item['tax_amount'] = precio * tasa_iva
            item['total_with_tax'] = (precio + (precio * tasa_iva)) * cantidad
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
                               items=items,
                               subtotal_usd=subtotal_usd,
                               iva_usd=iva_usd,
                               total_usd=total_usd,
                               total_bs=total_bs,
                               tasa_iva=int(tasa_iva * 100),
                               reason=devolucion['reason'],
                               current_date=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                               company=company,
                               session=session,
                               saldo_bs=saldo['bs'],
                               saldo_usd=saldo['usd'])
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
        products = execute_query("""SELECT id, barcode, name, category, unit, sale_type, COALESCE(cost,0) as cost, COALESCE(margin,0) as margin, COALESCE(price,0) as price, COALESCE(stock,0) as stock, COALESCE(arrival_date,'') as arrival_date, CASE WHEN stock <= 0 THEN 'AGOTADO' WHEN stock <= 5 THEN 'STOCK BAJO' ELSE 'DISPONIBLE' END as stock_status FROM products WHERE activo = 1 ORDER BY stock ASC, name""", fetch_all=True)
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
        cost = float(request.form['cost'])
        margin = float(request.form['margin'])
        stock = float(request.form['stock'])
        arrival_date = request.form.get('arrival_date', '')
        price = cost * (1 + margin / 100)
        if barcode:
            existing = execute_query("SELECT id, name, stock, activo FROM products WHERE barcode = %s", (barcode,), fetch_one=True)
            if existing:
                new_stock = float(existing['stock']) + stock
                status = 'AGOTADO' if new_stock <= 0 else 'STOCK BAJO' if new_stock <= 5 else 'DISPONIBLE'
                execute_query("UPDATE products SET name = %s, category = %s, unit = %s, sale_type = %s, cost = %s, margin = %s, price = %s, stock = stock + %s, arrival_date = %s, activo = 1, stock_status = %s WHERE barcode = %s", (name, category, unit, sale_type, cost, margin, price, stock, arrival_date, status, barcode), commit=True)
                flash(f'[OK] Producto {name} reactivado con stock actualizado', 'success')
            else:
                status = 'AGOTADO' if stock <= 0 else 'STOCK BAJO' if stock <= 5 else 'DISPONIBLE'
                execute_query("INSERT INTO products (barcode, name, category, unit, sale_type, cost, margin, price, stock, arrival_date, stock_status, activo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)", (barcode, name, category, unit, sale_type, cost, margin, price, stock, arrival_date, status), commit=True)
                flash(f'[OK] Producto {name} agregado correctamente', 'success')
        else:
            status = 'AGOTADO' if stock <= 0 else 'STOCK BAJO' if stock <= 5 else 'DISPONIBLE'
            execute_query("INSERT INTO products (barcode, name, category, unit, sale_type, cost, margin, price, stock, arrival_date, stock_status, activo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)", (barcode, name, category, unit, sale_type, cost, margin, price, stock, arrival_date, status), commit=True)
            flash(f'[OK] Producto {name} agregado correctamente', 'success')
        log_action(session['user_id'], session['username'], 'ADD_PRODUCT', f"Producto: {name} - Tipo: {sale_type}")
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
            execute_query("INSERT IGNORE INTO categories (name) VALUES (%s)", (name,), commit=True)
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
        flash('[INFO] No hay productos agotados para limpiar', 'info')
    return redirect(url_for('inventory'))

@app.route('/edit_product', methods=['POST'])
@login_required
@permission_required('edit_products')
def edit_product():
    try:
        product_id = request.form.get('product_id')
        if not product_id:
            flash('ID de producto no válido', 'danger')
            return redirect(url_for('inventory'))
        barcode = request.form.get('barcode', '').strip()
        name = request.form.get('name', '').upper()
        category = request.form.get('category')
        unit = request.form.get('unit')
        sale_type = request.form.get('sale_type', 'unit')
        cost = float(request.form.get('cost', 0))
        margin = float(request.form.get('margin', 0))
        stock = float(request.form.get('stock', 0))
        arrival_date = request.form.get('arrival_date', '')
        price = cost * (1 + margin / 100)
        if stock <= 0:
            status = 'AGOTADO'
        elif stock <= 5:
            status = 'STOCK BAJO'
        else:
            status = 'DISPONIBLE'
        old_product = execute_query("SELECT name FROM products WHERE id = %s", (product_id,), fetch_one=True)
        execute_query("UPDATE products SET barcode = %s, name = %s, category = %s, unit = %s, sale_type = %s, cost = %s, margin = %s, price = %s, stock = %s, arrival_date = %s, stock_status = %s, activo = 1 WHERE id = %s", (barcode, name, category, unit, sale_type, cost, margin, price, stock, arrival_date, status, product_id), commit=True)
        log_action(session['user_id'], session['username'], 'EDIT_PRODUCT', f"Producto: {old_product['name'] if old_product else 'Unknown'} -> {name}")
        flash(f'[OK] Producto {name} actualizado correctamente', 'success')
    except Exception as e:
        logger.error(f"Error en edit_product: {e}")
        flash(f'[ERROR] Error al actualizar producto: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

@app.route('/delete_product', methods=['POST'])
@login_required
def delete_product():
    if session.get('role') != 'Dueño':
        flash('[ERROR] No tienes permiso para eliminar productos. Solo el Dueño puede hacerlo.', 'danger')
        return redirect(url_for('inventory'))
    product_id = request.form.get('product_id')
    motivo = request.form.get('motivo_eliminacion', '').strip()
    usuario = session.get('username', 'Desconocido')
    if not product_id:
        flash('ID de producto no válido', 'danger')
        return redirect(url_for('inventory'))
    if not motivo:
        flash('[ERROR] Debes especificar un motivo para eliminar el producto', 'danger')
        return redirect(url_for('inventory'))
    try:
        product = execute_query("SELECT * FROM products WHERE id = %s AND activo = 1", (product_id,), fetch_one=True)
        if not product:
            flash('Producto no encontrado', 'danger')
            return redirect(url_for('inventory'))
        execute_query("INSERT INTO deleted_products (original_id, barcode, name, category, unit, stock, cost, price, margin, arrival_date, motivo_eliminacion, usuario_elimino, fecha_eliminacion) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())", (product['id'], product['barcode'], product['name'], product['category'], product['unit'], product['stock'], product['cost'], product['price'], product['margin'], product['arrival_date'], motivo, usuario), commit=True)
        execute_query("UPDATE products SET activo = 0 WHERE id = %s", (product_id,), commit=True)
        log_action(session['user_id'], session['username'], 'DELETE_PRODUCT', f"Producto: {product['name']} - Motivo: {motivo}")
        flash(f'[OK] Producto "{product["name"]}" eliminado correctamente. Motivo: {motivo}', 'success')
    except Exception as e:
        logger.error(f"Error en delete_product: {e}")
        flash(f'[ERROR] Error al eliminar producto: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

# ==================== PRODUCTOS ELIMINADOS ====================
@app.route('/deleted_products')
@login_required
def deleted_products():
    if session.get('role') != 'Dueño':
        flash('No tienes permiso para ver esta página', 'danger')
        return redirect(url_for('inventory'))
    try:
        available_years = execute_query("""
            SELECT DISTINCT YEAR(fecha_eliminacion) as año
            FROM deleted_products
            ORDER BY año DESC
        """, fetch_all=True)
        selected_year = request.args.get('year', type=int, default=datetime.now().year)
        selected_month = request.args.get('month', type=int, default=None)
        query = "SELECT * FROM deleted_products"
        params = []
        if selected_month and selected_month > 0:
            query += " WHERE YEAR(fecha_eliminacion) = %s AND MONTH(fecha_eliminacion) = %s"
            params = [selected_year, selected_month]
        else:
            query += " WHERE YEAR(fecha_eliminacion) = %s"
            params = [selected_year]
        query += " ORDER BY fecha_eliminacion DESC"
        deleted = execute_query(query, tuple(params), fetch_all=True)
        converted_deleted = []
        for item in deleted:
            new_item = dict(item)
            if new_item.get('fecha_eliminacion') and hasattr(new_item['fecha_eliminacion'], 'strftime'):
                new_item['fecha_eliminacion'] = new_item['fecha_eliminacion'].strftime('%Y-%m-%d %H:%M:%S')
            new_item['stock'] = float(new_item['stock']) if new_item.get('stock') else 0
            new_item['cost'] = float(new_item['cost']) if new_item.get('cost') else 0
            new_item['price'] = float(new_item['price']) if new_item.get('price') else 0
            new_item['margin'] = float(new_item['margin']) if new_item.get('margin') else 0
            converted_deleted.append(new_item)
        meses_nombres_list = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        chart_months = []
        chart_counts = []
        chart_agotados = []
        chart_discontinuados = []
        chart_dañados = []
        if converted_deleted:
            mensual = defaultdict(lambda: {'total': 0, 'agotado': 0, 'discontinuado': 0, 'dañado': 0})
            for item in converted_deleted:
                if item.get('fecha_eliminacion'):
                    try:
                        fecha_str = item['fecha_eliminacion']
                        if isinstance(fecha_str, str):
                            fecha = datetime.strptime(fecha_str[:10], '%Y-%m-%d')
                        else:
                            fecha = item['fecha_eliminacion']
                        mes = fecha.month
                        mensual[mes]['total'] += 1
                        motivo = item.get('motivo_eliminacion', '').lower()
                        if 'agotado' in motivo:
                            mensual[mes]['agotado'] += 1
                        elif 'discontinu' in motivo:
                            mensual[mes]['discontinuado'] += 1
                        elif 'dañ' in motivo or 'daño' in motivo:
                            mensual[mes]['dañado'] += 1
                    except:
                        pass
            for mes in range(1, 13):
                chart_months.append(meses_nombres_list[mes-1])
                chart_counts.append(mensual[mes]['total'])
                chart_agotados.append(mensual[mes]['agotado'])
                chart_discontinuados.append(mensual[mes]['discontinuado'])
                chart_dañados.append(mensual[mes]['dañado'])
        current_date = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        return render_template('deleted_products.html',
                            deleted=converted_deleted,
                            available_years=available_years,
                            selected_year=selected_year,
                            selected_month=selected_month or '',
                            meses_nombres=meses_nombres_list,
                            now=current_date,
                            current_date=current_date,
                            chart_months=chart_months,
                            chart_counts=chart_counts,
                            chart_agotados=chart_agotados,
                            chart_discontinuados=chart_discontinuados,
                            chart_dañados=chart_dañados)
    except Exception as e:
        logger.error(f"Error en deleted_products: {e}")
        flash(f'Error al cargar productos eliminados: {str(e)}', 'danger')
        current_date = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        return render_template('deleted_products.html',
                            deleted=[],
                            available_years=[],
                            selected_year=datetime.now().year,
                            selected_month='',
                            meses_nombres=["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                                            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
                            now=current_date,
                            current_date=current_date,
                            chart_months=[],
                            chart_counts=[],
                            chart_agotados=[],
                            chart_discontinuados=[],
                            chart_dañados=[])

# ==================== CARRITO ====================
def add_to_cart():
    data = request.json
    try:
        product = execute_query("SELECT * FROM products WHERE id = %s AND activo = 1", (data['id'],), fetch_one=True)
        if not product:
            return jsonify({'error': 'Producto no encontrado'}), 404
        stock_val = float(product['stock'])
        quantity = float(data['quantity'])
        if stock_val < quantity:
            return jsonify({'error': f'Stock insuficiente. Solo hay {stock_val} disponibles'}), 400
        if stock_val <= 0:
            return jsonify({'error': 'Producto AGOTADO. No se puede agregar al carrito'}), 400
        cart = session.get('cart', [])
        sale_type = product.get('sale_type', 'unit')
        quantity_text = str(quantity)
        if sale_type == 'weight':
            if quantity == 1:
                quantity_text = "1 Kg"
            elif quantity == 0.5:
                quantity_text = "1/2 Kg (500g)"
            elif quantity == 0.25:
                quantity_text = "1/4 Kg (250g)"
            else:
                quantity_text = f"{quantity} Kg"
        else:
            if quantity == int(quantity):
                quantity_text = str(int(quantity))
        cart.append({
            'id': product['id'],
            'name': product['name'],
            'price': float(product['price']),
            'quantity': quantity,
            'quantity_text': quantity_text,
            'unit': product['unit'],
            'sale_type': sale_type,
            'barcode': product['barcode'] or ''
        })
        session['cart'] = cart
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error en add_to_cart: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart_route():
    return add_to_cart()

def get_cart():
    cart = session.get('cart', [])
    for item in cart:
        if isinstance(item.get('price'), Decimal):
            item['price'] = float(item['price'])
        if isinstance(item.get('quantity'), Decimal):
            item['quantity'] = float(item['quantity'])
        if 'quantity_text' not in item:
            item['quantity_text'] = str(item['quantity'])
    return jsonify({'items': cart})

@app.route('/get_cart')
def get_cart_route():
    return get_cart()

@app.route('/remove_from_cart/<int:index>')
@login_required
def remove_from_cart(index):
    cart = session.get('cart', [])
    if 0 <= index < len(cart):
        cart.pop(index)
        session['cart'] = cart
    return redirect(url_for('facturacion'))

@app.route('/clear_cart')
@login_required
def clear_cart():
    session.pop('cart', None)
    flash('Carrito limpiado', 'success')
    return redirect(url_for('facturacion'))

# ==================== CHECKOUT ====================
def checkout():
    cart = session.get('cart', [])
    if not cart:
        flash('Carrito vacío', 'warning')
        return redirect(url_for('facturacion'))

    client_id = request.form['client_id'].strip()
    client_name = request.form['client_name'].strip().upper()
    client_phone = request.form.get('client_phone', '')
    client_address = request.form['client_address'].strip().upper()
    pagos_json = request.form.get('pagos', '{}')
    pagos = json.loads(pagos_json) if pagos_json else {}

    if not client_id or not client_name or not client_address:
        flash('Cédula, nombre y dirección son obligatorios', 'danger')
        return redirect(url_for('facturacion'))

    try:
        for item in cart:
            product = execute_query("SELECT stock FROM products WHERE id = %s AND activo = 1", (item['id'],), fetch_one=True)
            if not product or float(product['stock']) < item['quantity']:
                flash(f'[ERROR] Stock insuficiente para {item["name"]}', 'danger')
                return redirect(url_for('facturacion'))

        subtotal = sum(item['price'] * item['quantity'] for item in cart)
        rate = get_cached_exchange_rate()
        discount_percent = get_cached_discount()

        pago_efectivo_bs = parse_venezuela_number(pagos.get('efectivo_bs', 0))
        pago_efectivo_usd = parse_venezuela_number(pagos.get('efectivo_usd', 0))
        pago_zelle = parse_venezuela_number(pagos.get('zelle', 0))
        pago_movil = parse_venezuela_number(pagos.get('movil', 0))
        pago_transferencia = parse_venezuela_number(pagos.get('transferencia', 0))
        pago_tarjeta = parse_venezuela_number(pagos.get('tarjeta', 0))
        pago_nota_credito_usd = parse_venezuela_number(pagos.get('nota_credito', 0))
        pago_devolucion_usd = parse_venezuela_number(pagos.get('devolucion', 0))

        tiene_descuento = (pago_efectivo_usd > 0 or pago_zelle > 0) and (pago_efectivo_bs == 0 and pago_movil == 0 and pago_transferencia == 0 and pago_tarjeta == 0)

        discount = subtotal * (discount_percent / 100) if tiene_descuento else 0
        taxable = subtotal - discount
        tax = taxable * 0.16
        total_usd = taxable + tax
        total_bs = total_usd * rate

        total_pagado_usd = (pago_efectivo_usd + pago_zelle +
                           (pago_efectivo_bs / rate) +
                           (pago_movil / rate) +
                           (pago_transferencia / rate) +
                           (pago_tarjeta / rate) +
                           pago_nota_credito_usd +
                           pago_devolucion_usd)

        cambio_usd = total_pagado_usd - total_usd
        cambio_bs = cambio_usd * rate

        metodos_pagos = []
        if pago_efectivo_bs > 0: metodos_pagos.append(f"Efectivo Bs: Bs. {pago_efectivo_bs:,.2f}")
        if pago_efectivo_usd > 0: metodos_pagos.append(f"Efectivo USD: ${pago_efectivo_usd:,.2f}")
        if pago_zelle > 0: metodos_pagos.append(f"Zelle: ${pago_zelle:,.2f}")
        if pago_movil > 0: metodos_pagos.append(f"Pago Móvil: Bs. {pago_movil:,.2f}")
        if pago_transferencia > 0: metodos_pagos.append(f"Transferencia: Bs. {pago_transferencia:,.2f}")
        if pago_tarjeta > 0: metodos_pagos.append(f"Tarjeta: Bs. {pago_tarjeta:,.2f}")
        if pago_nota_credito_usd > 0: metodos_pagos.append(f"Nota Crédito: ${pago_nota_credito_usd:,.2f}")
        if pago_devolucion_usd > 0: metodos_pagos.append(f"Devolución: ${pago_devolucion_usd:,.2f}")

        payment_method = " | ".join(metodos_pagos) if metodos_pagos else "Múltiples métodos"
        payment_details = json.dumps({
            'efectivo_bs': pago_efectivo_bs,
            'efectivo_usd': pago_efectivo_usd,
            'zelle': pago_zelle,
            'movil': pago_movil,
            'transferencia': pago_transferencia,
            'tarjeta': pago_tarjeta,
            'nota_credito_usd': pago_nota_credito_usd,
            'devolucion_usd': pago_devolucion_usd,
            'cambio_usd': cambio_usd,
            'cambio_bs': cambio_bs,
            'tasa': rate
        })

        next_inv_result = execute_query("SELECT value FROM config WHERE key_name = 'next_invoice'", fetch_one=True)
        next_inv = int(next_inv_result['value']) if next_inv_result else 1
        execute_query("UPDATE config SET value = %s WHERE key_name = 'next_invoice'", (str(next_inv + 1),), commit=True)

        items_json = json.dumps(cart)
        fecha_actual = datetime.now().isoformat()

        execute_query("""INSERT INTO sales
                       (invoice_number, date, user_id, client_id, client_name,
                        client_phone, client_address, items, subtotal, discount, tax,
                        total_usd, total_bs, payment_method, payment_details,
                        pago_efectivo_bs, pago_efectivo_usd, pago_zelle, pago_movil,
                        pago_transferencia, pago_tarjeta, cambio_usd, cambio_bs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (next_inv, fecha_actual, session['user_id'], client_id, client_name,
                      client_phone, client_address, items_json, subtotal, discount, tax,
                      total_usd, total_bs, payment_method, payment_details,
                      pago_efectivo_bs, pago_efectivo_usd, pago_zelle, pago_movil,
                      pago_transferencia, pago_tarjeta, cambio_usd, cambio_bs), commit=True)

        registrar_movimiento_caja(
            tipo='ingreso',
            monto_bs=total_bs,
            monto_usd=total_usd,
            concepto=f'Venta N° {next_inv} - {client_name}',
            referencia_tipo='venta',
            referencia_id=next_inv,
            metodo='efectivo',
            usuario=session.get('username', 'SISTEMA'),
            nota=f"Venta N° {next_inv} - Cliente: {client_name}",
            tasa=rate
        )

        for item in cart:
            execute_query("UPDATE products SET stock = stock - %s WHERE id = %s", (item['quantity'], item['id']), commit=True)

        eliminados = limpiar_o_reactivar_productos_agotados()
        if eliminados > 0:
            flash(f'[INFO] Se eliminaron {eliminados} productos agotados', 'info')

        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)

        existing_client = execute_query("SELECT id FROM clientes_frecuentes WHERE cedula = %s", (client_id,), fetch_one=True)
        if existing_client:
            execute_query("UPDATE clientes_frecuentes SET nombre = %s, telefono = %s, direccion = %s, ultima_compra = %s, total_compras = total_compras + 1, monto_total = monto_total + %s WHERE cedula = %s", (client_name, client_phone, client_address, fecha_actual, total_usd, client_id), commit=True)
        else:
            execute_query("INSERT INTO clientes_frecuentes (cedula, nombre, telefono, direccion, ultima_compra, monto_total) VALUES (%s, %s, %s, %s, %s, %s)", (client_id, client_name, client_phone, client_address, fecha_actual, total_usd), commit=True)

        log_action(session['user_id'], session['username'], 'SALE', f"Factura N° {next_inv} - Total: ${total_usd:.2f}")

        if cambio_usd > 0.01:
            flash(f'[OK] Factura N° {next_inv:04d} generada. Cambio: ${cambio_usd:.2f} USD (Bs. {cambio_bs:.2f})', 'success')
        else:
            flash(f'[OK] Factura N° {next_inv:04d} generada.', 'success')

    except Exception as e:
        logger.error(f"Error en checkout: {e}")
        flash(f'[ERROR] Error al procesar la venta: {str(e)}', 'danger')
        return redirect(url_for('facturacion'))

    session.pop('cart', None)

    return render_template('invoice.html',
                          invoice_number=next_inv,
                          client_id=client_id,
                          client_name=client_name,
                          client_phone=client_phone,
                          client_address=client_address,
                          cart=cart,
                          subtotal=subtotal,
                          discount=discount,
                          tax=tax,
                          total_usd=total_usd,
                          total_bs=total_bs,
                          current_date=fecha_actual,
                          company=company,
                          pagos={
                              'efectivo_bs': pago_efectivo_bs,
                              'efectivo_usd': pago_efectivo_usd,
                              'zelle': pago_zelle,
                              'movil': pago_movil,
                              'transferencia': pago_transferencia,
                              'tarjeta': pago_tarjeta,
                              'nota_credito_usd': pago_nota_credito_usd,
                              'devolucion_usd': pago_devolucion_usd,
                              'cambio_usd': cambio_usd,
                              'cambio_bs': cambio_bs
                          },
                          rate=rate)

# ==================== PRESUPUESTO ====================
def presupuesto():
    cart = session.get('cart', [])
    if not cart:
        flash('Carrito vacío', 'warning')
        return redirect(url_for('facturacion'))
    client_name = request.form.get('client_name', '').upper() or "CLIENTE"
    client_id = request.form.get('client_id', '')
    client_phone = request.form.get('client_phone', '')
    client_address = request.form.get('client_address', '').upper()
    subtotal = sum(i['price'] * i['quantity'] for i in cart)
    tax = subtotal * 0.16
    total_usd = subtotal + tax
    try:
        rate = get_cached_exchange_rate()
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
    except:
        rate = 36.50
        company = None
    total_bs = total_usd * rate
    return render_template('presupuesto.html', client_name=client_name, client_id=client_id, client_phone=client_phone, client_address=client_address, cart=cart, subtotal=subtotal, tax=tax, total_usd=total_usd, total_bs=total_bs, current_date=datetime.now().strftime('%d/%m/%Y %H:%M:%S'), company=company)

@app.route('/presupuesto', methods=['POST'])
def presupuesto_route():
    return presupuesto()

# ==================== APERTURA DE CAJA ====================
@app.route('/opening_balance', methods=['GET', 'POST'])
@login_required
def opening_balance():
    if request.method == 'POST':
        monto = float(request.form.get('monto', 0))
        password = request.form.get('supervisor_password', '')
        if monto < 0:
            flash('[ERROR] El monto no puede ser negativo', 'danger')
            return redirect(url_for('opening_balance'))
        if not password:
            flash('[ERROR] Se requiere contraseña de supervisor', 'danger')
            return redirect(url_for('opening_balance'))
        try:
            autorizado = execute_query("SELECT * FROM users WHERE role IN ('Dueño', 'Supervisor')", fetch_one=True)
            if not autorizado or not bcrypt.checkpw(password.encode(), autorizado['password'].encode()):
                flash('Contraseña incorrecta. Solo Dueño o Supervisor pueden abrir caja', 'danger')
                return redirect(url_for('opening_balance'))
            today = datetime.now().strftime('%Y-%m-%d')
            cierre_hoy = execute_query("SELECT id FROM cash_closures WHERE closure_date LIKE %s LIMIT 1", (today+'%',), fetch_one=True)
            opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
            current_balance = float(opening_result['value']) if opening_result else 0
            if current_balance > 0 and not cierre_hoy:
                flash(f'[WARN] Ya hay un fondo activo de Bs. {current_balance:.2f}. Debe cerrar la caja primero.', 'warning')
                return redirect(url_for('opening_balance'))
            execute_query("UPDATE config SET value = %s WHERE key_name = 'opening_balance'", (str(monto),), commit=True)
            execute_query("INSERT INTO cash_opening (opening_date, user_id, user_name, opening_balance, notes) VALUES (%s, %s, %s, %s, %s)", (datetime.now().isoformat(), session['user_id'], session['username'], monto, "Apertura de caja"), commit=True)
            registrar_movimiento_caja(
                tipo='ingreso',
                monto_bs=monto,
                monto_usd=0,
                concepto='Apertura de caja',
                referencia_tipo='apertura',
                referencia_id=0,
                metodo='efectivo',
                usuario=session.get('username', 'SISTEMA'),
                nota=f"Apertura de caja con Bs. {monto:.2f}",
                tasa=get_cached_exchange_rate()
            )
            log_action(session['user_id'], session['username'], 'OPENING_BALANCE', f"Apertura de caja con Bs. {monto:.2f}")
            flash(f'[OK] Caja abierta exitosamente con fondo de Bs. {monto:.2f}', 'success')
        except Exception as e:
            logger.error(f"Error en opening_balance POST: {e}")
            flash(f'[ERROR] Error al abrir caja: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))
    try:
        opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
        current_balance = float(opening_result['value']) if opening_result else 0
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        today = datetime.now().strftime('%Y-%m-%d')
        cierre_hoy = execute_query("SELECT id FROM cash_closures WHERE closure_date LIKE %s LIMIT 1", (today+'%',), fetch_one=True)
        puede_abrir = (current_balance == 0) or (cierre_hoy is not None)
        saldo = obtener_saldo_actual()
    except Exception as e:
        logger.error(f"Error en opening_balance GET: {e}")
        current_balance = 0
        company = None
        puede_abrir = True
        saldo = {'bs': 0, 'usd': 0}
    return render_template('opening_balance.html', current_balance=current_balance, company=company,
                          puede_abrir=puede_abrir, datetime=datetime, saldo_bs=saldo['bs'], saldo_usd=saldo['usd'])

# ==================== CIERRE DE CAJA (REDIRECCIÓN A UNIFICADO) ====================
@app.route('/cash_closure', methods=['GET', 'POST'])
@login_required
def cash_closure():
    return redirect(url_for('daily_closure'))

# ==================== REPORTE UNIFICADO ====================
@app.route('/daily_closure', methods=['GET', 'POST'])
@login_required
@permission_required('reports')
def daily_closure():
    today_str = datetime.now().strftime('%Y-%m-%d')
    cierre_realizado = False
    cierre_hoy = execute_query(
        "SELECT id FROM cash_closures WHERE closure_date LIKE %s LIMIT 1",
        (today_str + '%',), fetch_one=True
    )
    if cierre_hoy:
        cierre_realizado = True

    if request.method == 'POST':
        if cierre_realizado:
            flash('[WARN] Ya se realizó el cierre de caja hoy.', 'warning')
            return redirect(url_for('daily_closure'))

        password = request.form.get('supervisor_password')
        if not password:
            flash('[ERROR] Se requiere contraseña para cerrar caja', 'danger')
            return redirect(url_for('daily_closure'))

        autorizado = execute_query(
            "SELECT * FROM users WHERE role IN ('Dueño', 'Supervisor')",
            fetch_one=True
        )
        if not autorizado or not bcrypt.checkpw(password.encode(), autorizado['password'].encode()):
            flash('Contraseña incorrecta. Solo Dueño o Supervisor pueden cerrar caja', 'danger')
            return redirect(url_for('daily_closure'))

        rate = get_cached_exchange_rate()
        opening_row = execute_query(
            "SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True
        )
        opening_balance = float(opening_row['value']) if opening_row else 0

        ventas = execute_query(
            "SELECT * FROM sales WHERE date LIKE %s AND invoice_number > 0",
            (today_str + '%',), fetch_all=True
        )
        total_bs = sum(float(v['total_bs']) for v in ventas) if ventas else 0
        total_usd = sum(float(v['total_usd']) for v in ventas) if ventas else 0

        metodos = execute_query("""
            SELECT
                COALESCE(SUM(pago_efectivo_bs),0) as efectivo_bs,
                COALESCE(SUM(pago_efectivo_usd),0) as efectivo_usd,
                COALESCE(SUM(pago_zelle),0) as zelle,
                COALESCE(SUM(pago_movil),0) as movil,
                COALESCE(SUM(pago_transferencia),0) as transferencia,
                COALESCE(SUM(pago_tarjeta),0) as tarjeta
            FROM sales
            WHERE date LIKE %s AND invoice_number > 0
        """, (today_str + '%',), fetch_one=True) or {}

        total_efectivo_bs = float(metodos.get('efectivo_bs', 0))
        total_efectivo_usd = float(metodos.get('efectivo_usd', 0))
        total_zelle = float(metodos.get('zelle', 0))
        total_pago_movil = float(metodos.get('movil', 0))
        total_transferencia = float(metodos.get('transferencia', 0))
        total_tarjeta = float(metodos.get('tarjeta', 0))

        devoluciones_bs = float(request.form.get('devoluciones_bs', 0) or 0)
        devoluciones_usd = float(request.form.get('devoluciones_usd', 0) or 0)
        total_devoluciones_bs = devoluciones_bs + (devoluciones_usd * rate)

        expected_cash = opening_balance + total_bs - total_devoluciones_bs

        entregado_efectivo_bs = float(request.form.get('cash_bs_delivered', 0) or 0)
        entregado_efectivo_usd = float(request.form.get('cash_usd_delivered', 0) or 0)
        entregado_zelle = float(request.form.get('zelle_delivered', 0) or 0)
        entregado_movil = float(request.form.get('pago_movil_delivered', 0) or 0)
        entregado_transferencia = float(request.form.get('transferencia_delivered', 0) or 0)
        entregado_tarjeta = float(request.form.get('tarjeta_delivered', 0) or 0)

        total_entregado = (
            entregado_efectivo_bs +
            (entregado_efectivo_usd * rate) +
            (entregado_zelle * rate) +
            entregado_movil +
            entregado_transferencia +
            entregado_tarjeta
        )

        difference = total_entregado - expected_cash

        if difference > 0.01:
            resultado = f"SOBRANTE: +{difference:.2f} Bs"
            tipo_flash = 'warning'
        elif difference < -0.01:
            resultado = f"FALTANTE: {difference:.2f} Bs"
            tipo_flash = 'danger'
        else:
            resultado = "CUADRADO PERFECTO"
            tipo_flash = 'success'

        notes = request.form.get('notes', '')
        execute_query("""
            INSERT INTO cash_closures
            (closure_date, user_id, user_name, cashier_name, supervisor_username,
             opening_balance, sales_total_bs,
             cash_bs_delivered, cash_usd_delivered, zelle_delivered,
             pago_movil_delivered, transferencia_delivered, tarjeta_delivered,
             devoluciones_bs, devoluciones_usd,
             difference, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            datetime.now().isoformat(),
            session['user_id'],
            session['username'],
            session['username'],
            autorizado['username'],
            opening_balance,
            total_bs,
            entregado_efectivo_bs,
            entregado_efectivo_usd,
            entregado_zelle,
            entregado_movil,
            entregado_transferencia,
            entregado_tarjeta,
            devoluciones_bs,
            devoluciones_usd,
            difference,
            f"{resultado}\n{notes}" if notes else resultado
        ), commit=True)

        execute_query("UPDATE config SET value = '0' WHERE key_name = 'opening_balance'", commit=True)
        execute_query("UPDATE saldo_caja SET saldo_bs = 0, saldo_usd = 0 WHERE id = 1", commit=True)

        saldo = obtener_saldo_actual()
        if saldo['bs'] > 0:
            registrar_movimiento_caja(
                tipo='egreso',
                monto_bs=saldo['bs'],
                monto_usd=saldo['usd'],
                concepto='Cierre de caja',
                referencia_tipo='cierre',
                referencia_id=0,
                metodo='efectivo',
                usuario=session.get('username', 'SISTEMA'),
                nota=f"Cierre de caja - {resultado}",
                tasa=rate
            )

        log_action(session['user_id'], session['username'], 'CASH_CLOSURE', f"Resultado: {resultado} - Fondo reiniciado a CERO")
        flash(f'[OK] Cierre de caja completado. {resultado} Fondo de caja reiniciado a CERO (Bs. 0.00).', tipo_flash)
        return redirect(url_for('daily_closure'))

    try:
        rate = get_cached_exchange_rate()
        opening_row = execute_query(
            "SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True
        )
        opening_balance = float(opening_row['value']) if opening_row else 0

        sales = execute_query(
            "SELECT * FROM sales WHERE date LIKE %s AND invoice_number > 0 ORDER BY date DESC",
            (today_str + '%',), fetch_all=True
        ) or []

        metodos = execute_query("""
            SELECT
                COALESCE(SUM(pago_efectivo_bs),0) as efectivo_bs,
                COALESCE(SUM(pago_efectivo_usd),0) as efectivo_usd,
                COALESCE(SUM(pago_zelle),0) as zelle,
                COALESCE(SUM(pago_movil),0) as movil,
                COALESCE(SUM(pago_transferencia),0) as transferencia,
                COALESCE(SUM(pago_tarjeta),0) as tarjeta,
                COALESCE(SUM(total_bs),0) as total_bs,
                COALESCE(SUM(total_usd),0) as total_usd
            FROM sales
            WHERE date LIKE %s AND invoice_number > 0
        """, (today_str + '%',), fetch_one=True) or {}

        total_bs = float(metodos.get('total_bs', 0))
        total_usd = float(metodos.get('total_usd', 0))
        total_efectivo_bs = float(metodos.get('efectivo_bs', 0))
        total_efectivo_usd = float(metodos.get('efectivo_usd', 0))
        total_zelle = float(metodos.get('zelle', 0))
        total_pago_movil = float(metodos.get('movil', 0))
        total_transferencia = float(metodos.get('transferencia', 0))
        total_tarjeta = float(metodos.get('tarjeta', 0))

        saldo = obtener_saldo_actual()
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        cashier_name = session.get('username', 'Usuario')
        today = today_str

        opening_balance_cero = opening_balance if not cierre_realizado else 0
        total_bs_cero = total_bs if not cierre_realizado else 0
        total_usd_cero = total_usd if not cierre_realizado else 0
        total_efectivo_bs_cero = total_efectivo_bs if not cierre_realizado else 0
        total_efectivo_usd_cero = total_efectivo_usd if not cierre_realizado else 0
        total_zelle_cero = total_zelle if not cierre_realizado else 0
        total_pago_movil_cero = total_pago_movil if not cierre_realizado else 0
        total_transferencia_cero = total_transferencia if not cierre_realizado else 0
        total_tarjeta_cero = total_tarjeta if not cierre_realizado else 0

    except Exception as e:
        logger.error(f"Error en daily_closure GET: {e}")
        flash(f'Error al cargar datos: {e}', 'danger')
        sales = []
        total_bs = total_usd = 0
        total_efectivo_bs = total_efectivo_usd = total_zelle = total_pago_movil = total_transferencia = total_tarjeta = 0
        opening_balance = 0
        company = None
        rate = 40
        cashier_name = session.get('username', 'Usuario')
        saldo = {'bs': 0, 'usd': 0}
        opening_balance_cero = total_bs_cero = total_usd_cero = 0
        total_efectivo_bs_cero = total_efectivo_usd_cero = total_zelle_cero = total_pago_movil_cero = total_transferencia_cero = total_tarjeta_cero = 0

    return render_template('daily_closure_unified.html',
        sales=sales,
        total_ventas=len(sales),
        total_usd=total_usd,
        total_bs=total_bs,
        total_efectivo_bs=total_efectivo_bs,
        total_efectivo_usd=total_efectivo_usd,
        total_zelle=total_zelle,
        total_pago_movil=total_pago_movil,
        total_transferencia=total_transferencia,
        total_tarjeta=total_tarjeta,
        opening_balance=opening_balance,
        cierre_realizado=cierre_realizado,
        cashier_name=cashier_name,
        rate=rate,
        saldo_bs=saldo['bs'],
        saldo_usd=saldo['usd'],
        company=company,
        today=today,
        opening_balance_cero=opening_balance_cero,
        total_bs_cero=total_bs_cero,
        total_usd_cero=total_usd_cero,
        total_efectivo_bs_cero=total_efectivo_bs_cero,
        total_efectivo_usd_cero=total_efectivo_usd_cero,
        total_zelle_cero=total_zelle_cero,
        total_pago_movil_cero=total_pago_movil_cero,
        total_transferencia_cero=total_transferencia_cero,
        total_tarjeta_cero=total_tarjeta_cero,
        now=datetime.now()
    )

# ==================== VENTAS DEL MES ====================
@app.route('/ventas_mes')
@login_required
@permission_required('view_sales')
def ventas_mes():
    try:
        year = request.args.get('year', type=int, default=datetime.now().year)
        month = request.args.get('month', type=int, default=datetime.now().month)
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"
        sales = execute_query("""
            SELECT s.date, s.invoice_number, s.client_name,
                   s.total_usd, s.total_bs,
                   COALESCE(s.payment_method, 'Efectivo USD') as payment_method,
                   u.username as seller_name
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.date >= %s AND s.date < %s AND s.invoice_number > 0
            ORDER BY s.date DESC
        """, (start, end), fetch_all=True)
        total_usd = sum(float(s['total_usd']) for s in sales) if sales else 0
        total_bs = sum(float(s['total_bs']) for s in sales) if sales else 0
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
    except Exception as e:
        logger.error(f"Error en ventas_mes: {e}")
        flash(f'Error: {e}', 'danger')
        sales = []
        total_usd = total_bs = 0
        company = None
    meses = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    return render_template('ventas_mes.html',
                          sales=sales,
                          total_usd=total_usd,
                          total_bs=total_bs,
                          month=month,
                          year=year,
                          meses=meses,
                          company=company,
                          total_ventas=len(sales))

@app.route('/get_cash_fund')
@login_required
def get_cash_fund():
    try:
        opening_result = execute_query("SELECT value FROM config WHERE key_name = 'opening_balance'", fetch_one=True)
        rate = get_cached_exchange_rate()
        opening_balance = float(opening_result['value']) if opening_result else 0
        today = datetime.now().strftime('%Y-%m-%d')
        sales = execute_query("SELECT COALESCE(SUM(total_bs),0) as total FROM sales WHERE date LIKE %s AND invoice_number > 0", (today+'%',), fetch_one=True)
        total_ventas_bs = float(sales['total']) if sales else 0
        current_bs = opening_balance + total_ventas_bs
        current_usd = current_bs / rate if rate > 0 else 0
        return jsonify({'usd': round(current_usd, 2), 'bs': round(current_bs, 2)})
    except Exception as e:
        logger.error(f"Error en get_cash_fund: {e}")
        return jsonify({'usd': 0, 'bs': 0})

@app.route('/monthly_profit')
@login_required
@permission_required('view_sales')
def monthly_profit():
    try:
        month = request.args.get('month', type=int, default=datetime.now().month)
        year = request.args.get('year', type=int, default=datetime.now().year)
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"
        sales = execute_query("SELECT * FROM sales WHERE date >= %s AND date < %s AND invoice_number > 0", (start, end), fetch_all=True)
        purchases = execute_query("SELECT * FROM purchases WHERE date >= %s AND date < %s", (start, end), fetch_all=True)
        total_ventas = sum(float(s['total_usd']) for s in sales) if sales else 0
        costo_ventas = 0
        for sale in sales:
            items = json.loads(sale['items'])
            for item in items:
                prod = execute_query("SELECT cost FROM products WHERE id = %s", (item['id'],), fetch_one=True)
                if prod:
                    costo_ventas += float(prod['cost']) * item['quantity']
        ganancia = total_ventas - costo_ventas
        margen = (ganancia / total_ventas * 100) if total_ventas > 0 else 0
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
    except Exception as e:
        logger.error(f"Error en monthly_profit: {e}")
        flash(f'Error: {e}', 'danger')
        sales = []
        purchases = []
        total_ventas = ganancia = margen = 0
        company = None
    meses = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    return render_template('monthly_profit.html', sales=sales, purchases=purchases, total_ventas=total_ventas, ganancia=ganancia, margen=margen, month=month, year=year, meses=meses, company=company)

@app.route('/print_inventory')
@login_required
@permission_required('inventory')
def print_inventory():
    try:
        products = execute_query("SELECT * FROM products WHERE activo = 1 ORDER BY name", fetch_all=True)
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        for p in products:
            p['cost'] = float(p['cost']) if p['cost'] else 0
            p['price'] = float(p['price']) if p['price'] else 0
            p['stock'] = float(p['stock']) if p['stock'] else 0
    except Exception as e:
        logger.error(f"Error en print_inventory: {e}")
        flash(f'Error: {e}', 'danger')
        products = []
        company = None
    return render_template('print_inventory.html', products=products, company=company, current_date=datetime.now().strftime('%d/%m/%Y %H:%M:%S'))

# ==================== HISTORIAL DE FACTURACIÓN ====================
@app.route('/historial_facturacion')
@login_required
@permission_required('view_sales')
def historial_facturacion():
    try:
        sales = execute_query("""SELECT s.*, u.username as user_name FROM sales s LEFT JOIN users u ON s.user_id = u.id WHERE s.invoice_number > 0 ORDER BY s.id DESC LIMIT 500""", fetch_all=True)
        if sales is None:
            sales = []
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        for sale in sales:
            try:
                if sale.get('items'):
                    if isinstance(sale['items'], str):
                        sale['items_list'] = json.loads(sale['items'])
                    else:
                        sale['items_list'] = sale['items']
                else:
                    sale['items_list'] = []
            except:
                sale['items_list'] = []
            sale['subtotal'] = float(sale['subtotal']) if sale.get('subtotal') else 0.0
            sale['discount'] = float(sale['discount']) if sale.get('discount') else 0.0
            sale['tax'] = float(sale['tax']) if sale.get('tax') else 0.0
            sale['total_usd'] = float(sale['total_usd']) if sale.get('total_usd') else 0.0
            sale['total_bs'] = float(sale['total_bs']) if sale.get('total_bs') else 0.0
            sale['invoice_number'] = int(sale['invoice_number']) if sale.get('invoice_number') else 0
        return render_template('historial_facturacion.html', sales=sales, company=company, datetime=datetime)
    except Exception as e:
        logger.error(f"Error en historial_facturacion: {str(e)}")
        flash(f'Error al cargar historial de facturación: {str(e)}', 'danger')
        return render_template('historial_facturacion.html', sales=[], company=None, datetime=datetime)

# ==================== IMPRIMIR FACTURA ====================
@app.route('/imprimir_factura/<int:invoice_number>')
@login_required
def imprimir_factura(invoice_number):
    logger.info(f"📄 IMPRIMIR FACTURA: N° {invoice_number} - Solicitada por {session.get('username')}")
    try:
        sale = execute_query("""
            SELECT s.*, u.username as user_name
            FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.invoice_number = %s AND s.invoice_number > 0
        """, (invoice_number,), fetch_one=True)

        if not sale:
            logger.warning(f"⚠️ Factura N° {invoice_number} NO encontrada")
            flash('Factura no encontrada', 'danger')
            return redirect(url_for('historial_facturacion'))

        logger.info(f"✅ Factura N° {invoice_number} encontrada. Renderizando...")

        items = json.loads(sale['items']) if sale['items'] else []
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        rate = get_cached_exchange_rate()
        total_bs = float(sale['total_bs']) if sale.get('total_bs') else (float(sale['total_usd']) * rate)

        for item in items:
            product = execute_query("SELECT sale_type, unit FROM products WHERE id = %s", (item['id'],), fetch_one=True)
            item['sale_type'] = product['sale_type'] if product else 'unit'
            item['unit'] = product['unit'] if product and product.get('unit') else 'unidad'
            item['price'] = float(item['price']) if item.get('price') else 0
            item['quantity'] = float(item['quantity']) if item.get('quantity') else 0

        pagos = {}
        if sale.get('payment_details'):
            try:
                pagos = json.loads(sale['payment_details']) if isinstance(sale['payment_details'], str) else sale['payment_details']
            except:
                pagos = {}
        if not pagos:
            pagos = {
                'efectivo_bs': float(sale.get('pago_efectivo_bs', 0)),
                'efectivo_usd': float(sale.get('pago_efectivo_usd', 0)),
                'zelle': float(sale.get('pago_zelle', 0)),
                'movil': float(sale.get('pago_movil', 0)),
                'transferencia': float(sale.get('pago_transferencia', 0)),
                'tarjeta': float(sale.get('pago_tarjeta', 0)),
                'nota_credito_usd': float(sale.get('pago_nota_credito_usd', 0)) if 'pago_nota_credito_usd' in sale else 0,
                'devolucion_usd': float(sale.get('pago_devolucion_usd', 0)) if 'pago_devolucion_usd' in sale else 0,
                'cambio_usd': float(sale.get('cambio_usd', 0)),
                'cambio_bs': float(sale.get('cambio_bs', 0)),
                'tasa': rate
            }
        else:
            pagos.setdefault('nota_credito_usd', float(sale.get('pago_nota_credito_usd', 0)) if 'pago_nota_credito_usd' in sale else 0)
            pagos.setdefault('devolucion_usd', float(sale.get('pago_devolucion_usd', 0)) if 'pago_devolucion_usd' in sale else 0)
            pagos.setdefault('cambio_usd', float(sale.get('cambio_usd', 0)))
            pagos.setdefault('cambio_bs', float(sale.get('cambio_bs', 0)))
            pagos.setdefault('tasa', rate)

        return render_template('invoice.html',
                              invoice_number=sale['invoice_number'],
                              client_name=sale['client_name'],
                              client_id=sale['client_id'],
                              client_phone=sale['client_phone'] or '',
                              client_address=sale['client_address'] or '',
                              cart=items,
                              subtotal=float(sale['subtotal']) if sale.get('subtotal') else 0,
                              discount=float(sale['discount']) if sale.get('discount') else 0,
                              tax=float(sale['tax']) if sale.get('tax') else 0,
                              total_usd=float(sale['total_usd']) if sale.get('total_usd') else 0,
                              total_bs=total_bs,
                              payment_method=sale['payment_method'],
                              current_date=sale['date'],
                              company=company,
                              session=session,
                              datetime=datetime,
                              pagos=pagos,
                              rate=rate)
    except Exception as e:
        logger.error(f"❌ ERROR al imprimir factura N° {invoice_number}: {e}")
        import traceback
        traceback.print_exc()
        flash(f'Error al cargar factura: {str(e)}', 'danger')
        return redirect(url_for('historial_facturacion'))

@app.route('/insertar_factura_prueba')
@login_required
def insertar_factura_prueba():
    try:
        next_inv_result = execute_query("SELECT value FROM config WHERE key_name = 'next_invoice'", fetch_one=True)
        next_inv = int(next_inv_result['value']) if next_inv_result else 1
        fecha_actual = datetime.now().isoformat()
        items_json = '[{"id":1,"name":"Producto Prueba","price":10,"quantity":2,"sale_type":"unit"}]'
        execute_query("""INSERT INTO sales (invoice_number, date, user_id, client_id, client_name, client_phone, client_address, items, subtotal, discount, tax, total_usd, total_bs, payment_method) VALUES (%s, %s, %s, 'V12345678', 'CLIENTE PRUEBA', '04121234567', 'DIRECCION PRUEBA Calle 1', %s, 20, 0, 3.2, 23.2, 846.80, 'Efectivo USD')""", (next_inv, fecha_actual, session['user_id'], items_json), commit=True)
        execute_query("UPDATE config SET value = %s WHERE key_name = 'next_invoice'", (str(next_inv + 1),), commit=True)
        flash(f'[OK] Factura de prueba N° {next_inv} insertada correctamente', 'success')
        return redirect(url_for('historial_facturacion'))
    except Exception as e:
        logger.error(f"Error en insertar_factura_prueba: {e}")
        flash(f'[ERROR] Error al insertar factura de prueba: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

# ==================== CONFIGURACIÓN ====================
@app.route('/exchange_rate')
@login_required
@permission_required('exchange_rate')
def exchange_rate():
    try:
        rate = execute_query("SELECT value FROM config WHERE key_name = 'exchange_rate'", fetch_one=True)
        discount = execute_query("SELECT value FROM config WHERE key_name = 'discount_percent'", fetch_one=True)
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        rate_history = execute_query("SELECT * FROM exchange_rate_history ORDER BY created_at DESC LIMIT 50", fetch_all=True)
        exchange_rate_val = float(rate['value']) if rate else 36.50
        discount_val = float(discount['value']) if discount else 5.0
    except Exception as e:
        logger.error(f"Error en exchange_rate: {e}")
        flash(f'Error: {e}', 'danger')
        exchange_rate_val = 36.50
        discount_val = 5.0
        company = None
        rate_history = []
    return render_template('exchange_rate.html', exchange_rate=exchange_rate_val, discount_percent=discount_val, company=company, rate_history=rate_history)

@app.route('/update_exchange_rate', methods=['POST'])
@login_required
@permission_required('exchange_rate')
def update_exchange_rate():
    nueva_tasa = float(request.form['exchange_rate'])
    nuevo_descuento = float(request.form['discount_percent'])
    try:
        tasa_anterior_result = execute_query("SELECT value FROM config WHERE key_name = 'exchange_rate'", fetch_one=True)
        tasa_anterior = float(tasa_anterior_result['value']) if tasa_anterior_result else 36.50
        execute_query("UPDATE config SET value = %s WHERE key_name = 'exchange_rate'", (str(nueva_tasa),), commit=True)
        execute_query("UPDATE config SET value = %s WHERE key_name = 'discount_percent'", (str(nuevo_descuento),), commit=True)
        global _cache_exchange_rate, _cache_discount
        _cache_exchange_rate = {'rate': None, 'timestamp': 0}
        _cache_discount = {'percent': None, 'timestamp': 0}
        execute_query("INSERT INTO exchange_rate_history (tasa_anterior, tasa_nueva, descuento, usuario, fecha) VALUES (%s, %s, %s, %s, %s)", (tasa_anterior, nueva_tasa, nuevo_descuento, session['username'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')), commit=True)
        log_action(session['user_id'], session['username'], 'UPDATE_RATE', f"Tasa: {tasa_anterior} -> {nueva_tasa}, Descuento: {nuevo_descuento}%")
        flash(f'[OK] Tasa y descuento actualizados. Tasa anterior: Bs. {tasa_anterior:.2f} Nueva: Bs. {nueva_tasa:.2f}', 'success')
    except Exception as e:
        logger.error(f"Error en update_exchange_rate: {e}")
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('exchange_rate'))

# ==================== ROLES Y USUARIOS ====================
@app.route('/roles')
@login_required
@permission_required('roles')
def manage_roles():
    try:
        roles = execute_query("SELECT * FROM roles", fetch_all=True)
        roles_list = []
        for role in roles:
            perms = json.loads(role['permissions'])
            roles_list.append({'name': role['name'], 'permissions': perms})
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
    except Exception as e:
        logger.error(f"Error en manage_roles: {e}")
        flash(f'Error: {e}', 'danger')
        roles_list = []
        company = None
    return render_template('roles.html', roles=roles_list, company=company)

@app.route('/add_role', methods=['POST'])
@login_required
@permission_required('roles')
def add_role():
    name = request.form['role_name']
    perms = {"inventory": False, "edit_products": False, "buy": False, "sell": False, "users": False, "roles": False, "reports": False, "exchange_rate": False, "cash_closure": False, "view_sales": False}
    try:
        execute_query("INSERT INTO roles (name, permissions) VALUES (%s, %s)", (name, json.dumps(perms)), commit=True)
        log_action(session['user_id'], session['username'], 'ADD_ROLE', f"Rol: {name}")
        flash(f'[OK] Rol {name} creado', 'success')
    except Exception as e:
        logger.error(f"Error en add_role: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('manage_roles'))

@app.route('/update_role_permissions', methods=['POST'])
@login_required
@permission_required('roles')
def update_role_permissions():
    role_name = request.form['role_name']
    perms = {
        "inventory": 'inventory' in request.form,
        "edit_products": 'edit_products' in request.form,
        "buy": 'buy' in request.form,
        "sell": 'sell' in request.form,
        "users": 'users' in request.form,
        "roles": 'roles' in request.form,
        "reports": 'reports' in request.form,
        "exchange_rate": 'exchange_rate' in request.form,
        "cash_closure": 'cash_closure' in request.form,
        "view_sales": 'view_sales' in request.form
    }
    try:
        execute_query("UPDATE roles SET permissions = %s WHERE name = %s", (json.dumps(perms), role_name), commit=True)
        log_action(session['user_id'], session['username'], 'UPDATE_ROLE', f"Rol: {role_name}")
        flash(f'[OK] Permisos de {role_name} actualizados', 'success')
    except Exception as e:
        logger.error(f"Error en update_role_permissions: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('manage_roles'))

@app.route('/users')
@login_required
@permission_required('users')
def manage_users():
    try:
        users = execute_query("SELECT id, username, email, role, failed_attempts, locked_until FROM users", fetch_all=True)
        roles = execute_query("SELECT name FROM roles", fetch_all=True)
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
    except Exception as e:
        logger.error(f"Error en manage_users: {e}")
        flash(f'Error: {e}', 'danger')
        users = []
        roles = []
        company = None
    return render_template('users.html', users=users, roles=roles, time=time, company=company)

@app.route('/add_user', methods=['POST'])
@login_required
@permission_required('users')
def add_user():
    username = request.form['username']
    email = request.form.get('email', '')
    password = request.form['password']
    role = request.form['role']
    errores = validar_contraseña_segura(password)
    if errores:
        for error in errores:
            flash(f'[ERROR] {error}', 'danger')
        return redirect(url_for('manage_users'))
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    try:
        execute_query("INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)", (username, email, hashed.decode(), role), commit=True)
        log_action(session['user_id'], session['username'], 'ADD_USER', f"Usuario: {username}, Rol: {role}")
        flash(f'[OK] Usuario {username} creado', 'success')
    except:
        flash('[ERROR] El usuario ya existe', 'danger')
    return redirect(url_for('manage_users'))

@app.route('/change_user_password/<int:user_id>', methods=['POST'])
@login_required
@permission_required('users')
def change_user_password(user_id):
    if session.get('role') != 'Dueño':
        flash('[ERROR] Solo el Dueño puede cambiar contraseñas de otros usuarios', 'danger')
        return redirect(url_for('manage_users'))
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')
    if new_password != confirm_password:
        flash('[ERROR] Las contraseñas no coinciden', 'danger')
        return redirect(url_for('manage_users'))
    errores = validar_contraseña_segura(new_password)
    if errores:
        for error in errores:
            flash(f'[ERROR] {error}', 'danger')
        return redirect(url_for('manage_users'))
    try:
        target_user = execute_query("SELECT id, username FROM users WHERE id = %s", (user_id,), fetch_one=True)
        if not target_user:
            flash('[ERROR] Usuario no encontrado', 'danger')
            return redirect(url_for('manage_users'))
        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        execute_query("UPDATE users SET password = %s, failed_attempts = 0, locked_until = 0 WHERE id = %s", (hashed, user_id), commit=True)
        log_action(session['user_id'], session['username'], 'CHANGE_PASSWORD', f"Cambió contraseña del usuario: {target_user['username']} (ID: {user_id})")
        flash(f'[OK] Contraseña de "{target_user["username"]}" actualizada correctamente. Se desbloqueó la cuenta.', 'success')
    except Exception as e:
        logger.error(f"Error en change_user_password: {e}")
        flash(f'[ERROR] Error al cambiar contraseña: {str(e)}', 'danger')
    return redirect(url_for('manage_users'))

@app.route('/unlock_user/<int:user_id>')
@login_required
@permission_required('users')
def unlock_user(user_id):
    try:
        user = execute_query("SELECT username FROM users WHERE id = %s", (user_id,), fetch_one=True)
        execute_query("UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE id = %s", (user_id,), commit=True)
        log_action(session['user_id'], session['username'], 'UNLOCK_USER', f"Usuario desbloqueado: {user['username']}")
        flash('[OK] Usuario desbloqueado', 'success')
    except Exception as e:
        logger.error(f"Error en unlock_user: {e}")
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('manage_users'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
@permission_required('users')
def delete_user(user_id):
    password = request.form.get('confirm_password')
    if not password:
        flash('Se requiere su contraseña', 'danger')
        return redirect(url_for('manage_users'))
    try:
        owner = execute_query("SELECT password FROM users WHERE id = %s", (session['user_id'],), fetch_one=True)
        if not bcrypt.checkpw(password.encode(), owner['password'].encode()):
            flash('[ERROR] Contraseña incorrecta', 'danger')
            return redirect(url_for('manage_users'))
        if user_id == session['user_id']:
            flash('[ERROR] No puede eliminarse a sí mismo', 'danger')
            return redirect(url_for('manage_users'))
        user = execute_query("SELECT username FROM users WHERE id = %s", (user_id,), fetch_one=True)
        execute_query("DELETE FROM users WHERE id = %s", (user_id,), commit=True)
        log_action(session['user_id'], session['username'], 'DELETE_USER', f"Usuario eliminado: {user['username']}")
        flash('[OK] Usuario eliminado', 'success')
    except Exception as e:
        logger.error(f"Error en delete_user: {e}")
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('manage_users'))

# ==================== RUTAS DE DIAGNÓSTICO Y CORRECCIÓN ====================
@app.route('/corregir_stocks_negativos')
@login_required
def corregir_stocks_negativos():
    if session.get('role') != 'Dueño':
        flash('No autorizado', 'danger')
        return redirect(url_for('inventory'))
    try:
        negativos_before = execute_query("SELECT COUNT(*) as count FROM products WHERE stock < 0 AND activo = 1", fetch_one=True)
        execute_query("UPDATE products SET stock = 0, stock_status = 'AGOTADO' WHERE stock < 0 AND activo = 1", commit=True)
        flash(f'[OK] Se corrigieron {negativos_before["count"] if negativos_before else 0} productos con stock negativo a CERO', 'success')
        eliminados = limpiar_o_reactivar_productos_agotados()
        if eliminados > 0:
            flash(f'[INFO] Además, se eliminaron {eliminados} productos agotados automáticamente', 'info')
    except Exception as e:
        logger.error(f"Error en corregir_stocks_negativos: {e}")
        flash(f'[ERROR] Error: {str(e)}', 'danger')
    return redirect(url_for('inventory'))

@app.route('/reset_caja')
@login_required
def reset_caja():
    if session.get('role') != 'Dueño':
        flash('No autorizado', 'danger')
        return redirect(url_for('dashboard'))
    try:
        execute_query("UPDATE config SET value = '0' WHERE key_name = 'opening_balance'", commit=True)
        execute_query("UPDATE saldo_caja SET saldo_bs = 0, saldo_usd = 0 WHERE id = 1", commit=True)
        flash('[OK] Fondo de caja reiniciado a CERO exitosamente', 'success')
    except Exception as e:
        logger.error(f"Error en reset_caja: {e}")
        flash(f'[ERROR] Error: {e}', 'danger')
    return redirect(url_for('opening_balance'))

# ==================== HISTORIAL DE CAJA ====================
@app.route('/historial_caja')
@login_required
@permission_required('view_sales')
def historial_caja():
    try:
        limite = request.args.get('limite', 100, type=int)
        tipo = request.args.get('tipo', None)
        desde = request.args.get('desde', None)
        hasta = request.args.get('hasta', None)
        movimientos = obtener_movimientos_caja(limite=limite, tipo=tipo, desde=desde, hasta=hasta)
        saldo = obtener_saldo_actual()
        company = execute_query("SELECT * FROM company WHERE id = 1", fetch_one=True)
        for mov in movimientos:
            mov['monto_bs'] = float(mov['monto_bs']) if mov['monto_bs'] else 0
            mov['monto_usd'] = float(mov['monto_usd']) if mov['monto_usd'] else 0
            mov['tasa_usd'] = float(mov['tasa_usd']) if mov['tasa_usd'] else 0
        return render_template('historial_caja.html',
                              movimientos=movimientos,
                              returns=movimientos,
                              saldo_bs=saldo['bs'],
                              saldo_usd=saldo['usd'],
                              company=company,
                              datetime=datetime)
    except Exception as e:
        logger.error(f"Error en historial_caja: {e}")
        flash(f'[ERROR] Error al cargar historial de caja: {str(e)}', 'danger')
        return render_template('historial_caja.html',
                              movimientos=[],
                              returns=[],
                              saldo_bs=0,
                              saldo_usd=0,
                              company=None,
                              datetime=datetime)

@app.route('/debug_ventas')
@login_required
def debug_ventas():
    if session.get('role') != 'Dueño':
        flash('No autorizado', 'danger')
        return redirect(url_for('dashboard'))
    try:
        ventas = execute_query("""SELECT id, invoice_number, date, client_name, total_usd, total_bs FROM sales WHERE invoice_number > 0 ORDER BY id DESC LIMIT 20""", fetch_all=True)
        html = """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>Diagnóstico de Ventas - RENAVEN</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background: #f8fafc; }
            h1 { color: #001C47; }
            table { border-collapse: collapse; width: 100%; background: white; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background: #6BB6EC; color: #000; }
            .success { background: #d1fae5; color: #065f46; padding: 10px; border-radius: 8px; }
            .error { background: #fee2e2; color: #991b1b; padding: 10px; border-radius: 8px; }
            .info { background: #dbeafe; color: #1e40af; padding: 10px; border-radius: 8px; margin-bottom: 20px; }
        </style>
        </head>
        <body>
            <h1>Diagnóstico de Ventas - RENAVEN</h1>
        """
        html += f"""<div class="info"><strong>Configuración:</strong><br>Total de ventas en base de datos (facturas positivas): {len(ventas)}</div>"""
        if ventas:
            html += "<h2>Últimas facturas registradas:</h2>"
            html += "<table><tr><th>ID</th><th>Invoice N°</th><th>Fecha</th><th>Cliente</th><th>Total USD</th><th>Total Bs</th></tr>"
            for venta in ventas:
                html += f"<tr><td>{venta['id']}</td><td>{venta['invoice_number']}</td><td>{venta['date']}</td><td>{venta['client_name']}</td><td>{venta['total_usd']}</td><td>{venta['total_bs']}</td></tr>"
            html += "</table>"
        else:
            html += "<div class='error'><strong>No hay facturas registradas en la base de datos!</strong><br>Use el botón de abajo para insertar una factura de prueba.</div>"
        html += """
            <br>
            <div style="margin-top: 20px;">
                <a href="/historial_facturacion" style="background: #001C47; color: white; padding: 10px 20px; text-decoration: none; border-radius: 50px; margin-right: 10px;">Ir a Historial de Facturación</a>
                <a href="/insertar_factura_prueba" style="background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 50px; margin-right: 10px;">Insertar Factura de Prueba</a>
                <a href="/facturacion" style="background: #6BB6EC; color: white; padding: 10px 20px; text-decoration: none; border-radius: 50px; margin-right: 10px;">Ir a Facturación</a>
                <a href="/dashboard" style="background: #6c757d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 50px;">Volver al Dashboard</a>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        logger.error(f"Error en debug_ventas: {e}")
        return f"<div class='error'>Error: {str(e)}</div>"

# ==================== MANEJO DE ERRORES ====================
@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Error 500: {error}")
    flash('[ERROR] Error interno del servidor. Por favor contacte al administrador.', 'danger')
    return redirect(url_for('dashboard'))

@app.errorhandler(403)
def forbidden(error):
    flash('[ERROR] No tiene permiso para acceder a esta página', 'danger')
    return redirect(url_for('dashboard'))

# ==================== SCHEDULER PARA TASA BCV ====================
def scheduled_bcv_update():
    with app.app_context():
        logger.info("[SCHEDULER] Ejecutando actualización programada de tasa BCV...")
        update_bcv_rate()
        logger.info("[SCHEDULER] Actualización programada de tasa BCV completada")

def start_scheduler():
    def run_scheduled():
        while True:
            time.sleep(BCV_CACHE_TTL)
            scheduled_bcv_update()
    scheduler_thread = threading.Thread(target=run_scheduled, daemon=True)
    scheduler_thread.start()
    logger.info("[SCHEDULER] Scheduler de tasa BCV iniciado (cada 6 horas)")

# ==================== INICIALIZACIÓN PARA RENDER (GUNICORN) ====================
with app.app_context():
    try:
        init_connection_pool()
        init_db()
        print("✅ Base de datos inicializada al arrancar la app")
    except Exception as e:
        print(f"❌ Error al inicializar la base de datos: {e}")
# ==============================================================================
# ==================== INICIO DE LA APLICACIÓN ====================
if __name__ == '__main__':
    import webbrowser

    try:
        # ========== PASO 1: ARRANCAR MYSQL PORTABLE ==========
        mysql_server_path = os.path.join(bundle_dir, 'mysql_server', 'bin', 'mysqld.exe')
        if os.path.exists(mysql_server_path):
            print("🔍 Detectado MySQL portable en mysql_server/")
            if not asegurar_mysql():
                print("❌ No se pudo arrancar MySQL portable.")
                print("💡 Alternativa: Abre XAMPP manualmente y vuelve a intentar.")
                sys.exit(1)
            print("⏳ Esperando 3 segundos adicionales...")
            time.sleep(3)
        else:
            print("ℹ️  No se encontró mysql_server/. Usando MySQL externo (XAMPP).")

        # ========== PASO 2: CREAR DB SI NO EXISTE ==========
        print("🔌 Verificando base de datos...")
        ensure_database_exists()

        # ========== PASO 3: INICIALIZAR POOL Y TABLAS ==========
        print("🔌 Conectando a MySQL...")
        init_connection_pool()
        print("✅ Conexión MySQL exitosa")

        print("📊 Inicializando base de datos...")
        init_db()
        print("✅ Base de datos lista")

        try:
            update_bcv_rate()
        except Exception as e:
            print(f"⚠️ No se pudo actualizar tasa BCV: {e}")
        start_scheduler()

        print("\n" + "="*60)
        print("[OK] SISTEMA CON MYSQL PORTÁTIL - VERSIÓN 3.0 COMPLETA")
        print("Base de datos: renaven_db en modo portable")
        print("http://127.0.0.1:5000")
        print("="*60)
        print("USUARIOS CREADOS (CONTRASEÑAS SEGURAS):")
        print("   dueño      | Admin123!@#Renaven      | Dueño")
        print("   supervisor | Super2024!@#Seguro      | Supervisor")
        print("   cajera     | Cajera$2024Segura       | Cajera")
        print("   almacen    | Almacen*2024Safe        | Almacenista")
        print("   jonathan   | Jonathan$2024Secure!    | Dueño")
        print("="*60)
        print("TASA BCV AUTOMÁTICA EN TIEMPO REAL")
        print("   Actualización cada 6 horas")
        print("   Endpoint /api/bcv-rate")
        print("="*60)
        print("🌐 ABRIENDO NAVEGADOR EN 2 SEGUNDOS...")
        print("="*60)

        # ========== PASO 4: ABRIR NAVEGADOR ==========
        def abrir_navegador():
            time.sleep(2)
            url = 'http://127.0.0.1:5000'
            try:
                chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
                ]
                chrome = next((p for p in chrome_paths if os.path.exists(p)), None)
                if chrome:
                    print(f"[INFO] Abriendo Chrome desde: {chrome}")
                    webbrowser.register('chrome', None, webbrowser.GenericBrowser(chrome))
                    webbrowser.get('chrome').open(url)
                else:
                    print("[INFO] No se encontró Chrome, usando navegador predeterminado.")
                    webbrowser.open(url)
            except Exception as e:
                print(f"[ERROR] No se pudo abrir navegador: {e}")
                print(f"👉 Abre manualmente: {url}")

        threading.Thread(target=abrir_navegador, daemon=True).start()

    except Exception as e:
        print(f"[ERROR] Error al iniciar: {e}")
        print("\nVERIFIQUE:")
        print("1. Tener la carpeta 'mysql_server' junto al .exe.")
        print("2. Permisos de escritura en la carpeta del .exe.")
        import traceback
        traceback.print_exc()

        # Guardar error en log para diagnóstico cuando console=False
        try:
            error_log = os.path.join(base_dir, "renaven_error.log")
            with open(error_log, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now()}] {e}\n")
                f.write(traceback.format_exc())
        except Exception:
            pass

        sys.exit(1)

    # ========== PASO 5: ARRANCAR FLASK ==========
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=False,
        use_reloader=False,
        threaded=True
    )
