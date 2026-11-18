"""Seguimiento y control de horas: asistencia automática, inasistencias, visitas y avance por alumno."""
from datetime import date

from flask import abort, flash, g, redirect, render_template, request, url_for

from .. import auth, services
from ..auth import perm_required
from ..crud import C, Entity, F
from ..db import audit, commit, ex, now, q, q1, today

TIPOS_ASIST = services.TIPOS_INASISTENCIA
ADMITEN_INASISTENCIAS = ("En curso", "Suspendida", "Finalizada")


def _access_asistencia(id):
    r = q1("SELECT alumno_practica_id FROM asistencias WHERE id = ?", (id,))
    if r:
        auth.check_ap_access(r["alumno_practica_id"])


def _ap_calc(ap_id):
    rows = services.ap_rows({"ap_id": ap_id}, scoped=False, con_docs=False)
    return rows[0] if rows else None


def _motivo_no_programado(ap, fecha):
    """Explica por qué en una fecha no corren horas para la asignación (None si es un día de práctica)."""
    if fecha in ap["dias_prog"]:
        return None
    if fecha > today():
        return "No se pueden registrar inasistencias en una fecha futura."
    if ap["estado"] not in services.CORREN_HORAS or not ap["periodo_desde"]:
        return "La práctica del alumno todavía no comenzó: no hay horas corriendo."
    if fecha < ap["periodo_desde"] or fecha > ap["periodo_hasta"]:
        return (f"La fecha está fuera del período de la práctica ({ap['periodo_desde'][8:10]}/{ap['periodo_desde'][5:7]}/{ap['periodo_desde'][:4]} "
                f"al {ap['periodo_hasta'][8:10]}/{ap['periodo_hasta'][5:7]}/{ap['periodo_hasta'][:4]}).")
    return "Ese día no es un día de práctica (día no laborable de la práctica, feriado, receso o suspensión): no hay horas para descontar."


def _horas_inasistencia(valor, jornada):
    """Horas a descontar: vacío = jornada completa. Devuelve (horas, error)."""
    if valor in (None, ""):
        return jornada, None
    try:
        h = round(float(str(valor).replace(",", ".")), 2)
    except ValueError:
        return None, "Número inválido."
    if h <= 0:
        return None, "Debe ser mayor que cero."
    if h > jornada:
        return None, f"No puede superar la jornada de la práctica ({jornada:g} h)."
    return h, None


def _before_asist(vals, row, errors):
    ap_id = vals.get("alumno_practica_id")
    if not ap_id:
        return
    auth.check_ap_access(ap_id)
    ap = _ap_calc(ap_id)
    if not ap:
        errors["alumno_practica_id"] = "Asignación inexistente."
        return
    if ap["estado"] in ("Aprobada", "Desaprobada"):
        errors["fecha"] = "La práctica ya tiene resultado final: no admite nuevas inasistencias."
        return
    if vals.get("fecha") and "fecha" not in errors:
        motivo = _motivo_no_programado(ap, vals["fecha"])
        if motivo:
            errors["fecha"] = motivo
        elif q1("SELECT id FROM asistencias WHERE alumno_practica_id = ? AND fecha = ? AND eliminado_en IS NULL AND id <> ?",
                (ap_id, vals["fecha"], row["id"] if row else 0)):
            errors["fecha"] = "Ya existe una inasistencia registrada para ese día."
    if "horas" not in errors:
        h, err = _horas_inasistencia(vals.get("horas"), float(ap["horas_dia"]))
        if err:
            errors["horas"] = err
        vals["horas"] = h
    vals["hora_entrada"] = vals["hora_salida"] = None


asistencias = Entity(
    "asistencias", "asistencias", "Inasistencia", "Inasistencias", "seguimiento",
    fields=[
        F("alumno_practica_id", "Asignación", "hidden", coerce=int, required=True),
        F("fecha", "Fecha de la inasistencia", "date", required=True, default=today),
        F("tipo", "Tipo", "select", required=True, default=TIPOS_ASIST[0], choices=[(t, t) for t in TIPOS_ASIST]),
        F("horas", "Horas a descontar", "number", decimal=True, min=0.25, max=14,
          help="Déjelo vacío para descontar la jornada completa. Use un valor menor si el alumno faltó sólo una parte del día."),
        F("observaciones", "Motivo / observaciones", maxlen=300, cols=2),
    ],
    columns=[C("fecha", "Fecha", "date")], listable=False, before_save=_before_asist, access=_access_asistencia,
    nuevo_label="Registrar inasistencia",
)

visitas = Entity(
    "visitas", "seguimiento", "Registro de seguimiento", "Seguimiento", "seguimiento",
    fields=lambda row=None: [
        F("practica_id", "Práctica", "hidden", coerce=int, required=True),
        F("alumno_practica_id", "Alumno (opcional)", "select", coerce=int,
          choices=lambda: _aps_choices(request.values.get("practica_id") or (row["practica_id"] if row else 0))),
        F("fecha", "Fecha", "date", required=True, default=today),
        F("tipo", "Tipo", "select", required=True, default="Visita", choices=[(t, t) for t in ("Visita", "Llamado", "Reunión", "Informe", "Otro")]),
        F("docente_id", "Docente que realiza el seguimiento", "select", coerce=int,
          default=lambda: g.user["docente_id"] or "", choices=lambda: [(d["id"], d["nombre"]) for d in services.lista_docentes(False)]),
        F("referente_id", "Referente de la empresa", "select", coerce=int,
          choices=lambda: _referentes_choices(request.values.get("practica_id") or (row["practica_id"] if row else 0),
                                              row["referente_id"] if row else None),
          help="Referente de la empresa que participó o fue contactado (opcional)."),
        F("descripcion", "Descripción / observaciones", "textarea", required=True, cols=2, maxlen=3000),
    ],
    columns=[C("fecha", "Fecha", "date")], listable=False,
    access=lambda id: auth.check_practica_access(q1("SELECT practica_id FROM seguimiento WHERE id = ?", (id,))["practica_id"]),
    before_save=lambda vals, row, errors: _before_visita(vals, row, errors),
    nuevo_label="Registrar seguimiento",
)


def _referentes_choices(practica_id, actual=None):
    """Referentes activos de la empresa de la práctica (más el ya elegido, aunque se haya dado de baja)."""
    if not str(practica_id).isdigit():
        return []
    rows = q("SELECT r.id, r.nombre, r.cargo FROM referentes_empresas r JOIN practicas p ON p.empresa_id = r.empresa_id "
             "WHERE p.id = ? AND ((r.eliminado_en IS NULL AND r.activo = 1) OR r.id = ?) ORDER BY r.nombre",
             (practica_id, actual or 0))
    return [(r["id"], r["nombre"] + (f" ({r['cargo']})" if r["cargo"] else "")) for r in rows]


def _before_visita(vals, row, errors):
    if vals.get("practica_id"):
        auth.check_practica_access(vals["practica_id"])
    if vals.get("referente_id") and vals.get("practica_id"):
        ok = q1("SELECT 1 FROM referentes_empresas r JOIN practicas p ON p.empresa_id = r.empresa_id WHERE p.id = ? AND r.id = ?",
                (vals["practica_id"], vals["referente_id"]))
        if not ok:
            errors["referente_id"] = "El referente no pertenece a la empresa de la práctica."


def _aps_choices(practica_id):
    if not str(practica_id).isdigit():
        return []
    return [(r["ap_id"], r["alumno"]) for r in services.ap_rows({"practica_id": practica_id})]


bp = auth_bp = None
from flask import Blueprint  # noqa: E402
bp = Blueprint("seguimiento", __name__, url_prefix="/seguimiento")
bp_asist = asistencias.blueprint("/seguimiento/asistencias")
bp_vis = visitas.blueprint("/seguimiento/visitas")


@bp.route("/")
@perm_required("seguimiento.ver")
def inicio():
    args = request.args
    ciclo = services.ciclo_activo()
    filtros = {
        "ciclo_id": args.get("ciclo_id", ciclo["id"] if ciclo and "ciclo_id" not in args else ""),
        "especialidad_id": args.get("especialidad_id"), "curso_id": args.get("curso_id"), "division_id": args.get("division_id"),
        "empresa_id": args.get("empresa_id"), "tutor_id": args.get("tutor_id"), "estado": args.get("estado"),
    }
    rows = services.ap_rows(filtros, order="a.apellido, a.nombre")
    if args.get("pendientes") == "1":
        rows = [r for r in rows if r["horas_faltan"] > 0 and r["estado"] in ("En curso", "Finalizada", "Suspendida", "Habilitada para comenzar")]
    if args.get("q"):
        from ..db import norm
        t = norm(args["q"])
        rows = [r for r in rows if t in norm(r["alumno"] + " " + r["dni"] + " " + r["empresa"] + " " + r["numero"])]
    total_req = sum(r["horas_requeridas"] for r in rows)
    total_hs = sum(r["horas_realizadas"] for r in rows)
    return render_template("seguimiento/inicio.html", rows=rows, f=filtros, args=args,
                           ciclos=services.lista_ciclos(), especialidades=services.lista_especialidades(), cursos=services.lista_cursos(),
                           divisiones=services.lista_divisiones(), empresas=services.lista_empresas(False),
                           docentes=services.lista_docentes(False), estados=services.ESTADOS_PP,
                           cumplimiento=round(total_hs * 100 / total_req, 1) if total_req else 0, total_hs=total_hs, total_req=total_req)


@bp.route("/ap/<int:ap_id>")
@perm_required("seguimiento.ver")
def detalle(ap_id):
    auth.check_ap_access(ap_id)
    rows = services.ap_rows({"ap_id": ap_id}, scoped=False)
    if not rows:
        abort(404)
    ap = rows[0]
    asist = [dict(a, aplica=a["fecha"] in ap["dias_prog"]) for a in
             q("SELECT * FROM asistencias WHERE alumno_practica_id = ? AND eliminado_en IS NULL AND tipo <> 'Presente' ORDER BY fecha DESC", (ap_id,))]
    segs = q("SELECT s.*, dc.apellido || ', ' || dc.nombre AS docente, r.nombre AS referente FROM seguimiento s "
             "LEFT JOIN docentes dc ON dc.id = s.docente_id LEFT JOIN referentes_empresas r ON r.id = s.referente_id "
             "WHERE s.practica_id = ? AND (s.alumno_practica_id = ? OR s.alumno_practica_id IS NULL) AND s.eliminado_en IS NULL "
             "ORDER BY s.fecha DESC, s.id DESC", (ap["practica_id"], ap_id))
    pct_asist = round(ap["horas_realizadas"] * 100 / ap["horas_programadas"], 1) if ap["horas_programadas"] else None
    return render_template("seguimiento/detalle.html", ap=ap, asist=asist, segs=segs, pct_asist=pct_asist, tipos=TIPOS_ASIST, hoy=today(),
                           cerrado=ap["estado"] in ("Aprobada", "Desaprobada"), admite=ap["estado"] in ADMITEN_INASISTENCIAS,
                           dias_txt=services.dias_texto(ap["dias_semana"]))


@bp.route("/practica/<int:practica_id>/dia", methods=["GET", "POST"])
@perm_required("seguimiento.editar")
def dia(practica_id):
    auth.check_practica_access(practica_id)
    p = q1("SELECT p.*, em.razon_social AS empresa FROM practicas p JOIN empresas em ON em.id = p.empresa_id WHERE p.id = ? AND p.eliminado_en IS NULL", (practica_id,))
    if not p:
        abort(404)
    fecha = request.values.get("fecha") or today()
    try:
        date.fromisoformat(fecha)
    except ValueError:
        fecha = today()
    todos = services.ap_rows({"practica_id": practica_id}, con_docs=False)
    aps = [r for r in todos if r["estado"] in ADMITEN_INASISTENCIAS and fecha in r["dias_prog"]]
    if request.method == "POST":
        n, quitadas, errores = 0, 0, []
        previas = {r["alumno_practica_id"]: r for r in q("SELECT * FROM asistencias WHERE fecha = ? AND eliminado_en IS NULL AND tipo <> 'Presente'", (fecha,))}
        for r in aps:
            tipo = request.form.get(f"tipo_{r['ap_id']}", "")
            existente = previas.get(r["ap_id"])
            if not tipo:                      # asistió: si había una inasistencia cargada, se quita
                if existente:
                    ex("UPDATE asistencias SET eliminado_en = ?, eliminado_por = ? WHERE id = ?", (now(), g.user["id"], existente["id"]), commit=False)
                    audit("asistencias", existente["id"], "baja", {"fecha": fecha, "tipo": existente["tipo"], "horas": existente["horas"]})
                    quitadas += 1
                continue
            if tipo not in TIPOS_ASIST:
                errores.append(f"{r['alumno']}: tipo inválido")
                continue
            horas, err = _horas_inasistencia(request.form.get(f"hs_{r['ap_id']}", "").strip(), float(r["horas_dia"]))
            if err:
                errores.append(f"{r['alumno']}: horas a descontar — {err}")
                continue
            obs = (request.form.get(f"obs_{r['ap_id']}") or "").strip()[:300] or None
            if existente:
                if (existente["tipo"], existente["horas"], existente["observaciones"]) == (tipo, horas, obs):
                    continue
                ex("UPDATE asistencias SET tipo=?, hora_entrada=NULL, hora_salida=NULL, horas=?, observaciones=?, modificado_en=?, modificado_por=? WHERE id=?",
                   (tipo, horas, obs, now(), g.user["id"], existente["id"]), commit=False)
                audit("asistencias", existente["id"], "modificar", {"fecha": fecha, "tipo": tipo, "horas": horas})
            else:
                cur = ex("INSERT INTO asistencias(alumno_practica_id, fecha, tipo, horas, observaciones, creado_en, creado_por) VALUES (?,?,?,?,?,?,?)",
                         (r["ap_id"], fecha, tipo, horas, obs, now(), g.user["id"]), commit=False)
                audit("asistencias", cur.lastrowid, "crear", {"fecha": fecha, "tipo": tipo, "horas": horas})
            n += 1
        commit()
        f_txt = f"{fecha[8:10]}/{fecha[5:7]}/{fecha[:4]}"
        if n:
            flash(f"Inasistencias del {f_txt}: {n} registrada(s) o actualizada(s).", "success")
        if quitadas:
            flash(f"Inasistencias del {f_txt}: {quitadas} quitada(s).", "success")
        if not n and not quitadas and not errores:
            flash("Sin cambios: todos los alumnos figuran presentes ese día.", "info")
        for e in errores:
            flash(e, "danger")
        return redirect(url_for("seguimiento.dia", practica_id=practica_id, fecha=fecha))
    previas = {r["alumno_practica_id"]: r for r in q("SELECT * FROM asistencias WHERE fecha = ? AND eliminado_en IS NULL AND tipo <> 'Presente'", (fecha,))}
    aviso = None
    if not aps:
        activos = [r for r in todos if r["estado"] in ADMITEN_INASISTENCIAS]
        aviso = _motivo_no_programado(activos[0], fecha) if activos else "No hay alumnos con la práctica en curso."
    return render_template("seguimiento/dia.html", p=p, aps=aps, fecha=fecha, previas=previas, tipos=TIPOS_ASIST, aviso=aviso, hoy=today(),
                           dias_txt=services.dias_texto(p["dias_semana"]))


def blueprints():
    return [bp, bp_asist, bp_vis]
