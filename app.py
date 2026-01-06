from math import ceil
from flask import Flask, flash, jsonify, render_template, request, redirect, abort, url_for
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

# ---------- PANEL DE USUARIOS (SOLO ADMIN) ----------
@app.route("/usuarios")
@login_required
def panel_usuarios():
    if not current_user.es_admin:
        abort(403)

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT id, username, es_admin
        FROM usuarios
        ORDER BY username
    """)

    usuarios = cur.fetchall()

    return render_template("usuarios.html", usuarios=usuarios)


@app.route("/usuarios/nuevo", methods=["GET", "POST"])
@login_required
def nuevo_usuario():
    # Solo admins pueden crear usuarios
    if not current_user.es_admin:
        abort(403)

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        es_admin = "es_admin" in request.form

        if not username or not password:
            flash("Usuario y contraseña son obligatorios", "danger")
            return render_template("nuevo_usuario.html")

        password_hash = generate_password_hash(password)

        db = get_db()
        cur = db.cursor()

        try:
            cur.execute("""
                INSERT INTO usuarios (
                    username,
                    password,
                    password_hash,
                    es_admin,
                    activo,
                    debe_cambiar_password
                )
                VALUES (%s, %s, %s, %s, true, true)
            """, (
                username,
                "TEMP",  # placeholder, no se usa
                password_hash,
                es_admin
            ))

            db.commit()
            flash("Usuario creado correctamente", "success")
            return redirect(url_for("panel_usuarios"))

        except Exception as e:
            db.rollback()
            flash("Error al crear el usuario (¿usuario duplicado?)", "danger")

        finally:
            cur.close()

    return render_template("nuevo_usuario.html")




# ---------- EDITAR USUARIO ----------
@app.route("/usuarios/editar/<int:user_id>", methods=["GET", "POST"])
@login_required
def editar_usuario(user_id):
    if not current_user.es_admin:
        abort(403)

    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        es_admin = "es_admin" in request.form
        activo = "activo" in request.form

        cur.execute("""
            UPDATE usuarios
            SET es_admin = %s,
                activo = %s
            WHERE id = %s
        """, (es_admin, activo, user_id))

        db.commit()
        cur.close()
        flash("Usuario actualizado", "success")
        return redirect(url_for("panel_usuarios"))

    cur.execute("SELECT id, username, es_admin, activo FROM usuarios WHERE id = %s", (user_id,))
    usuario = cur.fetchone()
    cur.close()

    return render_template("editar_usuario.html", usuario=usuario)


@app.route("/usuarios/toggle/<int:user_id>", methods=["POST"])
@login_required
def toggle_usuario(user_id):
    if not current_user.es_admin:
        return jsonify({"error": "No autorizado"}), 403

    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE usuarios SET activo = NOT activo WHERE id = %s", (user_id,))
        db.commit()
        cur.close()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400


@app.route("/usuarios/<int:user_id>/toggle-admin", methods=["POST"])
@login_required
def toggle_admin(user_id):
    if not current_user.es_admin:
        return jsonify({"error": "No autorizado"}), 403

    # Evitar que el admin se quite a sí mismo
    if current_user.id == user_id:
        return jsonify({"error": "No podés cambiar tu propio rol"}), 400

    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("UPDATE usuarios SET es_admin = NOT es_admin WHERE id = %s", (user_id,))
        db.commit()
        cur.close()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400



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



@app.route("/usuarios/reset_password/<int:user_id>", methods=["POST"])
@login_required
def reset_password(user_id):
    if not current_user.es_admin:
        abort(403)

    nueva_password = "1234"  # o generada
    password_hash = generate_password_hash(nueva_password)

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE usuarios
        SET password_hash = %s
        WHERE id = %s
    """, (password_hash, user_id))

    db.commit()
    flash("Contraseña reseteada a 1234", "warning")
    return redirect(url_for("panel_usuarios"))



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

if __name__ == "__main__":
    app.run(debug=True)
