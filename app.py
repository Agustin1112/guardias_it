from math import ceil
import math
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
import csv
from io import StringIO
from flask import Response


# ================== ENV ==================
from dotenv import load_dotenv
load_dotenv()



ENV = os.environ.get("FLASK_ENV", "production")

if ENV == "testing":
    load_dotenv(".env.testing")
else:
    load_dotenv(".env")

print("🚀 Entorno:", ENV)
print("📦 DATABASE_URL:", os.environ.get("DATABASE_URL"))
# ================== CONFIG ==================
app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "super_secreto_guardias")

DATABASE_URL = os.environ.get("DATABASE_URL")
FLASK_ENV = os.environ.get("FLASK_ENV", "production")

ITEMS_PER_PAGE = 10

app.config["ENV"] = FLASK_ENV
app.config["DEBUG"] = FLASK_ENV == "testing"

# ================== LOGIN ==================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# ================== DB ==================
def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada")

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
from flask import render_template, request, redirect, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash

from flask import render_template, request, redirect, flash
from flask_login import login_user

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_db()
        cur = db.cursor()

        cur.execute("""
            SELECT id, username, password_hash, es_admin
            FROM usuarios
            WHERE username = %s
            AND activo = true
        """, (username,))

        user = cur.fetchone()
        cur.close()
        db.close()

        if user and check_password_hash(user["password_hash"], password):
            login_user(
                User(
                    user["id"],
                    user["username"],
                    user["password_hash"],
                    user["es_admin"]
                )
            )
            return redirect("/")

        # ❌ Credenciales inválidas
        flash("Usuario o contraseña incorrectos", "danger")

    return render_template("login.html")





@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")


# ================== INDEX ==================
import re
from datetime import datetime, timedelta
from flask import request, render_template
from flask_login import login_required, current_user

@app.route("/")
@login_required
def index():
    db = get_db()
    cur = db.cursor()

    guardia_filtro = request.args.get("guardia")
    estado_filtro = request.args.get("estado")
    resueltos_filtro = request.args.get("resueltos")
    from_dashboard = request.args.get("from_dashboard")
    q = request.args.get("q")
    page = int(request.args.get("page", 1))

    where = []
    params = []

    # =========================
    # PERMISOS
    # =========================
    if not current_user.es_admin:
        where.append("quien_guardia = %s")
        params.append(current_user.username)

    if current_user.es_admin and guardia_filtro:
        where.append("quien_guardia = %s")
        params.append(guardia_filtro)

    # =========================
    # FILTRO POR ESTADO
    # =========================
    if estado_filtro == "Resuelto":
        where.append("estado = 'Resuelto'")
        where.append("fecha_resolucion IS NOT NULL")
    elif estado_filtro:
        where.append("estado = %s")
        params.append(estado_filtro)

    # =========================
    # FILTRO DESDE DASHBOARD
    # =========================
    if resueltos_filtro == "hoy":
        where.append("estado = 'Resuelto'")
        where.append("fecha_resolucion IS NOT NULL")
        where.append("DATE(fecha_resolucion) = CURRENT_DATE")

    elif resueltos_filtro == "semana":
        where.append("estado = 'Resuelto'")
        where.append("fecha_resolucion IS NOT NULL")
        where.append("fecha_resolucion >= date_trunc('week', CURRENT_DATE)")

    # =========================
    # 🔍 BÚSQUEDA FLEXIBLE
    # =========================
    if q:
        q_norm = re.sub(r"[\s\-]+", "", q.lower())
        like = f"%{q_norm}%"

        where.append("""
            (
                LOWER(REPLACE(REPLACE(descripcion, ' ', ''), '-', '')) LIKE %s
                OR LOWER(REPLACE(REPLACE(quien_llamo, ' ', ''), '-', '')) LIKE %s
                OR LOWER(REPLACE(REPLACE(COALESCE(derivado_a,''), ' ', ''), '-', '')) LIKE %s
            )
        """)
        params.extend([like, like, like])

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

    # =========================
    # PAGINACIÓN
    # =========================
    ITEMS_PER_PAGE = 10
    total = len(guardias_all)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    guardias_pag = guardias_all[start:end]

    # =========================
    # RECIENTES + RESALTADO
    # =========================
    now = datetime.now()

    if q:
        pattern = re.compile(
            r"(" + r"[\s\-]*".join(map(re.escape, q_norm)) + r")",
            re.IGNORECASE
        )

        def highlight(text):
            if not text:
                return text
            return pattern.sub(r"<mark>\1</mark>", text)

        for g in guardias_pag:
            g["recent"] = g["fecha_registro"] and g["fecha_registro"] > now - timedelta(minutes=10)
            g["descripcion_html"] = highlight(g["descripcion"])
            g["quien_llamo_html"] = highlight(g["quien_llamo"])
            g["derivado_a_html"] = highlight(g["derivado_a"]) if g["derivado_a"] else None
    else:
        for g in guardias_pag:
            g["recent"] = g["fecha_registro"] and g["fecha_registro"] > now - timedelta(minutes=10)
            g["descripcion_html"] = g["descripcion"]
            g["quien_llamo_html"] = g["quien_llamo"]
            g["derivado_a_html"] = g["derivado_a"]

    # =========================
    # GUARDIAS DISPONIBLES
    # =========================
    guardias_disponibles = []
    if current_user.es_admin:
        cur.execute("""
            SELECT DISTINCT quien_guardia
            FROM guardias
            ORDER BY quien_guardia
        """)
        guardias_disponibles = cur.fetchall()

    cur.close()

    return render_template(
        "index.html",
        guardias=guardias_pag,
        guardias_disponibles=guardias_disponibles,
        guardia_filtro=guardia_filtro,
        estado_filtro=estado_filtro,
        resueltos_filtro=resueltos_filtro,
        from_dashboard=from_dashboard,
        q=q,
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
        SELECT id, username, es_admin, activo
        FROM usuarios
        ORDER BY username
    """)

    usuarios = cur.fetchall()

    cur.close()
    db.close()

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

        estado = request.form["estado"]

        # 🔥 CLAVE: setear fecha_resolucion si es Resuelto
        fecha_resolucion = None
        if estado == "Resuelto":
            fecha_resolucion = datetime.now()

        cur.execute("""
            INSERT INTO guardias (
                quien_llamo,
                fecha_llamado,
                quien_guardia,
                descripcion,
                prioridad,
                fecha_registro,
                fecha_resolucion,
                derivado,
                derivado_a,
                estado
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            request.form["quien_llamo"],
            fecha_llamado,
            current_user.username,
            request.form["descripcion"],
            request.form["prioridad"],
            datetime.now(),
            fecha_resolucion,          # 👈 ACA
            bool(request.form.get("derivado")),
            request.form.get("derivado_a"),
            estado
        ))

        db.commit()
        db.close()
        return redirect("/")

    return render_template("nueva_guardia.html")


@app.route("/guardias/editar/<int:guardia_id>", methods=["GET", "POST"])
@login_required
def editar_guardia(guardia_id):
    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        estado = request.form.get("estado")
        descripcion = request.form.get("descripcion")
        resolucion = request.form.get("resolucion")
        derivado = "derivado" in request.form
        derivado_a = request.form.get("derivado_a")

        cur.execute("""
            UPDATE guardias
            SET estado = %s,
                descripcion = %s,
                resolucion = %s,
                derivado = %s,
                derivado_a = %s
            WHERE id = %s
        """, (
            estado,
            descripcion,
            resolucion,
            derivado,
            derivado_a,
            guardia_id
        ))

        db.commit()
        cur.close()
        flash("Llamado actualizado correctamente", "success")
        return redirect(url_for("index"))

    cur.execute("SELECT * FROM guardias WHERE id = %s", (guardia_id,))
    guardia = cur.fetchone()
    cur.close()

    return render_template("editar_guardia.html", guardia=guardia)


@app.route("/historial_guardias")
@login_required
def historial_guardias():
    import math

    page = request.args.get("page", 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    guardia_filtro = request.args.get("guardia")

    db = get_db()
    cur = db.cursor()

    # ===============================
    # GUARDIAS DISPONIBLES (ADMIN)
    # ===============================
    guardias_disponibles = []
    if current_user.es_admin:
        cur.execute("""
            SELECT DISTINCT quien_guardia
            FROM guardias
            ORDER BY quien_guardia
        """)
        guardias_disponibles = cur.fetchall()

    # ===============================
    # ADMIN (paginación SIEMPRE)
    # ===============================
    if current_user.es_admin:
        filtros = []
        params = []

        if guardia_filtro:
            filtros.append("quien_guardia = %s")
            params.append(guardia_filtro)

        where = f"WHERE {' AND '.join(filtros)}" if filtros else ""

        # TOTAL
        cur.execute(f"SELECT COUNT(*) FROM guardias {where}", params)
        total = cur.fetchone()[0]

        # DATOS
        cur.execute(f"""
            SELECT *
            FROM guardias
            {where}
            ORDER BY fecha_registro DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])

    # ===============================
    # GUARDIA NORMAL (paginación desde 11)
    # ===============================
    else:
        cur.execute("""
            SELECT COUNT(*)
            FROM guardias
            WHERE quien_guardia = %s
        """, (current_user.username,))
        total = cur.fetchone()[0]

        if total <= 10:
            cur.execute("""
                SELECT *
                FROM guardias
                WHERE quien_guardia = %s
                ORDER BY fecha_registro DESC
            """, (current_user.username,))
        else:
            cur.execute("""
                SELECT *
                FROM guardias
                WHERE quien_guardia = %s
                ORDER BY fecha_registro DESC
                LIMIT %s OFFSET %s
            """, (current_user.username, per_page, offset))

    guardias = cur.fetchall()
    cur.close()

    # ===============================
    # TOTAL PAGES (CLAVE DEL ARREGLO)
    # ===============================
    if current_user.es_admin:
        total_pages = math.ceil(total / per_page)
    else:
        total_pages = math.ceil(total / per_page) if total > 10 else 1

    return render_template(
        "historial_guardias.html",
        guardias=guardias,
        guardias_disponibles=guardias_disponibles,
        guardia_filtro=guardia_filtro,
        page=page,
        total_pages=total_pages,
        total=total
    )








# ================== DASHBOARD ==================
# ================== DASHBOARD ==================
@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.es_admin:
        return redirect("/")

    db = get_db()
    cur = db.cursor()

    guardia_filtro = request.args.get("guardia")

    # ======================
    # BASE DE FILTROS
    # ======================
    where = []
    params = []

    if guardia_filtro:
        where.append("quien_guardia = %s")
        params.append(guardia_filtro)

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    # ======================
    # TOTAL GENERAL
    # ======================
    cur.execute(f"""
        SELECT COUNT(*) FROM guardias
        {where_sql}
    """, params)
    total = cur.fetchone()["count"]

    # ======================
    # ABIERTOS
    # ======================
    cur.execute(f"""
        SELECT COUNT(*) FROM guardias
        {where_sql} {"AND" if where_sql else "WHERE"} estado = 'Abierto'
    """, params)
    abiertos = cur.fetchone()["count"]

    # ======================
    # EN PROGRESO
    # ======================
    cur.execute(f"""
        SELECT COUNT(*) FROM guardias
        {where_sql} {"AND" if where_sql else "WHERE"} estado = 'En progreso'
    """, params)
    en_progreso = cur.fetchone()["count"]

    # ======================
    # RESUELTOS
    # ======================
    cur.execute(f"""
        SELECT COUNT(*) FROM guardias
        {where_sql} {"AND" if where_sql else "WHERE"}
        estado = 'Resuelto'
        AND fecha_resolucion IS NOT NULL
    """, params)
    total_resueltos = cur.fetchone()["count"]

    # ======================
    # TOP GUARDIAS (solo sin filtro)
    # ======================
    top_guardias = []
    if not guardia_filtro:
        cur.execute("""
            SELECT quien_guardia, COUNT(*) AS total
            FROM guardias
            WHERE estado = 'Resuelto'
            GROUP BY quien_guardia
            ORDER BY total DESC
            LIMIT 5
        """)
        top_guardias = cur.fetchall()

    # ======================
    # TIEMPO PROMEDIO GLOBAL
    # ======================
    cur.execute(f"""
        SELECT AVG(
            EXTRACT(EPOCH FROM (fecha_resolucion - fecha_llamado)) / 60
        ) AS promedio
        FROM guardias
        {where_sql} {"AND" if where_sql else "WHERE"}
        estado = 'Resuelto'
        AND fecha_resolucion IS NOT NULL
    """, params)
    tiempo_promedio = cur.fetchone()["promedio"]

    # ======================
    # GUARDIAS DISPONIBLES
    # ======================
    cur.execute("""
        SELECT DISTINCT quien_guardia
        FROM guardias
        ORDER BY quien_guardia
    """)
    guardias_disponibles = cur.fetchall()

    cur.close()

    return render_template(
        "dashboard.html",
        total=total,
        abiertos=abiertos,
        en_progreso=en_progreso,
        total_resueltos=total_resueltos,
        top_guardias=top_guardias,
        tiempo_promedio=round(tiempo_promedio, 1) if tiempo_promedio else "—",
        guardias_disponibles=guardias_disponibles,
        guardia_filtro=guardia_filtro
    )





@app.route("/resolver_guardia/<int:id>", methods=["POST"])
@login_required
def resolver_guardia(id):
    db = get_db()
    cur = db.cursor()

    # Solo admin o el guardia asignado pueden resolver
    cur.execute("""
        SELECT quien_guardia
        FROM guardias
        WHERE id = %s
    """, (id,))
    guardia = cur.fetchone()

    if not guardia:
        cur.close()
        return redirect("/historial_guardias")

    if not current_user.es_admin and guardia["quien_guardia"] != current_user.username:
        cur.close()
        return redirect("/historial_guardias")

    # Marcar como resuelto + fecha
    cur.execute("""
        UPDATE guardias
        SET estado = 'Resuelto',
            fecha_resolucion = NOW()
        WHERE id = %s
    """, (id,))

    db.commit()
    cur.close()

    return redirect("/historial_guardias")

import csv
import io
from flask import Response

@app.route("/reporte/guardias")
@login_required
def reporte_guardias():

    db = get_db()
    cur = db.cursor()

    guardia = request.args.get("guardia")

    where = ""
    params = []

    if guardia:
        where = "WHERE quien_guardia = %s"
        params.append(guardia)

    cur.execute(f"""
        SELECT
            fecha_llamado,
            fecha_registro,
            quien_llamo,
            quien_guardia,
            prioridad,
            descripcion,
            estado,
            fecha_resolucion
        FROM guardias
        {where}
        ORDER BY fecha_llamado DESC
    """, params)

    rows = cur.fetchall()

    output = io.StringIO()
    output.write("\ufeff")  # 🔥 CLAVE PARA EXCEL

    writer = csv.writer(output)
    writer.writerow([
        "Fecha llamado",
        "Fecha carga",
        "Quién llamó",
        "Guardia",
        "Prioridad",
        "Descripción",
        "Estado",
        "Fecha resolución"
    ])

    for r in rows:
        writer.writerow([
            r["fecha_llamado"],
            r["fecha_registro"],
            r["quien_llamo"],
            r["quien_guardia"],
            r["prioridad"],
            r["descripcion"],
            r["estado"],
            r["fecha_resolucion"]
        ])

    cur.close()

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=guardias.csv"
        }
    )




if __name__ == "__main__":
    app.run(debug=True)
