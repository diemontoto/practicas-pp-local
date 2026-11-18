"""Autenticación, sesiones, CSRF, permisos por rol y alcance de datos (tutores)."""
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import audit, ex, now, q, q1, commit

bp = Blueprint("auth", __name__)

# «consulta.inicio»: consulta pública de sólo lectura de Seguimiento y asistencia (ver views/consulta.py)
PUBLIC_ENDPOINTS = {"auth.login", "static", "main.salud", "consulta.inicio"}
IDLE_SECONDS = 2 * 3600
MAX_INTENTOS = 5
BLOQUEO_MINUTOS = 15


# ----------------------------------------------------------------- hashing
def hash_password(pw):
    try:
        return generate_password_hash(pw, method="scrypt")
    except Exception:  # OpenSSL sin scrypt
        return generate_password_hash(pw, method="pbkdf2:sha256:600000")


def validate_password(pw, username=""):
    """Devuelve un mensaje de error o None."""
    if len(pw) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "La contraseña debe combinar letras y números."
    if username and pw.lower() == username.lower():
        return "La contraseña no puede ser igual al usuario."
    return None


# -------------------------------------------------------------------- CSRF
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(24)
    return session["_csrf"]


def safe_next(target, default=None):
    """Sólo permite redirecciones internas."""
    if target:
        p = urlparse(target)
        if not p.scheme and not p.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return default


# ------------------------------------------------------------------ usuario
def load_user():
    g.user = None
    g.perms = set()
    uid = session.get("uid")
    if not uid:
        return
    last = session.get("last", 0)
    if time.time() - last > IDLE_SECONDS:
        session.clear()
        flash("Su sesión expiró por inactividad. Ingrese nuevamente.", "warning")
        return
    row = q1(
        "SELECT u.*, r.codigo AS rol, r.nombre AS rol_nombre FROM usuarios u JOIN roles r ON r.id = u.rol_id "
        "WHERE u.id = ? AND u.eliminado_en IS NULL AND u.activo = 1", (uid,))
    if not row:
        session.clear()
        return
    g.user = row
    g.perms = {r["codigo"] for r in q(
        "SELECT p.codigo FROM rol_permisos rp JOIN permisos p ON p.id = rp.permiso_id WHERE rp.rol_id = ?",
        (row["rol_id"],))}
    session["last"] = time.time()


def can(code):
    user = getattr(g, "user", None)
    if not user:
        return False
    return user["rol"] == "admin" or code in g.perms


def perm_required(*codes):
    """Exige al menos uno de los permisos indicados (se controla en el backend)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not any(can(c) for c in codes):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


# ------------------------------------------------- alcance de datos (tutor)
def tutor_docente():
    """id de docente si el usuario es tutor; None si ve todo."""
    u = g.user
    if u and u["rol"] == "tutor":
        return u["docente_id"] or -1
    return None


def scope_alumnos(alias="a"):
    d = tutor_docente()
    if d is None:
        return "1=1", []
    return (f"({alias}.id IN (SELECT alumno_id FROM asignaciones_tutor WHERE docente_id = ?) "
            f"OR {alias}.id IN (SELECT ap2.alumno_id FROM alumnos_practicas ap2 JOIN practicas p2 ON p2.id = ap2.practica_id "
            f"WHERE p2.tutor_id = ? AND ap2.eliminado_en IS NULL AND p2.eliminado_en IS NULL))", [d, d])


def scope_ap(ap="ap", p="p"):
    d = tutor_docente()
    if d is None:
        return "1=1", []
    return (f"({p}.tutor_id = ? OR EXISTS (SELECT 1 FROM asignaciones_tutor at WHERE at.docente_id = ? "
            f"AND at.alumno_id = {ap}.alumno_id AND at.ciclo_id = {p}.ciclo_id))", [d, d])


def scope_practicas(p="p"):
    d = tutor_docente()
    if d is None:
        return "1=1", []
    return (f"({p}.tutor_id = ? OR EXISTS (SELECT 1 FROM alumnos_practicas ap2 JOIN asignaciones_tutor at "
            f"ON at.alumno_id = ap2.alumno_id AND at.ciclo_id = {p}.ciclo_id AND at.docente_id = ? "
            f"WHERE ap2.practica_id = {p}.id AND ap2.eliminado_en IS NULL))", [d, d])


def check_alumno_access(alumno_id):
    if not q1("SELECT 1 FROM alumnos WHERE id = ?", (alumno_id,)):
        abort(404)
    sql, params = scope_alumnos("a")
    if not q1(f"SELECT a.id FROM alumnos a WHERE a.id = ? AND {sql}", [alumno_id] + params):
        abort(403)


def check_practica_access(practica_id):
    if not q1("SELECT 1 FROM practicas WHERE id = ?", (practica_id,)):
        abort(404)
    sql, params = scope_practicas("p")
    if not q1(f"SELECT p.id FROM practicas p WHERE p.id = ? AND {sql}", [practica_id] + params):
        abort(403)


def check_ap_access(ap_id):
    if not q1("SELECT 1 FROM alumnos_practicas WHERE id = ?", (ap_id,)):
        abort(404)
    sql, params = scope_ap("ap", "p")
    if not q1(f"SELECT ap.id FROM alumnos_practicas ap JOIN practicas p ON p.id = ap.practica_id "
              f"WHERE ap.id = ? AND {sql}", [ap_id] + params):
        abort(403)


# ----------------------------------------------------------------- hooks
def before_request():
    load_user()
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
        expected = session.get("_csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, "Token de seguridad inválido o vencido. Recargue la página e intente nuevamente.")
    ep = request.endpoint
    if ep is None or ep in PUBLIC_ENDPOINTS:
        return None
    if not g.user:
        return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
    if g.user["debe_cambiar_clave"] and ep not in ("auth.cambiar_clave", "auth.logout"):
        return redirect(url_for("auth.cambiar_clave"))
    return None


def init_app(app):
    app.before_request(before_request)
    app.register_blueprint(bp)
    app.jinja_env.globals.update(can=can, csrf_token=csrf_token)


# ------------------------------------------------------------------ vistas
@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.inicio"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        u = q1("SELECT * FROM usuarios WHERE lower(username) = lower(?) AND eliminado_en IS NULL", (username,))
        error = "Usuario o contraseña incorrectos."
        if u and u["bloqueado_hasta"] and u["bloqueado_hasta"] > now():
            error = "Cuenta bloqueada temporalmente por intentos fallidos. Intente más tarde."
        elif u and u["activo"] and check_password_hash(u["password_hash"], password):
            session.clear()
            session["uid"] = u["id"]
            session["last"] = time.time()
            session.permanent = True
            csrf_token()
            ex("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL, ultimo_acceso = ? WHERE id = ?",
               (now(), u["id"]), commit=False)
            g.user = u
            audit("usuarios", u["id"], "login", {"usuario": u["username"]})
            commit()
            return redirect(safe_next(request.args.get("next"), url_for("main.inicio")))
        elif u:
            intentos = u["intentos_fallidos"] + 1
            bloqueo = None
            if intentos >= MAX_INTENTOS:
                bloqueo = (datetime.now() + timedelta(minutes=BLOQUEO_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
                intentos = 0
            ex("UPDATE usuarios SET intentos_fallidos = ?, bloqueado_hasta = ? WHERE id = ?",
               (intentos, bloqueo, u["id"]), commit=False)
            audit("usuarios", u["id"], "login_fallido", {"usuario": username})
            commit()
        else:
            audit("usuarios", None, "login_fallido", {"usuario": username})
            commit()
        flash(error, "danger")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit("usuarios", g.user["id"], "logout")
        commit()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/cuenta/clave", methods=["GET", "POST"])
def cambiar_clave():
    forzado = bool(g.user["debe_cambiar_clave"])
    if request.method == "POST":
        actual = request.form.get("actual") or ""
        nueva = request.form.get("nueva") or ""
        repetir = request.form.get("repetir") or ""
        if not check_password_hash(g.user["password_hash"], actual):
            flash("La contraseña actual no es correcta.", "danger")
        elif nueva != repetir:
            flash("Las contraseñas nuevas no coinciden.", "danger")
        elif check_password_hash(g.user["password_hash"], nueva):
            flash("La nueva contraseña debe ser distinta de la actual.", "danger")
        elif (msg := validate_password(nueva, g.user["username"])):
            flash(msg, "danger")
        else:
            ex("UPDATE usuarios SET password_hash = ?, debe_cambiar_clave = 0, modificado_en = ?, modificado_por = ? WHERE id = ?",
               (hash_password(nueva), now(), g.user["id"], g.user["id"]), commit=False)
            audit("usuarios", g.user["id"], "cambio_clave", {"usuario": g.user["username"]})
            commit()
            flash("Contraseña actualizada correctamente.", "success")
            return redirect(url_for("main.inicio"))
    return render_template("cambiar_clave.html", forzado=forzado)
