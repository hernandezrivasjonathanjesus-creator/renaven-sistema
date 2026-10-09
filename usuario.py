import bcrypt
import pymysql
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Colores ANSI para la consola
class Colors:
    AZUL = '\033[94m'
    VERDE = '\033[92m'
    ROJO = '\033[91m'
    AMARILLO = '\033[93m'
    BLANCO = '\033[97m'
    GRIS = '\033[90m'
    RESET = '\033[0m'
    NEGRO = '\033[30m'
    FONDO_GRIS = '\033[100m'

print(f"{Colors.AZUL}{'='*50}{Colors.RESET}")
print(f"{Colors.AZUL}🔧 CREANDO USUARIOS PARA RENAVEN{Colors.RESET}")
print(f"{Colors.AZUL}{'='*50}{Colors.RESET}")

# Configuración de la base de datos (usando variables de entorno o valores por defecto)
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'renaven_db')
}

# Conectar a la base de datos
try:
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    print(f"{Colors.VERDE}✅ Conexión a MySQL exitosa{Colors.RESET}")
    print(f"   Host: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"   Base de datos: {DB_CONFIG['database']}")
except Exception as e:
    print(f"{Colors.ROJO}❌ Error de conexión: {e}{Colors.RESET}")
    exit()

# Limpiar la tabla users
cursor.execute("DELETE FROM users")
print(f"{Colors.AMARILLO}🗑️ Usuarios anteriores eliminados{Colors.RESET}")

# Lista de usuarios con sus contraseñas
usuarios = [
    ('dueño', 'admin@renaven.com', 'admin123', 'Dueño'),
    ('supervisor', 'supervisor@renaven.com', 'super123', 'Supervisor'),
    ('cajera', 'cajera@renaven.com', 'cajera123', 'Cajera'),
    ('almacen', 'almacen@renaven.com', 'almacen123', 'Almacenista'),
    ('jonathan', 'hernandezrivasjonathanjesus@gmail.com', 'jonathan123', 'Dueño')
]

print(f"\n{Colors.AZUL}📝 Creando usuarios...{Colors.RESET}")
print(f"{Colors.GRIS}{'-'*40}{Colors.RESET}")

# Insertar cada usuario
for username, email, password, role in usuarios:
    # Generar el hash de la contraseña
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    
    # Insertar en la base de datos
    cursor.execute("""
        INSERT INTO users (username, email, password, role, failed_attempts, locked_until) 
        VALUES (%s, %s, %s, %s, 0, 0)
    """, (username, email, hashed, role))
    
    print(f"{Colors.VERDE}✅{Colors.RESET} {Colors.BLANCO}{username}{Colors.RESET} | {Colors.GRIS}contraseña:{Colors.RESET} {Colors.AMARILLO}{password}{Colors.RESET} | {Colors.AZUL}rol:{Colors.RESET} {Colors.BLANCO}{role}{Colors.RESET}")

# Guardar los cambios
conn.commit()
print(f"{Colors.GRIS}{'-'*40}{Colors.RESET}")
print(f"{Colors.VERDE}🎉 USUARIOS CREADOS EXITOSAMENTE!{Colors.RESET}")

# Verificar los usuarios creados
cursor.execute("SELECT id, username, role FROM users")
usuarios_db = cursor.fetchall()

print(f"\n{Colors.AZUL}📋 USUARIOS EN LA BASE DE DATOS:{Colors.RESET}")
print(f"{Colors.GRIS}{'-'*40}{Colors.RESET}")
for user in usuarios_db:
    print(f"   {Colors.BLANCO}ID:{Colors.RESET} {user[0]} | {Colors.BLANCO}Usuario:{Colors.RESET} {Colors.AZUL}{user[1]}{Colors.RESET} | {Colors.BLANCO}Rol:{Colors.RESET} {Colors.VERDE}{user[2]}{Colors.RESET}")

# Cerrar conexión
cursor.close()
conn.close()

print(f"\n{Colors.AZUL}{'='*50}{Colors.RESET}")
print(f"{Colors.VERDE}✅ LISTO! Ahora ejecuta: python app.py{Colors.RESET}")
print(f"   {Colors.BLANCO}Usuario:{Colors.RESET} {Colors.AZUL}dueño{Colors.RESET}")
print(f"   {Colors.BLANCO}Contraseña:{Colors.RESET} {Colors.AMARILLO}admin123{Colors.RESET}")
print(f"{Colors.AZUL}{'='*50}{Colors.RESET}")