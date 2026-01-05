from math import ceil
from flask import Flask, render_template, request, redirect, abort
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin, current_user
    
)
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash, generate_password_hash
ITEMS_PER_PAGE = 10
app = Flask(__name__)
app.secret_key = "super_secreto_guardias"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# ---------- DB ----------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn



# ---------- USUARIOS ----------
class User(UserMixin):
    def __init__(self, id, username, password, es_admin):
        self.id = id
        self.username = username
        self.password = password
        self.es_admin = es_admin


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    user = db.execute(
        "SELECT * FROM usuarios WHERE id = ?", (user_id,)
    ).fetchone()

    if user:
        return User(
            user["id"],
            user["username"],
            user["password"],
            user["es_admin"]
        )
    return None


# ---------- LOGIN ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        user = db.execute(
    "SELECT * FROM usuarios WHERE username = ? AND activo = 1",
    (request.form["username"],)
).fetchone()

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


# ---------- GUARDIAS ----------
from flask import request, render_template
from datetime import datetime, timedelta

@app.route("/")
@login_required
def index():
    db = get_db()

    # --- filtros ---
    guardia_filtro = request.args.get("guardia")
    estado_filtro = request.args.get("estado")
    resueltos_filtro = request.args.get("resueltos")

    page = int(request.args.get("page", 1))
    per_page = 10

    where = []
    params = []

    # --- visibilidad según rol ---
    if not current_user.es_admin:
        where.append("quien_guardia = ?")
        params.append(current_user.username)

    # --- filtro guardia (solo admin) ---
    if current_user.es_admin and guardia_filtro:
        where.append("quien_guardia = ?")
        params.append(guardia_filtro)

    # --- filtro estado ---
    if estado_filtro:
        where.append("estado = ?")
        params.append(estado_filtro)

    # --- filtro resueltos ---
    if resueltos_filtro == "hoy":
        where.append("DATE(fecha_resolucion) = DATE('now')")
    elif resueltos_filtro == "semana":
        where.append("fecha_resolucion >= DATE('now','-7 day')")

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    # --- traer TODOS los guardias filtrados ---
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

    # --- paginación ---
    total = len(guardias_all)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    guardias_pag = guardias_all[start:end]

    # --- marcar recientes ---
    now = datetime.now()
    guardias_pag_dict = []
    for g in guardias_pag:
        g_dict = dict(g)
        fecha_registro = datetime.strptime(
            g_dict["fecha_registro"],
            "%Y-%m-%d %H:%M:%S.%f"
        )
        g_dict["recent"] = fecha_registro > now - timedelta(minutes=10)
        guardias_pag_dict.append(g_dict)

    # --- lista de guardias para filtro (solo admin) ---
    if current_user.es_admin:
        guardias_disponibles = db.execute("""
            SELECT DISTINCT quien_guardia
            FROM guardias
            ORDER BY quien_guardia
        """).fetchall()
    else:
        guardias_disponibles = []

    return render_template(
        "index.html",
        guardias=guardias_pag_dict,
        guardias_disponibles=guardias_disponibles,
        guardia_filtro=guardia_filtro,
        page=page,
        total_pages=total_pages
    )





@app.route("/nueva", methods=["GET", "POST"])
@login_required
def nueva_guardia():
    if request.method == "POST":
        db = get_db()

        fecha_llamado = datetime.strptime(
            request.form["fecha_llamado"],
            "%Y-%m-%dT%H:%M"
        )

        fecha_registro = datetime.now()

        db.execute("""
            INSERT INTO guardias (
                quien_llamo,
                fecha_llamado,
                quien_guardia,
                descripcion,
                prioridad,
                fecha_registro,
                derivado,
                derivado_a,
                estado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            request.form["quien_llamo"],
            fecha_llamado,
            current_user.username,
            request.form["descripcion"],
            request.form["prioridad"],
            fecha_registro,
            1 if request.form.get("derivado") else 0,
            request.form.get("derivado_a"),
            request.form["estado"]   # 👈 ACÁ ESTÁ LA CLAVE
        ))

        db.commit()
        return redirect("/")

    return render_template("nueva_guardia.html")




from datetime import datetime

@app.route("/editar/<int:id>", methods=["GET", "POST"])
@login_required
def editar_guardia(id):
    db = get_db()

    guardia = db.execute(
        "SELECT * FROM guardias WHERE id = ?", (id,)
    ).fetchone()

    if not current_user.es_admin and guardia["quien_guardia"] != current_user.username:
        return redirect("/")

    if request.method == "POST":
        estado = request.form["estado"]
        resolucion = request.form.get("resolucion") or None

        fecha_resolucion = None
        if estado == "Resuelto":
            fecha_resolucion = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db.execute("""
            UPDATE guardias
            SET estado = ?,
                resolucion = ?,
                fecha_resolucion = ?,
                derivado = ?,
                derivado_a = ?
            WHERE id = ?
        """, (
            estado,
            resolucion,
            fecha_resolucion,
            1 if request.form.get("derivado") else 0,
            request.form.get("derivado_a"),
            id
        ))

        db.commit()
        return redirect("/")

    return render_template("editar_guardia.html", guardia=guardia)




from werkzeug.security import generate_password_hash

@app.route("/historial")
@login_required
def historial():
    db = get_db()

    if current_user.es_admin:
        guardias = db.execute("""
            SELECT *
            FROM guardias
            ORDER BY
                CASE prioridad
                    WHEN 'Alta' THEN 1
                    WHEN 'Media' THEN 2
                    WHEN 'Baja' THEN 3
                    ELSE 4
                END,
                datetime(fecha_llamado) DESC
        """).fetchall()
    else:
        guardias = db.execute("""
            SELECT *
            FROM guardias
            WHERE quien_guardia = ?
            ORDER BY
                CASE prioridad
                    WHEN 'Alta' THEN 1
                    WHEN 'Media' THEN 2
                    WHEN 'Baja' THEN 3
                    ELSE 4
                END,
                datetime(fecha_llamado) DESC
        """, (current_user.username,)).fetchall()

    return render_template("historial_guardias.html", guardias=guardias)




# ---------- PANEL DE USUARIOS (SOLO ADMIN) ----------
@app.route("/usuarios")
@login_required
def panel_usuarios():
    if not current_user.es_admin:
        return redirect("/")

    db = get_db()
    usuarios = db.execute("SELECT * FROM usuarios ORDER BY username").fetchall()
    mensaje = None  # para mostrar alertas si querés después
    return render_template("admin_usuarios.html", usuarios=usuarios, mensaje=mensaje)


@app.route("/usuarios/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_usuario():
    if not current_user.es_admin:
        return redirect("/")

    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])
        es_admin = 1 if request.form.get("es_admin") else 0

        db = get_db()
        db.execute(
            "INSERT INTO usuarios (username, password, es_admin) VALUES (?, ?, ?)",
            (username, password, es_admin)
        )
        db.commit()
        return redirect("/usuarios")

    return render_template("nuevo_usuario.html")

# ---------- EDITAR USUARIO ----------
@app.route("/usuarios/editar/<username>", methods=["GET", "POST"])
@login_required
def editar_usuario(username):
    if not current_user.es_admin:
        abort(403)

    db = get_db()
    usuario = db.execute(
        "SELECT * FROM usuarios WHERE username = ?",
        (username,)
    ).fetchone()

    if not usuario:
        abort(404)

    if request.method == "POST":
        es_admin = 1 if request.form.get("es_admin") else 0
        nueva_password = request.form.get("password")

        # Si cambia password
        if nueva_password:
            db.execute("""
                UPDATE usuarios
                SET password = ?, es_admin = ?
                WHERE username = ?
            """, (
                generate_password_hash(nueva_password),
                es_admin,
                username
            ))
        else:
            db.execute("""
                UPDATE usuarios
                SET es_admin = ?
                WHERE username = ?
            """, (
                es_admin,
                username
            ))

        db.commit()
        return redirect("/usuarios")

    return render_template("editar_usuario.html", usuario=usuario)


# ---------- ELIMINAR USUARIO ----------
@app.route("/usuarios/eliminar/<username>", methods=["POST"])
@login_required
def eliminar_usuario(username):
    if not current_user.es_admin:
        abort(403)

    if username == current_user.username:
        return redirect("/usuarios")

    db = get_db()

    # No borrar último admin
    admins = db.execute(
        "SELECT COUNT(*) FROM usuarios WHERE es_admin = 1"
    ).fetchone()[0]

    usuario = db.execute(
        "SELECT * FROM usuarios WHERE username = ?",
        (username,)
    ).fetchone()

    if usuario["es_admin"] and admins <= 1:
        return redirect("/usuarios")

    db.execute(
        "DELETE FROM usuarios WHERE username = ?",
        (username,)
    )
    db.commit()

    return redirect("/usuarios")

@app.route("/usuarios/desactivar/<username>", methods=["POST"])
@login_required
def desactivar_usuario(username):
    if not current_user.es_admin:
        abort(403)

    if username == current_user.username:
        return redirect("/usuarios")

    db = get_db()

    # evitar desactivar último admin
    admins = db.execute(
        "SELECT COUNT(*) FROM usuarios WHERE es_admin = 1 AND activo = 1"
    ).fetchone()[0]

    usuario = db.execute(
        "SELECT * FROM usuarios WHERE username = ?",
        (username,)
    ).fetchone()

    if usuario["es_admin"] and admins <= 1:
        return redirect("/usuarios")

    db.execute(
        "UPDATE usuarios SET activo = 0 WHERE username = ?",
        (username,)
    )
    db.commit()
    return redirect("/usuarios")

@app.route("/usuarios/activar/<username>", methods=["POST"])
@login_required
def activar_usuario(username):
    if not current_user.es_admin:
        abort(403)

    db = get_db()
    db.execute(
        "UPDATE usuarios SET activo = 1 WHERE username = ?",
        (username,)
    )
    db.commit()
    return redirect("/usuarios")



# ---------- PANEL ADMIN ----------
@app.route("/admin/usuarios", methods=["GET", "POST"])
@login_required
def admin_usuarios():

    if not current_user.es_admin:
        abort(403)

    db = get_db()
    mensaje = None

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        es_admin = 1 if request.form.get("es_admin") else 0

        existe = db.execute(
            "SELECT id FROM usuarios WHERE username = ?",
            (username,)
        ).fetchone()

        if existe:
            mensaje = "❌ El usuario ya existe"
        else:
            db.execute("""
                INSERT INTO usuarios (username, password, es_admin)
                VALUES (?, ?, ?)
            """, (
                username,
                generate_password_hash(password),
                es_admin
            ))
            db.commit()
            mensaje = "✅ Usuario creado correctamente"

    usuarios = db.execute(
    "SELECT id, username, es_admin, activo FROM usuarios ORDER BY username"
).fetchall()


    return render_template(
        "admin_usuarios.html",
        usuarios=usuarios,
        mensaje=mensaje
    )

@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.es_admin:
        return redirect("/")

    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM guardias").fetchone()[0]

    abiertos = db.execute("""
        SELECT COUNT(*) FROM guardias WHERE estado = 'Abierto'
    """).fetchone()[0]

    en_progreso = db.execute("""
        SELECT COUNT(*) FROM guardias WHERE estado = 'En progreso'
    """).fetchone()[0]

    resueltos_hoy = db.execute("""
        SELECT COUNT(*) FROM guardias
        WHERE estado = 'Resuelto'
        AND date(fecha_resolucion) = date('now')
    """).fetchone()[0]

    top_guardias = db.execute("""
        SELECT quien_guardia, COUNT(*) as total
        FROM guardias
        GROUP BY quien_guardia
        ORDER BY total DESC
        LIMIT 5
    """).fetchall()

    tiempo_promedio = db.execute("""
        SELECT AVG(
            (julianday(fecha_resolucion) - julianday(fecha_llamado)) * 24
        )
        FROM guardias
        WHERE fecha_resolucion IS NOT NULL
    """).fetchone()[0]

    return render_template(
        "dashboard.html",
        total=total,
        abiertos=abiertos,
        en_progreso=en_progreso,
        resueltos_hoy=resueltos_hoy,
        top_guardias=top_guardias,
        tiempo_promedio=round(tiempo_promedio, 2) if tiempo_promedio else None
    )


