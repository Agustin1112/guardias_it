from math import ceil
from flask import Flask, render_template, request, redirect, abort, g
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin, current_user
)
import os
import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

ITEMS_PER_PAGE = 10

app = Flask(__name__)
app.secret_key = "super_secreto_guardias"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# --------------------------------------------------
# DB
# --------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db:
        db.close()

# --------------------------------------------------
# INIT DB (CLAVE PARA RENDER)
# --------------------------------------------------
def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            es_admin INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            debe_cambiar_password INTEGER DEFAULT 1
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS guardias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quien_llamo TEXT,
            fecha_llamado TEXT,
            quien_guardia TEXT,
            descripcion TEXT,
            prioridad TEXT,
            fecha_registro TEXT,
            estado TEXT,
            resolucion TEXT,
            fecha_resolucion TEXT,
            derivado INTEGER DEFAULT 0,
            derivado_a TEXT
        )
    """)

    admin = db.execute(
        "SELECT id FROM usuarios WHERE username = 'admin'"
    ).fetchone()

    if not admin:
        db.execute("""
            INSERT INTO usuarios (username, password, es_admin, activo, debe_cambiar_password)
            VALUES (?, ?, 1, 1, 1)
        """, (
            "admin",
            generate_password_hash("admin123")
        ))

    db.commit()

# --------------------------------------------------
# USUARIOS
# --------------------------------------------------
class User(UserMixin):
    def __init__(self, id, username, es_admin):
        self.id = id
        self.username = username
        self.es_admin = es_admin

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    user = db.execute(
        "SELECT * FROM usuarios WHERE id = ?", (user_id,)
    ).fetchone()

    if user:
        return User(user["id"], user["username"], user["es_admin"])
    return None

# --------------------------------------------------
# LOGIN
# --------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        user = db.execute("""
            SELECT * FROM usuarios
            WHERE username = ? AND activo = 1
        """, (request.form["username"],)).fetchone()

        if user and check_password_hash(user["password"], request.form["password"]):
            login_user(User(user["id"], user["username"], user["es_admin"]))

            if user["debe_cambiar_password"]:
                return redirect("/cambiar-password")

            return redirect("/")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")

# --------------------------------------------------
# CAMBIO DE PASSWORD OBLIGATORIO
# --------------------------------------------------
@app.route("/cambiar-password", methods=["GET", "POST"])
@login_required
def cambiar_password():
    if request.method == "POST":
        nueva = request.form["password"]

        db = get_db()
        db.execute("""
            UPDATE usuarios
            SET password = ?, debe_cambiar_password = 0
            WHERE id = ?
        """, (
            generate_password_hash(nueva),
            current_user.id
        ))
        db.commit()

        return redirect("/")

    return render_template("cambiar_password.html")

# --------------------------------------------------
# INDEX
# --------------------------------------------------
@app.route("/")
@login_required
def index():
    db = get_db()

    guardia_filtro = request.args.get("guardia")
    estado_filtro = request.args.get("estado")
    resueltos_filtro = request.args.get("resueltos")

    page = int(request.args.get("page", 1))
    per_page = ITEMS_PER_PAGE

    where = []
    params = []

    if not current_user.es_admin:
        where.append("quien_guardia = ?")
        params.append(current_user.username)

    if current_user.es_admin and guardia_filtro:
        where.append("quien_guardia = ?")
        params.append(guardia_filtro)

    if estado_filtro:
        where.append("estado = ?")
        params.append(estado_filtro)

    if resueltos_filtro == "hoy":
        where.append("DATE(fecha_resolucion) = DATE('now')")
    elif resueltos_filtro == "semana":
        where.append("fecha_resolucion >= DATE('now','-7 day')")

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    query = f"""
        SELECT *
        FROM guardias
        {where_sql}
        ORDER BY
            CASE prioridad
                WHEN 'Alta' THEN 1
                WHEN 'Media' THEN 2
                WHEN 'Baja' THEN 3
            END,
            fecha_llamado DESC
    """

    guardias_all = db.execute(query, params).fetchall()

    total = len(guardias_all)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    guardias_pag = guardias_all[start:start + per_page]

    now = datetime.now()
    guardias_final = []
    for g in guardias_pag:
        g = dict(g)
        fecha_registro = datetime.strptime(g["fecha_registro"], "%Y-%m-%d %H:%M:%S.%f")
        g["recent"] = fecha_registro > now - timedelta(minutes=10)
        guardias_final.append(g)

    guardias_disponibles = []
    if current_user.es_admin:
        guardias_disponibles = db.execute("""
            SELECT DISTINCT quien_guardia
            FROM guardias
            ORDER BY quien_guardia
        """).fetchall()

    return render_template(
        "index.html",
        guardias=guardias_final,
        guardias_disponibles=guardias_disponibles,
        guardia_filtro=guardia_filtro,
        page=page,
        total_pages=total_pages
    )

# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.es_admin:
        return redirect("/")

    db = get_db()

    abiertos = db.execute(
        "SELECT COUNT(*) FROM guardias WHERE estado = 'Abierto'"
    ).fetchone()[0]

    en_progreso = db.execute(
        "SELECT COUNT(*) FROM guardias WHERE estado = 'En progreso'"
    ).fetchone()[0]

    resueltos_hoy = db.execute("""
        SELECT COUNT(*) FROM guardias
        WHERE estado = 'Resuelto'
        AND date(fecha_resolucion) = date('now')
    """).fetchone()[0]

    top_guardias = db.execute("""
        SELECT quien_guardia, COUNT(*) total
        FROM guardias
        GROUP BY quien_guardia
        ORDER BY total DESC
        LIMIT 5
    """).fetchall()

    tiempo_promedio = db.execute("""
        SELECT AVG(
            (julianday(fecha_resolucion) - julianday(fecha_llamado)) * 24 * 60
        )
        FROM guardias
        WHERE fecha_resolucion IS NOT NULL
    """).fetchone()[0]

    return render_template(
        "dashboard.html",
        abiertos=abiertos,
        en_progreso=en_progreso,
        resueltos_hoy=resueltos_hoy,
        top_guardias=top_guardias,
        tiempo_promedio=round(tiempo_promedio, 2) if tiempo_promedio else None
    )

# --------------------------------------------------
# MAIN
# --------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
