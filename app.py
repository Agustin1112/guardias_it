from math import ceil
from flask import Flask, render_template, request, redirect, abort
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin, current_user
)
import os
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash, generate_password_hash

import psycopg2
import psycopg2.extras

# ================== CONFIG ==================
app = Flask(__name__)
app.secret_key = "super_secreto_guardias"

DATABASE_URL = os.environ.get("DATABASE_URL")
ITEMS_PER_PAGE = 10

# ================== LOGIN ==================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# ================== DB ==================
def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ================== USUARIOS ==================
class User(UserMixin):
    def __init__(self, id, username, password, es_admin):
        self.id = id
        self.username = username
        self.password = password
        self.es_admin = es_admin

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM usuarios WHERE id = %s",
        (user_id,)
    )
    user = cur.fetchone()
    db.close()

    if user:
        return User(
            user["id"],
            user["username"],
            user["password"],
            user["es_admin"]
        )
    return None

# ================== LOGIN ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM usuarios WHERE username = %s AND activo = 1",
            (request.form["username"],)
        )
        user = cur.fetchone()
        db.close()

        if user and check_password_hash(user["password"], request.form["password"]):
            login_user(
                User(
                    user["id"],
                    user["username"],
                    user["password"],
                    user["es_admin"]
                )
            )
            return redirect("/")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")

# ================== INDEX ==================
@app.route("/")
@login_required
def index():
    db = get_db()
    cur = db.cursor()

    guardia_filtro = request.args.get("guardia")
    estado_filtro = request.args.get("estado")
    resueltos_filtro = request.args.get("resueltos")
    page = int(request.args.get("page", 1))

    where = []
    params = []

    if not current_user.es_admin:
        where.append("quien_guardia = %s")
        params.append(current_user.username)

    if current_user.es_admin and guardia_filtro:
        where.append("quien_guardia = %s")
        params.append(guardia_filtro)

    if estado_filtro:
        where.append("estado = %s")
        params.append(estado_filtro)

    if resueltos_filtro == "hoy":
        where.append("DATE(fecha_resolucion) = CURRENT_DATE")
    elif resueltos_filtro == "semana":
        where.append("fecha_resolucion >= CURRENT_DATE - INTERVAL '7 days'")

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

    cur.execute(query, params)
    guardias_all = cur.fetchall()

    total = len(guardias_all)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    guardias_pag = guardias_all[start:end]

    now = datetime.now()
    for g in guardias_pag:
        fecha_registro = g["fecha_registro"]
        g["recent"] = fecha_registro and fecha_registro > now - timedelta(minutes=10)

    guardias_disponibles = []
    if current_user.es_admin:
        cur.execute("""
            SELECT DISTINCT quien_guardia
            FROM guardias
            ORDER BY quien_guardia
        """)
        guardias_disponibles = cur.fetchall()

    db.close()

    return render_template(
        "index.html",
        guardias=guardias_pag,
        guardias_disponibles=guardias_disponibles,
        guardia_filtro=guardia_filtro,
        page=page,
        total_pages=total_pages
    )

# ================== NUEVA GUARDIA ==================
@app.route("/nueva", methods=["GET", "POST"])
@login_required
def nueva_guardia():
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()

        fecha_llamado = datetime.strptime(
            request.form["fecha_llamado"],
            "%Y-%m-%dT%H:%M"
        )

        cur.execute("""
            INSERT INTO guardias (
                quien_llamo, fecha_llamado, quien_guardia,
                descripcion, prioridad, fecha_registro,
                derivado, derivado_a, estado
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            request.form["quien_llamo"],
            fecha_llamado,
            current_user.username,
            request.form["descripcion"],
            request.form["prioridad"],
            datetime.now(),
            bool(request.form.get("derivado")),
            request.form.get("derivado_a"),
            request.form["estado"]
        ))

        db.commit()
        db.close()
        return redirect("/")

    return render_template("nueva_guardia.html")

# ================== DASHBOARD ==================
@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.es_admin:
        return redirect("/")

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM guardias")
    total = cur.fetchone()["count"]

    cur.execute("SELECT COUNT(*) FROM guardias WHERE estado = 'Abierto'")
    abiertos = cur.fetchone()["count"]

    cur.execute("SELECT COUNT(*) FROM guardias WHERE estado = 'En progreso'")
    en_progreso = cur.fetchone()["count"]

    cur.execute("""
        SELECT COUNT(*) FROM guardias
        WHERE estado = 'Resuelto'
        AND DATE(fecha_resolucion) = CURRENT_DATE
    """)
    resueltos_hoy = cur.fetchone()["count"]

    cur.execute("""
        SELECT quien_guardia, COUNT(*) AS total
        FROM guardias
        GROUP BY quien_guardia
        ORDER BY total DESC
        LIMIT 5
    """)
    top_guardias = cur.fetchall()

    cur.execute("""
        SELECT AVG(
            EXTRACT(EPOCH FROM (fecha_resolucion - fecha_llamado)) / 3600
        )
        FROM guardias
        WHERE fecha_resolucion IS NOT NULL
    """)
    tiempo_promedio = cur.fetchone()["avg"]

    db.close()

    return render_template(
        "dashboard.html",
        total=total,
        abiertos=abiertos,
        en_progreso=en_progreso,
        resueltos_hoy=resueltos_hoy,
        top_guardias=top_guardias,
        tiempo_promedio=round(tiempo_promedio, 2) if tiempo_promedio else None
    )
