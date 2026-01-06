from math import ceil
from flask import Flask, render_template, request, redirect, g
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin, current_user
)
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
ITEMS_PER_PAGE = 10

app = Flask(__name__)
app.secret_key = "super_secreto_guardias"

login_manager = LoginManager(app)
login_manager.login_view = "login"

DATABASE_URL = os.environ.get("DATABASE_URL")

# --------------------------------------------------
# DB
# --------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db:
        db.close()

# --------------------------------------------------
# INIT DB
# --------------------------------------------------
def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            es_admin BOOLEAN DEFAULT FALSE,
            activo BOOLEAN DEFAULT TRUE,
            debe_cambiar_password BOOLEAN DEFAULT TRUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS guardias (
            id SERIAL PRIMARY KEY,
            quien_llamo TEXT,
            fecha_llamado TIMESTAMP,
            quien_guardia TEXT,
            descripcion TEXT,
            prioridad TEXT,
            fecha_registro TIMESTAMP,
            estado TEXT,
            resolucion TEXT,
            fecha_resolucion TIMESTAMP,
            derivado BOOLEAN DEFAULT FALSE,
            derivado_a TEXT
        )
    """)

    cur.execute(
        "SELECT id FROM usuarios WHERE username = 'admin'"
    )
    admin = cur.fetchone()

    if not admin:
        cur.execute("""
            INSERT INTO usuarios (username, password, es_admin)
            VALUES (%s, %s, TRUE)
        """, ("admin", generate_password_hash("admin123")))

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
    cur = db.cursor()
    cur.execute(
        "SELECT * FROM usuarios WHERE id = %s", (user_id,)
    )
    user = cur.fetchone()
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
        cur = db.cursor()
        cur.execute("""
            SELECT * FROM usuarios
            WHERE username = %s AND activo = TRUE
        """, (request.form["username"],))
        user = cur.fetchone()

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
# CAMBIAR PASSWORD
# --------------------------------------------------
@app.route("/cambiar-password", methods=["GET", "POST"])
@login_required
def cambiar_password():
    if request.method == "POST":
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            UPDATE usuarios
            SET password = %s, debe_cambiar_password = FALSE
            WHERE id = %s
        """, (
            generate_password_hash(request.form["password"]),
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
    cur = db.cursor()

    where = []
    params = []

    if not current_user.es_admin:
        where.append("quien_guardia = %s")
        params.append(current_user.username)

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    cur.execute(f"""
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
    """, params)

    guardias = cur.fetchall()

    return render_template("index.html", guardias=guardias)

# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.es_admin:
        return redirect("/")

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM guardias WHERE estado = 'Abierto'")
    abiertos = cur.fetchone()["count"]

    cur.execute("SELECT COUNT(*) FROM guardias WHERE estado = 'En progreso'")
    en_progreso = cur.fetchone()["count"]

    cur.execute("""
        SELECT COUNT(*) FROM guardias
        WHERE estado = 'Resuelto'
        AND fecha_resolucion::date = CURRENT_DATE
    """)
    resueltos_hoy = cur.fetchone()["count"]

    cur.execute("""
        SELECT quien_guardia, COUNT(*) total
        FROM guardias
        GROUP BY quien_guardia
        ORDER BY total DESC
        LIMIT 5
    """)
    top_guardias = cur.fetchall()

    cur.execute("""
        SELECT AVG(EXTRACT(EPOCH FROM (fecha_resolucion - fecha_llamado)) / 60)
        FROM guardias
        WHERE fecha_resolucion IS NOT NULL
    """)
    tiempo = cur.fetchone()["avg"]

    return render_template(
        "dashboard.html",
        abiertos=abiertos,
        en_progreso=en_progreso,
        resueltos_hoy=resueltos_hoy,
        top_guardias=top_guardias,
        tiempo_promedio=round(tiempo, 2) if tiempo else None
    )

# --------------------------------------------------
# MAIN
# --------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

