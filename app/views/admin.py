"""Administración: usuarios, roles y permisos, configuración general, copias de seguridad e historial."""
import os
import re

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request, send_from_directory, session, url_for)

from .. import auth, backup, services
from ..auth import hash_password, perm_required, validate_password
from ..crud import C, Entity, F
from ..db import audit, commit, ex, get_config, q, q1, set_config

bp = Blueprint("admin", __name__, url_prefix="/configuracion")


# ------------------------------------------------------------------ usuarios
def _otros_admins(excluir_id):
    return q1("SELECT COUNT(*) AS n FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE r.codigo = 'admin' AND u.activo = 1 "
              "AND u.eliminado_en IS NULL AND u.id <> ?", (excluir_id,))["n"]


def _before_usuario(vals, row, errors):
    u = vals.get("username")
    if u and not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", u):
        errors["username"] = "Use 3 a 40 caracteres: letras, números, punto, guion o guion bajo."
    rol = q1("SELECT codigo FROM roles WHERE id = ?", (vals.get("rol_id"),)) if vals.get("rol_id") else None
    if rol and rol["codigo"] == "tutor" and not vals.get("docente_id"):
        errors["docente_id"] = "Un usuario con perfil «Docente tutor» debe estar vinculado a un docente."
    pw = vals.get("password")
    if row is None and not pw:
        errors["password"] = "Campo obligatorio para usuarios nuevos."
    if pw and "password" not in errors:
        msg = validate_password(pw, u or "")
        if msg:
            errors["password"] = msg
        else:
            vals["password_hash"] = hash_password(pw)
    if row is not None:
        es_admin_actual = q1("SELECT 1 FROM roles WHERE id = ? AND codigo = 'admin'", (row["rol_id"],))
        deja_de_serlo = not rol or rol["codigo"] != "admin" or not vals.get("activo")
        if es_admin_actual and deja_de_serlo and not _otros_admins(row["id"]):
            errors["rol_id"] = "No se puede quitar el último administrador activo."
        if row["id"] == g.user["id"] and not vals.get("activo"):
            errors["activo"] = "No puede desactivar su propio usuario."


def _baja_usuario(row):
    if row["id"] == g.user["id"]:
        return "No puede dar de baja su propio usuario."
    if q1("SELECT 1 FROM roles WHERE id = ? AND codigo = 'admin'", (row["rol_id"],)) and not _otros_admins(row["id"]):
        return "No se puede dar de baja al último administrador."
    return None


def _fields_usuario(row=None):
    return [
        F("username", "Usuario", required=True, unique=True, maxlen=40),
        F("nombre", "Apellido y nombre", required=True, maxlen=120),
        F("email", "Correo electrónico", "email"),
        F("rol_id", "Perfil", "select", required=True, coerce=int, choices=lambda: [(r["id"], r["nombre"]) for r in q("SELECT id, nombre FROM roles ORDER BY id")]),
        F("docente_id", "Docente vinculado (perfil tutor)", "select", coerce=int, choices=lambda: [(d["id"], d["nombre"]) for d in services.lista_docentes(False)],
          help="Define qué alumnos y prácticas verá un tutor."),
        F("password", "Contraseña" if not row else "Nueva contraseña (dejar vacío para no cambiarla)", "password", virtual=True, required=row is None,
          help="Mínimo 8 caracteres, con letras y números."),
        F("debe_cambiar_clave", "Obligar a cambiar la contraseña en el próximo ingreso", "checkbox", default=1, cols=2),
        F("activo", "Usuario activo", "checkbox", default=1),
    ]


usuarios = Entity(
    "usuarios", "usuarios", "Usuario", "Usuarios", "usuarios", fields=_fields_usuario,
    columns=[C("username", "Usuario"), C("nombre", "Nombre"), C("rol", "Perfil"), C("docente", "Docente vinculado"), C("email", "Correo"),
             C("ultimo_acceso", "Último acceso", "datetime"), C("activo", "Activo", "bool")],
    select="SELECT t.*, r.nombre AS rol, d.apellido || ', ' || d.nombre AS docente FROM usuarios t JOIN roles r ON r.id = t.rol_id "
           "LEFT JOIN docentes d ON d.id = t.docente_id",
    search=("t.username", "t.nombre", "t.email"),
    filters=[dict(name="rol", label="Perfil", expr="t.rol_id", choices=lambda: [(r["id"], r["nombre"]) for r in q("SELECT id, nombre FROM roles")])],
    order="t.nombre", before_save=_before_usuario, on_delete=_baja_usuario, nuevo_label="Nuevo usuario",
)
bp_usuarios = usuarios.blueprint()


# ------------------------------------------------------------- configuración
SCHOOL_KEYS = [
    ("escuela_nombre", "Nombre de la escuela", 150), ("escuela_cue", "CUE", 30), ("escuela_direccion", "Dirección", 150),
    ("escuela_localidad", "Localidad", 100), ("escuela_telefono", "Teléfono", 40), ("escuela_email", "Correo electrónico", 120),
]


@bp.route("/", methods=["GET", "POST"])
@perm_required("catalogos.ver", "configuracion.ver")
def configuracion():
    if request.method == "POST":
        if not auth.can("configuracion.editar"):
            from flask import abort
            abort(403)
        errs = []
        cambios = {}
        for k, _lbl, ml in SCHOOL_KEYS:
            v = (request.form.get(k) or "").strip()[:ml]
            if k == "escuela_nombre" and not v:
                errs.append("El nombre de la escuela es obligatorio.")
            cambios[k] = v
        try:
            nota = float((request.form.get("nota_aprobacion") or "").replace(",", "."))
            dias = int(request.form.get("dias_alerta_vencimiento") or "")
            umbral = float((request.form.get("umbral_ritmo") or "").replace(",", "."))
            if not (1 <= nota <= 10 and 0 <= dias <= 365 and 5 <= umbral <= 100):
                raise ValueError
            cambios.update(nota_aprobacion=f"{nota:g}", dias_alerta_vencimiento=str(dias), umbral_ritmo=f"{umbral:g}",
                           consulta_publica="1" if request.form.get("consulta_publica") else "0")
        except ValueError:
            errs.append("Revise los parámetros: nota de aprobación 1-10, días de alerta 0-365, umbral de ritmo 5-100.")
        if errs:
            for e in errs:
                flash(e, "danger")
        else:
            antes = {k: get_config(k, "") for k in cambios}
            for k, v in cambios.items():
                set_config(k, v)
            audit("configuracion", None, "modificar", {k: [antes[k], v] for k, v in cambios.items() if antes[k] != v})
            commit()
            flash("Configuración guardada.", "success")
            return redirect(url_for("admin.configuracion"))
    cfg = {k: get_config(k, "") for k in [s[0] for s in SCHOOL_KEYS] + ["nota_aprobacion", "dias_alerta_vencimiento", "umbral_ritmo"]}
    cfg["consulta_publica"] = get_config("consulta_publica", "1") or "1"
    return render_template("admin/configuracion.html", cfg=cfg, school_keys=SCHOOL_KEYS)


# ------------------------------------------------------------ roles y permisos
@bp.route("/roles", methods=["GET", "POST"])
@perm_required("configuracion.ver")
def roles():
    roles_ = q("SELECT * FROM roles ORDER BY id")
    permisos = q("SELECT * FROM permisos ORDER BY modulo, codigo")
    actuales = {(r["rol_id"], r["permiso_id"]) for r in q("SELECT * FROM rol_permisos")}
    if request.method == "POST":
        if not auth.can("configuracion.editar"):
            from flask import abort
            abort(403)
        nuevos = set()
        for r in roles_:
            if r["codigo"] == "admin":
                continue
            for p in permisos:
                if request.form.get(f"p_{r['id']}_{p['id']}"):
                    nuevos.add((r["id"], p["id"]))
        admin_id = next(r["id"] for r in roles_ if r["codigo"] == "admin")
        previos = {x for x in actuales if x[0] != admin_id}
        agregados, quitados = nuevos - previos, previos - nuevos
        for rid, pid in quitados:
            ex("DELETE FROM rol_permisos WHERE rol_id = ? AND permiso_id = ?", (rid, pid), commit=False)
        for rid, pid in agregados:
            ex("INSERT OR IGNORE INTO rol_permisos(rol_id, permiso_id) VALUES (?,?)", (rid, pid), commit=False)
        pn = {p["id"]: p["codigo"] for p in permisos}
        rn = {r["id"]: r["codigo"] for r in roles_}
        audit("roles", None, "modificar", {"agregados": [f"{rn[a]}:{pn[b]}" for a, b in agregados], "quitados": [f"{rn[a]}:{pn[b]}" for a, b in quitados]})
        commit()
        flash("Permisos actualizados. Rigen desde el próximo movimiento de cada usuario.", "success")
        return redirect(url_for("admin.roles"))
    modulos = {}
    for p in permisos:
        modulos.setdefault(p["modulo"], []).append(p)
    from ..bootstrap import MODULOS
    return render_template("admin/roles.html", roles=roles_, modulos=modulos, actuales=actuales, nombres=MODULOS)


# ------------------------------------------------------------------ respaldos
def _safe_backup_name(name):
    if not name or not backup.NAME_RE.match(name):
        return None
    path = os.path.join(current_app.config["BACKUP_DIR"], name)
    return name if os.path.isfile(path) else None


@bp.route("/respaldos")
@perm_required("configuracion.respaldo")
def respaldos():
    return render_template("admin/respaldos.html", copias=backup.list_backups(current_app))


@bp.route("/respaldos/crear", methods=["POST"])
@perm_required("configuracion.respaldo")
def respaldo_crear():
    name = backup.create_backup(current_app)
    backup.prune(current_app, keep=60)
    audit("configuracion", None, "backup", {"archivo": name})
    commit()
    flash(f"Copia de seguridad creada: {name}", "success")
    return redirect(url_for("admin.respaldos"))


@bp.route("/respaldos/<name>/descargar")
@perm_required("configuracion.respaldo")
def respaldo_descargar(name):
    name = _safe_backup_name(name)
    if not name:
        from flask import abort
        abort(404)
    audit("configuracion", None, "backup_descarga", {"archivo": name})
    commit()
    return send_from_directory(current_app.config["BACKUP_DIR"], name, as_attachment=True)


@bp.route("/respaldos/<name>/eliminar", methods=["POST"])
@perm_required("configuracion.respaldo")
def respaldo_eliminar(name):
    name = _safe_backup_name(name)
    if name:
        os.remove(os.path.join(current_app.config["BACKUP_DIR"], name))
        audit("configuracion", None, "backup_eliminado", {"archivo": name})
        commit()
        flash("Copia eliminada.", "success")
    return redirect(url_for("admin.respaldos"))


@bp.route("/respaldos/restaurar", methods=["POST"])
@perm_required("configuracion.respaldo")
def respaldo_restaurar():
    if request.form.get("confirmar", "").strip().upper() != "RESTAURAR":
        flash("Para restaurar debe escribir la palabra RESTAURAR en el campo de confirmación.", "danger")
        return redirect(url_for("admin.respaldos"))
    import io
    nombre = _safe_backup_name(request.form.get("nombre"))
    up = request.files.get("archivo")
    try:
        if up and up.filename:
            origen, etiqueta = io.BytesIO(up.read()), up.filename
        elif nombre:
            origen, etiqueta = os.path.join(current_app.config["BACKUP_DIR"], nombre), nombre
        else:
            flash("Seleccione una copia o suba un archivo ZIP.", "danger")
            return redirect(url_for("admin.respaldos"))
        backup.restore_backup(current_app, origen)
    except (ValueError, Exception) as exc:  # noqa: BLE001
        flash(f"No se pudo restaurar: {exc}", "danger")
        return redirect(url_for("admin.respaldos"))
    session.clear()
    flash("Restauración completada. Ingrese nuevamente con los usuarios del respaldo.", "success")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------------ historial
@bp.route("/historial")
@perm_required("configuracion.historial")
def historial():
    args = request.args
    where, params = ["1=1"], []
    for key, col in (("tabla", "h.tabla"), ("accion", "h.accion"), ("usuario", "h.usuario_id")):
        if args.get(key):
            where.append(f"{col} = ?")
            params.append(args[key])
    if args.get("desde"):
        where.append("h.fecha >= ?")
        params.append(args["desde"])
    if args.get("hasta"):
        where.append("h.fecha <= ?")
        params.append(args["hasta"] + " 23:59:59")
    if args.get("registro", "").isdigit():
        where.append("h.registro_id = ?")
        params.append(int(args["registro"]))
    total = q1(f"SELECT COUNT(*) AS n FROM historial_cambios h WHERE {' AND '.join(where)}", params)["n"]
    page = int(args["page"]) if args.get("page", "").isdigit() and int(args["page"]) > 0 else 1
    rows = q(f"SELECT h.* FROM historial_cambios h WHERE {' AND '.join(where)} ORDER BY h.id DESC LIMIT 50 OFFSET ?", params + [(page - 1) * 50])
    return render_template("admin/historial.html", rows=rows, total=total, page=page, pages=max(1, -(-total // 50)), args=args,
                           tablas=[r[0] for r in q("SELECT DISTINCT tabla FROM historial_cambios ORDER BY 1")],
                           acciones=[r[0] for r in q("SELECT DISTINCT accion FROM historial_cambios ORDER BY 1")],
                           usuarios=q("SELECT id, nombre FROM usuarios ORDER BY nombre"))


def blueprints():
    return [bp, bp_usuarios]
