"""Prácticas profesionalizantes (módulo central): ABM, ficha, asignación de alumnos y estados."""
from datetime import date

from flask import abort, flash, g, redirect, render_template, request, url_for

from .. import auth, services
from ..auth import perm_required
from ..crud import C, Entity, F
from ..db import audit, commit, ex, get_db, now, q, q1, today
from ..services import CERRADOS, ESTADOS_PP, REQUIERE_DOCS

ESTADOS_ASIGNABLES = ESTADOS_PP[:7]  # Aprobada / Desaprobada se fijan al cargar el resultado de la evaluación


def _fields(row=None):
    emp = q("SELECT id, razon_social FROM empresas WHERE eliminado_en IS NULL AND (activo = 1 OR id = ?) ORDER BY razon_social", (row["empresa_id"] if row else 0,))
    tut = q("SELECT id, apellido || ', ' || nombre AS n FROM docentes WHERE eliminado_en IS NULL AND (activo = 1 OR id = ?) ORDER BY apellido, nombre", (row["tutor_id"] if row and row["tutor_id"] else 0,))
    refs = q("SELECT r.id, r.nombre, r.empresa_id, e.razon_social FROM referentes_empresas r JOIN empresas e ON e.id = r.empresa_id "
             "WHERE r.eliminado_en IS NULL AND r.activo = 1 ORDER BY e.razon_social, r.nombre")
    esp = services.lista_especialidades()
    ciclo = services.ciclo_activo()
    fields = []
    if row:
        fields.append(F("numero", "N° de práctica", readonly=True))
    fields += [
        F("ciclo_id", "Año lectivo", "select", required=True, coerce=int, default=lambda: ciclo["id"] if ciclo else "",
          choices=[(c["id"], c["anio"]) for c in services.lista_ciclos()]),
        F("empresa_id", "Empresa o institución", "select", required=True, coerce=int, choices=[(e["id"], e["razon_social"]) for e in emp]),
        F("especialidad_id", "Especialidad técnica", "select", coerce=int,
          choices=[(e["id"], e["nombre"], {"data-horas": e["horas_pp"]}) for e in esp],
          attrs={"data-fill-target": "#f_horas_requeridas", "data-fill-attr": "data-horas"}),
        F("tutor_id", "Docente tutor", "select", coerce=int, choices=[(t["id"], t["n"]) for t in tut]),
        F("referente_id", "Referente de la empresa", "select", coerce=int,
          choices=[(r["id"], f"{r['nombre']} — {r['razon_social']}", {"data-empresa": r["empresa_id"]}) for r in refs],
          attrs={"data-depends-on": "#f_empresa_id", "data-depends-attr": "data-empresa"},
          help="Se listan los referentes de la empresa elegida."),
        F("proyecto", "Proyecto o actividad formativa", required=True, maxlen=250, cols=2),
        F("fecha_inicio", "Fecha de inicio", "date"),
        F("fecha_fin_estimada", "Fecha estimada de finalización", "date"),
        F("fecha_fin_real", "Fecha real de finalización", "date"),
        F("horas_requeridas", "Horas totales requeridas", "number", required=True, min=1, max=2000,
          default=lambda: ciclo["horas_pp_default"] if ciclo else 200),
        F("dias_semana", "Días de práctica", "weekdays", required=True, default="1,2,3,4,5",
          help="En estos días las horas de cada alumno corren automáticamente entre la fecha de inicio y la de finalización."),
        F("horas_dia", "Horas por día de práctica", "number", required=True, decimal=True, min=0.5, max=14, default=4,
          help="Jornada que se suma cada día de práctica. Sólo se cargan las inasistencias, que descuentan horas."),
        F("dias_horarios", "Horario (texto informativo)", maxlen=300, cols=2, placeholder="8:00 a 12:00"),
        F("objetivos", "Objetivos de aprendizaje", "textarea", cols=2),
        F("actividades", "Actividades a desarrollar", "textarea", cols=2),
        F("observaciones", "Observaciones", "textarea", cols=2),
    ]
    return fields


def _before(vals, row, errors):
    ini, fin, real = vals.get("fecha_inicio"), vals.get("fecha_fin_estimada"), vals.get("fecha_fin_real")
    if ini and fin and fin < ini:
        errors["fecha_fin_estimada"] = "No puede ser anterior a la fecha de inicio."
    if ini and real and real < ini:
        errors["fecha_fin_real"] = "No puede ser anterior a la fecha de inicio."
    if vals.get("referente_id") and vals.get("empresa_id") and "referente_id" not in errors:
        r = q1("SELECT empresa_id FROM referentes_empresas WHERE id = ?", (vals["referente_id"],))
        if not r or r["empresa_id"] != vals["empresa_id"]:
            errors["referente_id"] = "El referente no pertenece a la empresa seleccionada."
    if row is None and vals.get("ciclo_id"):
        vals["numero"] = services.siguiente_numero(vals["ciclo_id"])
    if row is None:
        vals["estado"] = "Pendiente de asignación"


def _after(id, data, nuevo, row):
    if data.get("tutor_id"):
        for r in q("SELECT alumno_id FROM alumnos_practicas WHERE practica_id = ? AND eliminado_en IS NULL", (id,)):
            services.asegurar_asignacion_tutor(data["tutor_id"], r["alumno_id"], data["ciclo_id"])


SELECT = """
SELECT t.*, ci.anio, em.razon_social AS empresa, e.nombre AS especialidad, d.apellido || ', ' || d.nombre AS tutor,
  (SELECT COUNT(*) FROM alumnos_practicas ap WHERE ap.practica_id = t.id AND ap.eliminado_en IS NULL) AS n_alumnos
FROM practicas t
JOIN ciclos_lectivos ci ON ci.id = t.ciclo_id
JOIN empresas em ON em.id = t.empresa_id
LEFT JOIN especialidades e ON e.id = t.especialidad_id
LEFT JOIN docentes d ON d.id = t.tutor_id
"""
ALUMNOS_TXT = ("(SELECT group_concat(a.apellido || ' ' || a.nombre || ' ' || a.dni || ' ' || a.legajo, ' ') FROM alumnos_practicas ap "
               "JOIN alumnos a ON a.id = ap.alumno_id WHERE ap.practica_id = t.id AND ap.eliminado_en IS NULL)")

practicas = Entity(
    "practicas", "practicas", "Práctica", "Prácticas profesionalizantes", "practicas", fields=_fields,
    columns=[C("numero", "N°", link=True), C("anio", "Año"), C("empresa", "Empresa"), C("proyecto", "Proyecto"),
             C("especialidad", "Especialidad"), C("tutor", "Tutor"), C("fecha_inicio", "Inicio", "date"),
             C("fecha_fin_estimada", "Fin estimado", "date"), C("n_alumnos", "Alumnos"), C("estado", "Estado", "badge")],
    select=SELECT, search=("t.numero", "t.proyecto", "em.razon_social", ALUMNOS_TXT),
    filters=[
        dict(name="ciclo", label="Año lectivo", expr="t.ciclo_id", choices=lambda: [(c["id"], c["anio"]) for c in services.lista_ciclos()]),
        dict(name="empresa", label="Empresa", expr="t.empresa_id", choices=lambda: [(e["id"], e["nombre"]) for e in services.lista_empresas(False)]),
        dict(name="especialidad", label="Especialidad", expr="t.especialidad_id", choices=lambda: services.choices(services.lista_especialidades())),
        dict(name="curso", label="Curso de los alumnos", choices=lambda: services.choices(services.lista_cursos()),
             sql="EXISTS (SELECT 1 FROM alumnos_practicas ap JOIN alumnos a ON a.id = ap.alumno_id WHERE ap.practica_id = t.id AND ap.eliminado_en IS NULL AND a.curso_id = ?)"),
        dict(name="tutor", label="Tutor", expr="t.tutor_id", choices=lambda: [(d["id"], d["nombre"]) for d in services.lista_docentes(False)]),
        dict(name="estado", label="Estado", expr="t.estado", choices=[(e, e) for e in ESTADOS_PP]),
    ],
    order="ci.anio DESC, t.numero DESC", detail=True, before_save=_before, after_save=_after,
    scope=lambda: auth.scope_practicas("t"), access=auth.check_practica_access, nuevo_label="Nueva práctica",
    deps=(("SELECT COUNT(*) FROM alumnos_practicas WHERE practica_id = ? AND eliminado_en IS NULL", "alumno(s) asignado(s)"),),
    subtitle="Busque por número, proyecto, empresa o por apellido, DNI o legajo de un alumno.",
)

bp = practicas.blueprint()


def _get_practica(id):
    auth.check_practica_access(id)
    p = q1("""SELECT t.*, ci.anio, em.razon_social AS empresa, e.nombre AS especialidad, d.apellido || ', ' || d.nombre AS tutor,
                     r.nombre AS referente, r.telefono AS ref_tel, r.email AS ref_email
              FROM practicas t JOIN ciclos_lectivos ci ON ci.id = t.ciclo_id JOIN empresas em ON em.id = t.empresa_id
              LEFT JOIN especialidades e ON e.id = t.especialidad_id LEFT JOIN docentes d ON d.id = t.tutor_id
              LEFT JOIN referentes_empresas r ON r.id = t.referente_id
              WHERE t.id = ?""", (id,))
    if not p or p["eliminado_en"]:
        abort(404)
    return p


@bp.route("/<int:id>")
@perm_required("practicas.ver")
def ficha(id):
    p = _get_practica(id)
    aps = services.ap_rows({"practica_id": id})
    todos = request.args.get("todos") == "1"
    candidatos = []
    if auth.can("practicas.editar"):
        candidatos = q("""SELECT a.id, a.apellido || ', ' || a.nombre AS alumno, c.nombre AS curso, dv.nombre AS division, e.nombre AS especialidad,
                          (SELECT COUNT(*) FROM alumnos_practicas ap2 JOIN practicas p2 ON p2.id = ap2.practica_id
                           WHERE ap2.alumno_id = a.id AND p2.ciclo_id = ? AND ap2.eliminado_en IS NULL AND p2.eliminado_en IS NULL) AS en_ciclo
                          FROM alumnos a JOIN cursos c ON c.id = a.curso_id LEFT JOIN divisiones dv ON dv.id = a.division_id
                          LEFT JOIN especialidades e ON e.id = a.especialidad_id
                          WHERE a.eliminado_en IS NULL AND a.estado_academico IN ('Regular','Repitente') AND (c.requiere_pp = 1 OR ? = 1)
                            AND a.id NOT IN (SELECT alumno_id FROM alumnos_practicas WHERE practica_id = ? AND eliminado_en IS NULL)
                          ORDER BY c.orden, a.apellido, a.nombre""", (p["ciclo_id"], 1 if todos else 0, id))
    docs = q("SELECT d.*, td.nombre AS tipo, a.apellido || ', ' || a.nombre AS alumno FROM documentos d "
             "JOIN tipos_documentos td ON td.id = d.tipo_documento_id LEFT JOIN alumnos a ON a.id = d.alumno_id "
             "WHERE d.practica_id = ? AND d.eliminado_en IS NULL ORDER BY d.creado_en DESC", (id,))
    visitas = q("SELECT s.*, a.apellido || ', ' || a.nombre AS alumno, dc.apellido || ', ' || dc.nombre AS docente, "
                "r.nombre AS referente FROM seguimiento s LEFT JOIN referentes_empresas r ON r.id = s.referente_id "
                "LEFT JOIN alumnos_practicas ap ON ap.id = s.alumno_practica_id LEFT JOIN alumnos a ON a.id = ap.alumno_id "
                "LEFT JOIN docentes dc ON dc.id = s.docente_id WHERE s.practica_id = ? AND s.eliminado_en IS NULL "
                "ORDER BY s.fecha DESC, s.id DESC LIMIT 10", (id,))
    ocup = services.ocupacion_empresas().get(p["empresa_id"], 0)
    emp = q1("SELECT vacantes FROM empresas WHERE id = ?", (p["empresa_id"],))
    return render_template("practicas/ficha.html", p=p, aps=aps, candidatos=candidatos, docs=docs, visitas=visitas, todos=todos,
                           estados=ESTADOS_ASIGNABLES, ciclos=services.lista_ciclos(),
                           vacantes=emp["vacantes"], ocupadas=ocup,
                           horas_total=sum(r["horas_realizadas"] for r in aps), horas_req=sum(r["horas_requeridas"] for r in aps),
                           horas_plan=services.horas_plan(p), dias_txt=services.dias_texto(p["dias_semana"]))


def _set_estado_ap(ap, nuevo):
    sets, params = ["estado = ?", "modificado_en = ?", "modificado_por = ?"], [nuevo, now(), g.user["id"]]
    if nuevo == "En curso" and not ap["ap_inicio"]:
        sets.append("fecha_inicio = ?")
        params.append(ap["fecha_inicio"] or today())
    if nuevo == "Finalizada" and not ap["ap_fin"]:
        sets.append("fecha_fin_real = ?")
        params.append(today())
    ex(f"UPDATE alumnos_practicas SET {', '.join(sets)} WHERE id = ?", params + [ap["ap_id"]], commit=False)
    if nuevo == "Suspendida" and ap["estado"] != "Suspendida":
        services.abrir_suspension(ap["ap_id"])
    elif nuevo != "Suspendida" and ap["estado"] == "Suspendida":
        services.cerrar_suspension(ap["ap_id"])


@bp.route("/<int:id>/estado", methods=["POST"])
@perm_required("practicas.editar")
def cambiar_estado(id):
    p = _get_practica(id)
    nuevo = request.form.get("estado")
    forzar = request.form.get("forzar") == "1"
    if nuevo not in ESTADOS_ASIGNABLES:
        flash("Estado inválido.", "danger")
        return redirect(url_for("practicas.ficha", id=id))
    aps = [r for r in services.ap_rows({"practica_id": id}, scoped=False) if r["estado"] not in CERRADOS[1:]]
    if nuevo in REQUIERE_DOCS and not forzar:
        bloqueados = [r["alumno"] for r in aps if r["docs_estado"] != "Completa"]
        if bloqueados:
            flash("No se puede pasar a «" + nuevo + "»: documentación incompleta o sin revisar de " + ", ".join(bloqueados) +
                  ". Complete y apruebe los documentos, o marque «Forzar» (queda registrado).", "danger")
            return redirect(url_for("practicas.ficha", id=id))
    for r in aps:
        _set_estado_ap(r, nuevo)
    sets, params = ["estado = ?", "modificado_en = ?", "modificado_por = ?"], [nuevo, now(), g.user["id"]]
    if nuevo == "Finalizada" and not p["fecha_fin_real"]:
        sets.append("fecha_fin_real = ?")
        params.append(today())
    if nuevo == "En curso" and not p["fecha_inicio"]:
        sets.append("fecha_inicio = ?")
        params.append(today())
    ex(f"UPDATE practicas SET {', '.join(sets)} WHERE id = ?", params + [id], commit=False)
    audit("practicas", id, "modificar", {"estado": [p["estado"], nuevo], "forzado": forzar, "alumnos_afectados": len(aps)})
    commit()
    flash(f"Estado de la práctica actualizado a «{nuevo}».", "success")
    return redirect(url_for("practicas.ficha", id=id))


@bp.route("/<int:id>/alumnos", methods=["POST"])
@perm_required("practicas.editar")
def agregar_alumnos(id):
    p = _get_practica(id)
    ids = [int(x) for x in request.form.getlist("alumno_ids") if x.isdigit()]
    horas = request.form.get("horas", "").strip()
    if not ids:
        flash("Seleccione al menos un alumno.", "danger")
        return redirect(url_for("practicas.ficha", id=id))
    if horas and (not horas.isdigit() or not 1 <= int(horas) <= 2000):
        flash("Las horas deben ser un número entre 1 y 2000.", "danger")
        return redirect(url_for("practicas.ficha", id=id))
    horas = int(horas) if horas else p["horas_requeridas"]
    n = 0
    for aid in ids:
        a = q1("SELECT id FROM alumnos WHERE id = ? AND eliminado_en IS NULL", (aid,))
        if not a or q1("SELECT 1 FROM alumnos_practicas WHERE practica_id = ? AND alumno_id = ? AND eliminado_en IS NULL", (id, aid)):
            continue
        cur = ex("INSERT INTO alumnos_practicas(practica_id, alumno_id, horas_requeridas, estado, creado_en, creado_por) VALUES (?,?,?,?,?,?)",
                 (id, aid, horas, "Documentación pendiente" if p["estado"] not in CERRADOS else p["estado"], now(), g.user["id"]), commit=False)
        audit("alumnos_practicas", cur.lastrowid, "crear", {"practica": p["numero"], "alumno_id": aid, "horas": horas})
        services.asegurar_asignacion_tutor(p["tutor_id"], aid, p["ciclo_id"])
        n += 1
    if p["estado"] == "Pendiente de asignación" and n:
        ex("UPDATE practicas SET estado = 'En proceso de asignación' WHERE id = ?", (id,), commit=False)
    commit()
    ocup = services.ocupacion_empresas().get(p["empresa_id"], 0)
    vac = q1("SELECT vacantes FROM empresas WHERE id = ?", (p["empresa_id"],))["vacantes"]
    flash(f"{n} alumno(s) asignado(s) a la práctica.", "success")
    if ocup > vac:
        flash(f"Atención: la empresa tiene {vac} vacante(s) y {ocup} alumno(s) asignados.", "warning")
    return redirect(url_for("practicas.ficha", id=id))


def _get_ap(ap_id):
    auth.check_ap_access(ap_id)
    rows = services.ap_rows({"ap_id": ap_id}, scoped=False)
    if not rows:
        abort(404)
    return rows[0]


@bp.route("/ap/<int:ap_id>/estado", methods=["POST"])
@perm_required("practicas.editar")
def estado_ap(ap_id):
    ap = _get_ap(ap_id)
    nuevo = request.form.get("estado")
    forzar = request.form.get("forzar") == "1"
    if nuevo not in ESTADOS_ASIGNABLES:
        flash("Estado inválido.", "danger")
    elif ap["estado"] in ("Aprobada", "Desaprobada"):
        flash("La asignación ya tiene resultado final; modifique la evaluación para cambiarlo.", "danger")
    elif nuevo in REQUIERE_DOCS and ap["docs_estado"] != "Completa" and not forzar:
        flash(f"Documentación {ap['docs_estado'].lower()} de {ap['alumno']}: " + (", ".join(ap["docs_faltan"]) or "pendiente de revisión") +
              ". Complétela o marque «Forzar».", "danger")
    else:
        _set_estado_ap(ap, nuevo)
        audit("alumnos_practicas", ap_id, "modificar", {"estado": [ap["estado"], nuevo], "forzado": forzar})
        commit()
        flash(f"Estado de {ap['alumno']} actualizado a «{nuevo}».", "success")
    return redirect(request.form.get("next") or url_for("practicas.ficha", id=ap["practica_id"]))


@bp.route("/ap/<int:ap_id>/quitar", methods=["POST"])
@perm_required("practicas.editar")
def quitar_alumno(ap_id):
    ap = _get_ap(ap_id)
    tiene = q1("SELECT (SELECT COUNT(*) FROM asistencias WHERE alumno_practica_id = ? AND eliminado_en IS NULL) + "
               "(SELECT COUNT(*) FROM evaluaciones WHERE alumno_practica_id = ? AND eliminado_en IS NULL) AS n", (ap_id, ap_id))["n"]
    if tiene:
        flash("No se puede quitar: el alumno tiene inasistencias o evaluaciones registradas. Cambie su estado a «Suspendida» si corresponde.", "danger")
    else:
        ex("UPDATE alumnos_practicas SET eliminado_en = ?, eliminado_por = ? WHERE id = ?", (now(), g.user["id"], ap_id), commit=False)
        audit("alumnos_practicas", ap_id, "baja", {"practica": ap["numero"], "alumno": ap["alumno"]})
        commit()
        flash("Alumno quitado de la práctica.", "success")
    return redirect(url_for("practicas.ficha", id=ap["practica_id"]))


@bp.route("/<int:id>/reutilizar", methods=["POST"])
@perm_required("practicas.editar")
def reutilizar(id):
    p = _get_practica(id)
    ciclo = request.form.get("ciclo_id", "")
    if not ciclo.isdigit() or not q1("SELECT 1 FROM ciclos_lectivos WHERE id = ? AND eliminado_en IS NULL", (int(ciclo),)):
        flash("Seleccione un ciclo lectivo válido.", "danger")
        return redirect(url_for("practicas.ficha", id=id))
    numero = services.siguiente_numero(int(ciclo))
    cur = ex("""INSERT INTO practicas(numero, ciclo_id, empresa_id, especialidad_id, tutor_id, referente_id, proyecto, horas_requeridas,
                dias_horarios, dias_semana, horas_dia, objetivos, actividades, observaciones, estado, creado_en, creado_por)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'Pendiente de asignación', ?, ?)""",
             (numero, int(ciclo), p["empresa_id"], p["especialidad_id"], p["tutor_id"], p["referente_id"], p["proyecto"],
              p["horas_requeridas"], p["dias_horarios"], p["dias_semana"], p["horas_dia"], p["objetivos"], p["actividades"], p["observaciones"],
              now(), g.user["id"]), commit=False)
    audit("practicas", cur.lastrowid, "crear", {"copiada_de": p["numero"], "numero": numero})
    commit()
    flash(f"Se creó la práctica {numero} a partir de {p['numero']}. Complete las fechas y asigne alumnos.", "success")
    return redirect(url_for("practicas.editar", id=cur.lastrowid))


def blueprints():
    return [bp]
