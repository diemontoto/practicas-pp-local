"""Informes y reportes (HTML, PDF y Excel) con encabezado institucional y filtros aplicados."""
import io
from datetime import datetime

from flask import Blueprint, Response, abort, g, render_template, request, url_for
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import auth, pdfs, services
from ..auth import perm_required
from ..db import all_config, audit, commit, q, q1
from ..filters import plain

bp = Blueprint("informes", __name__, url_prefix="/informes")

FILTRO_LABELS = {"ciclo_id": "Año lectivo", "especialidad_id": "Especialidad", "curso_id": "Curso", "division_id": "División",
                 "empresa_id": "Empresa", "estado": "Estado", "tutor_id": "Tutor", "desde": "Desde", "hasta": "Hasta"}


def _ap(f, estados=None, order="a.apellido, a.nombre", **kw):
    rows = services.ap_rows(f, order=order, **kw)
    if estados:
        rows = [r for r in rows if r["estado"] in estados]
    return rows


def r_alumnos(f):
    where, params = ["a.eliminado_en IS NULL"], []
    for key, col in (("ciclo_id", "a.ciclo_id"), ("especialidad_id", "a.especialidad_id"), ("curso_id", "a.curso_id"), ("division_id", "a.division_id")):
        if f.get(key):
            where.append(f"{col} = ?")
            params.append(f[key])
    s, sp = auth.scope_alumnos("a")
    rows = q("SELECT a.*, c.nombre AS curso, d.nombre AS division, e.nombre AS esp, ci.anio FROM alumnos a JOIN cursos c ON c.id = a.curso_id "
             "LEFT JOIN divisiones d ON d.id = a.division_id LEFT JOIN especialidades e ON e.id = a.especialidad_id LEFT JOIN ciclos_lectivos ci ON ci.id = a.ciclo_id "
             f"WHERE {' AND '.join(where)} AND {s} ORDER BY a.apellido, a.nombre", params + sp)
    cols = [("Legajo", 1, "text"), ("Apellido y nombre", 3, "text"), ("DNI", 1.3, "text"), ("Curso", 1.1, "text"), ("División", 0.9, "text"),
            ("Especialidad", 2, "text"), ("Año cursada", 0.9, "text"), ("Estado", 1.2, "text"), ("Teléfono", 1.5, "text"), ("Responsable", 2, "text")]
    return cols, [[r["legajo"], f"{r['apellido']}, {r['nombre']}", r["dni"], r["curso"], r["division"], r["esp"], r["anio"], r["estado_academico"],
                   r["telefono"], r["tutor_legal_nombre"]] for r in rows], []


def r_alumnos_especialidad(f):
    where, params = ["a.eliminado_en IS NULL"], []
    for key, col in (("ciclo_id", "a.ciclo_id"), ("especialidad_id", "a.especialidad_id"), ("curso_id", "a.curso_id"), ("division_id", "a.division_id")):
        if f.get(key):
            where.append(f"{col} = ?")
            params.append(f[key])
    s, sp = auth.scope_alumnos("a")
    rows = q("SELECT a.*, c.nombre AS curso, d.nombre AS division, COALESCE(e.nombre, 'Sin especialidad') AS esp FROM alumnos a JOIN cursos c ON c.id = a.curso_id "
             "LEFT JOIN divisiones d ON d.id = a.division_id LEFT JOIN especialidades e ON e.id = a.especialidad_id "
             f"WHERE {' AND '.join(where)} AND {s} ORDER BY esp, c.orden, d.nombre, a.apellido, a.nombre", params + sp)
    cols = [("Especialidad", 2.2, "text"), ("Curso", 1, "text"), ("División", 0.9, "text"), ("Legajo", 1, "text"), ("Apellido y nombre", 3, "text"),
            ("DNI", 1.3, "text"), ("Estado", 1.2, "text")]
    conteo = {}
    for r in rows:
        conteo[r["esp"]] = conteo.get(r["esp"], 0) + 1
    return cols, [[r["esp"], r["curso"], r["division"], r["legajo"], f"{r['apellido']}, {r['nombre']}", r["dni"], r["estado_academico"]] for r in rows], \
        ["Alumnos por especialidad: " + "; ".join(f"{k}: {v}" for k, v in conteo.items())]


def r_asignados(f):
    rows = _ap(f)
    cols = [("Alumno", 3, "text"), ("DNI", 1.2, "text"), ("Curso", 1.2, "text"), ("Especialidad", 1.8, "text"), ("Empresa", 2.5, "text"),
            ("N° práctica", 1.3, "text"), ("Tutor", 2, "text"), ("Estado", 1.6, "text"), ("Inicio", 1, "date"), ("Fin estimado", 1, "date")]
    return cols, [[r["alumno"], r["dni"], f"{r['curso']} {r['division'] or ''}", r["especialidad"], r["empresa"], r["numero"], r["tutor"], r["estado"],
                   r["fecha_inicio"], r["fecha_fin_estimada"]] for r in rows], []


def _horas_cols():
    return [("Alumno", 3, "text"), ("Curso", 1.2, "text"), ("Empresa", 2.4, "text"), ("N° práctica", 1.3, "text"), ("Estado", 1.5, "text"),
            ("Horas req.", 0.9, "num"), ("Horas real.", 0.9, "num"), ("Cumpl.", 0.8, "pct"), ("Faltan", 0.8, "num")]


def _horas_rows(rows):
    return [[r["alumno"], f"{r['curso']} {r['division'] or ''}", r["empresa"], r["numero"], r["estado"], r["horas_requeridas"],
             r["horas_realizadas"], r["porcentaje"], r["horas_faltan"]] for r in rows]


def r_en_curso(f):
    f = dict(f, estado="En curso")
    return _horas_cols(), _horas_rows(_ap(f)), []


def r_finalizadas(f):
    rows = _ap(f, estados=("Finalizada", "Aprobada", "Desaprobada"))
    cols = [("Alumno", 3, "text"), ("Curso", 1.2, "text"), ("Empresa", 2.4, "text"), ("N° práctica", 1.3, "text"), ("Estado", 1.4, "text"),
            ("Horas", 0.9, "num"), ("Finalizó", 1, "date"), ("Nota final", 0.9, "num"), ("Resultado", 1.2, "text")]
    return cols, [[r["alumno"], f"{r['curso']} {r['division'] or ''}", r["empresa"], r["numero"], r["estado"], r["horas_realizadas"], r["ap_fin"],
                   r["calificacion_final"], r["resultado"]] for r in rows], []


def r_horas(f):
    rows = _ap(f)
    pend = sum(1 for r in rows if r["horas_faltan"] > 0)
    return _horas_cols(), _horas_rows(rows), [f"Alumnos con horas pendientes: {pend}"]


def r_asistencia(f):
    where, params = ["s.eliminado_en IS NULL", "s.tipo <> 'Presente'"], []
    for key, col in (("ciclo_id", "p.ciclo_id"), ("especialidad_id", "a.especialidad_id"), ("curso_id", "a.curso_id"), ("division_id", "a.division_id"),
                     ("empresa_id", "p.empresa_id"), ("tutor_id", "p.tutor_id")):
        if f.get(key):
            where.append(f"{col} = ?")
            params.append(f[key])
    if f.get("desde"):
        where.append("s.fecha >= ?")
        params.append(f["desde"])
    if f.get("hasta"):
        where.append("s.fecha <= ?")
        params.append(f["hasta"])
    sc, sp = auth.scope_ap("ap", "p")
    rows = q("SELECT s.*, a.apellido || ', ' || a.nombre AS alumno, p.numero, em.razon_social AS empresa FROM asistencias s "
             "JOIN alumnos_practicas ap ON ap.id = s.alumno_practica_id JOIN practicas p ON p.id = ap.practica_id "
             "JOIN alumnos a ON a.id = ap.alumno_id JOIN empresas em ON em.id = p.empresa_id "
             f"WHERE {' AND '.join(where)} AND {sc} ORDER BY s.fecha DESC, a.apellido LIMIT 5000", params + sp)
    cols = [("Fecha", 1, "date"), ("Alumno", 3, "text"), ("N° práctica", 1.3, "text"), ("Empresa", 2.4, "text"), ("Tipo", 1.8, "text"),
            ("Horas descontadas", 1.1, "num"), ("Motivo / observaciones", 2.6, "text")]
    tot = sum(r["horas"] for r in rows)
    return cols, [[r["fecha"], r["alumno"], r["numero"], r["empresa"], r["tipo"], r["horas"], r["observaciones"]] for r in rows], \
        [f"Inasistencias del período: {len(rows)}", f"Total de horas descontadas: {tot:g}"]


def r_doc_pendiente(f):
    rows = [r for r in _ap(f) if r["estado"] not in ("Aprobada", "Desaprobada") and r["docs_estado"] != "Completa"]
    cols = [("Alumno", 3, "text"), ("Curso", 1.2, "text"), ("Empresa", 2.4, "text"), ("N° práctica", 1.3, "text"), ("Estado", 1.5, "text"),
            ("Documentación", 1.5, "text"), ("Documentos faltantes", 3.5, "text")]
    return cols, [[r["alumno"], f"{r['curso']} {r['division'] or ''}", r["empresa"], r["numero"], r["estado"], r["docs_estado"],
                   ", ".join(r["docs_faltan"]) or "Pendiente de revisión"] for r in rows], []


def r_evaluaciones(f):
    rows = _ap(f, con_docs=False)
    evs = {}
    for e in q("SELECT alumno_practica_id AS ap, evaluador, promedio FROM evaluaciones WHERE eliminado_en IS NULL"):
        evs.setdefault(e["ap"], {})[e["evaluador"]] = e["promedio"]
    cols = [("Alumno", 3, "text"), ("Curso", 1.2, "text"), ("Empresa", 2.4, "text"), ("N° práctica", 1.3, "text"), ("Horas", 0.9, "num"),
            ("Eval. tutor", 1, "num"), ("Eval. referente", 1.1, "num"), ("Nota final", 0.9, "num"), ("Resultado", 1.2, "text")]
    out = [[r["alumno"], f"{r['curso']} {r['division'] or ''}", r["empresa"], r["numero"], r["horas_realizadas"], evs.get(r["ap_id"], {}).get("Tutor"),
            evs.get(r["ap_id"], {}).get("Referente"), r["calificacion_final"], r["resultado"] or "Sin resultado"] for r in rows]
    apr = sum(1 for r in rows if r["resultado"] == "Aprobado")
    des = sum(1 for r in rows if r["resultado"] == "Desaprobado")
    return cols, out, [f"Aprobados: {apr} · Desaprobados: {des} · Sin resultado: {len(rows) - apr - des}"]


def r_empresas(f):
    ocup = services.ocupacion_empresas()
    where, params = ["e.eliminado_en IS NULL"], []
    if f.get("empresa_id"):
        where.append("e.id = ?")
        params.append(f["empresa_id"])
    ciclo_sql, cp = ("AND p.ciclo_id = ?", [f["ciclo_id"]]) if f.get("ciclo_id") else ("", [])
    rows = q(f"""SELECT e.*, (SELECT r.nombre FROM referentes_empresas r WHERE r.empresa_id = e.id AND r.activo = 1 AND r.eliminado_en IS NULL ORDER BY r.id LIMIT 1) AS referente,
                 (SELECT COUNT(*) FROM alumnos_practicas ap JOIN practicas p ON p.id = ap.practica_id WHERE p.empresa_id = e.id AND ap.eliminado_en IS NULL AND p.eliminado_en IS NULL {ciclo_sql}) AS alumnos,
                 (SELECT COUNT(*) FROM practicas p WHERE p.empresa_id = e.id AND p.eliminado_en IS NULL {ciclo_sql}) AS practicas
                 FROM empresas e WHERE {' AND '.join(where)} ORDER BY e.razon_social""", cp + cp + params)
    if f.get("ciclo_id"):
        rows = [r for r in rows if r["practicas"]]
    cols = [("Razón social", 3, "text"), ("CUIT", 1.4, "text"), ("Localidad", 1.4, "text"), ("Teléfono", 1.3, "text"), ("Referente", 2, "text"),
            ("Vacantes", 0.8, "num"), ("Ocupadas", 0.8, "num"), ("Disponibles", 0.9, "num"), ("Prácticas", 0.8, "num"), ("Alumnos", 0.8, "num"), ("Estado", 0.9, "text")]
    return cols, [[r["razon_social"], r["cuit"], r["localidad"], r["telefono"], r["referente"], r["vacantes"], ocup.get(r["id"], 0),
                   max(0, r["vacantes"] - ocup.get(r["id"], 0)), r["practicas"], r["alumnos"], "Activa" if r["activo"] else "Inactiva"] for r in rows], []


def r_estadisticas(f):
    ciclos = [c for c in services.lista_ciclos() if not f.get("ciclo_id") or str(c["id"]) == str(f["ciclo_id"])]
    out = []
    for c in sorted(ciclos, key=lambda x: x["anio"]):
        ff = dict(f, ciclo_id=c["id"])
        aps = services.ap_rows(ff, con_docs=False)
        deben = q1("SELECT COUNT(*) AS n FROM alumnos a JOIN cursos cu ON cu.id = a.curso_id WHERE a.eliminado_en IS NULL AND cu.requiere_pp = 1 "
                   "AND a.estado_academico IN ('Regular','Repitente') AND a.ciclo_id = ?" + (" AND a.especialidad_id = ?" if f.get("especialidad_id") else ""),
                   [c["id"]] + ([f["especialidad_id"]] if f.get("especialidad_id") else []))["n"]
        hs, req = sum(r["horas_realizadas"] for r in aps), sum(r["horas_requeridas"] for r in aps)
        out.append([c["anio"], deben, len({r["alumno_id"] for r in aps}), len({r["empresa_id"] for r in aps}), sum(r["estado"] == "En curso" for r in aps),
                    sum(r["estado"] in ("Finalizada", "Aprobada", "Desaprobada") for r in aps), sum(r["estado"] == "Aprobada" for r in aps),
                    sum(r["estado"] == "Desaprobada" for r in aps), hs, round(hs * 100 / req, 1) if req else 0])
    cols = [("Año", 0.8, "text"), ("Alumnos que deben PP", 1.3, "num"), ("Alumnos con práctica", 1.3, "num"), ("Empresas participantes", 1.3, "num"),
            ("En curso", 0.9, "num"), ("Finalizadas", 1, "num"), ("Aprobadas", 1, "num"), ("Desaprobadas", 1, "num"), ("Horas realizadas", 1.1, "num"), ("Cumplimiento", 1.1, "pct")]
    return cols, out, []


F_ALUM = ["ciclo_id", "especialidad_id", "curso_id", "division_id"]
F_AP = ["ciclo_id", "especialidad_id", "curso_id", "division_id", "empresa_id", "tutor_id", "estado"]
REPORTES = {
    "alumnos": ("Listado general de alumnos", "Todos los alumnos con sus datos académicos.", r_alumnos, F_ALUM, False),
    "alumnos_especialidad": ("Alumnos por especialidad", "Alumnos agrupados por especialidad, curso y división.", r_alumnos_especialidad, F_ALUM, False),
    "asignados": ("Alumnos asignados a empresas", "Qué alumno realiza su práctica en cada empresa, con tutor y fechas.", r_asignados, F_AP, True),
    "en_curso": ("Prácticas en curso", "Prácticas actualmente en ejecución con su avance de horas.", r_en_curso, F_AP[:-1], True),
    "finalizadas": ("Prácticas finalizadas", "Prácticas finalizadas, aprobadas o desaprobadas.", r_finalizadas, F_AP[:-1], True),
    "horas_alumno": ("Horas realizadas por alumno", "Horas requeridas, realizadas y pendientes de cada alumno.", r_horas, F_AP, True),
    "asistencia": ("Inasistencias", "Detalle de inasistencias por fecha y horas descontadas (la asistencia se calcula automáticamente).", r_asistencia, F_AP[:-1] + ["desde", "hasta"], True),
    "doc_pendiente": ("Documentación pendiente", "Alumnos con documentación incompleta o sin revisar y qué documentos faltan.", r_doc_pendiente, F_AP, True),
    "evaluaciones": ("Evaluaciones finales", "Evaluaciones del tutor y del referente, nota final y resultado.", r_evaluaciones, F_AP, True),
    "empresas": ("Empresas participantes", "Empresas con sus vacantes, ocupación y alumnos recibidos.", r_empresas, ["ciclo_id", "empresa_id"], False),
    "estadisticas": ("Estadísticas anuales", "Resumen comparativo por ciclo lectivo.", r_estadisticas, ["ciclo_id", "especialidad_id"], True),
}


def _filtros_actuales(permitidos):
    f = {}
    ciclo = services.ciclo_activo()
    for k in permitidos:
        v = request.args.get(k, "")
        if k == "ciclo_id" and "ciclo_id" not in request.args and ciclo:
            v = str(ciclo["id"]) if request.endpoint == "informes.ver" and not request.args else ""
        f[k] = v
    return f


def _texto_filtros(f):
    nombres = {
        "ciclo_id": lambda v: next((str(c["anio"]) for c in services.lista_ciclos() if str(c["id"]) == v), v),
        "especialidad_id": lambda v: next((e["nombre"] for e in services.lista_especialidades() if str(e["id"]) == v), v),
        "curso_id": lambda v: next((c["nombre"] for c in services.lista_cursos() if str(c["id"]) == v), v),
        "division_id": lambda v: next((d["nombre"] for d in services.lista_divisiones() if str(d["id"]) == v), v),
        "empresa_id": lambda v: next((e["nombre"] for e in services.lista_empresas(False) if str(e["id"]) == v), v),
        "tutor_id": lambda v: next((d["nombre"] for d in services.lista_docentes(False) if str(d["id"]) == v), v),
    }
    out = []
    for k, v in f.items():
        if v:
            out.append((FILTRO_LABELS[k], nombres[k](str(v)) if k in nombres else (plain(v, "date") if k in ("desde", "hasta") else v)))
    return out


def _get(clave):
    if clave not in REPORTES:
        abort(404)
    titulo, desc, fn, permitidos, _ap_flag = REPORTES[clave]
    f = _filtros_actuales(permitidos)
    cols, rows, extra = fn(f)
    return titulo, desc, permitidos, f, cols, rows, extra


@bp.route("/")
@perm_required("informes.ver")
def inicio():
    return render_template("informes/inicio.html", reportes=[(k, v[0], v[1]) for k, v in REPORTES.items()])


@bp.route("/<clave>")
@perm_required("informes.ver")
def ver(clave):
    titulo, desc, permitidos, f, cols, rows, extra = _get(clave)
    shown = [[plain(v, c[2]) for v, c in zip(r, cols)] for r in rows[:500]]
    return render_template("informes/ver.html", clave=clave, titulo=titulo, desc=desc, permitidos=permitidos, f=f, cols=cols, rows=shown,
                           total=len(rows), extra=extra, truncado=len(rows) > 500, ciclos=services.lista_ciclos(),
                           especialidades=services.lista_especialidades(), cursos=services.lista_cursos(), divisiones=services.lista_divisiones(),
                           empresas=services.lista_empresas(False), docentes=services.lista_docentes(False), estados=services.ESTADOS_PP,
                           etiquetas=FILTRO_LABELS)


@bp.route("/<clave>.pdf")
@perm_required("informes.ver")
def pdf(clave):
    titulo, _d, _p, f, cols, rows, extra = _get(clave)
    data = pdfs.reporte_tabla(titulo, [(c[0], c[1]) for c in cols], [[plain(v, c[2]) for v, c in zip(r, cols)] for r in rows],
                              _texto_filtros(f), apaisado=len(cols) > 6, resumen=extra)
    audit("informes", None, "exportar", {"informe": clave, "formato": "pdf", "filas": len(rows)})
    commit()
    return Response(data, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{clave}.pdf"'})


@bp.route("/<clave>.xlsx")
@perm_required("informes.ver")
def excel(clave):
    titulo, _d, _p, f, cols, rows, extra = _get(clave)
    cfg = all_config()
    wb = Workbook()
    ws = wb.active
    ws.title = titulo[:30]
    ws["A1"] = cfg.get("escuela_nombre") or "Escuela Técnica"
    ws["A1"].font = Font(bold=True, size=14, color="1E3A5F")
    ws["A2"] = titulo
    ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = "Fecha de emisión: " + datetime.now().strftime("%d/%m/%Y %H:%M") + " · Emitido por: " + g.user["nombre"]
    filtros = _texto_filtros(f)
    ws["A4"] = "Filtros aplicados: " + (" · ".join(f"{k}: {v}" for k, v in filtros) if filtros else "ninguno")
    head_row = 6
    for i, c in enumerate(cols, 1):
        cell = ws.cell(head_row, i, c[0])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E3A5F")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append([v if (c[2] in ("num",) and isinstance(v, (int, float))) else plain(v, c[2]) for v, c in zip(r, cols)])
    for i, c in enumerate(cols, 1):
        longest = max([len(c[0])] + [len(plain(r[i - 1], c[2])) for r in rows[:300]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, longest + 2), 55)
    ws.freeze_panes = ws.cell(head_row + 1, 1)
    ws.auto_filter.ref = f"A{head_row}:{get_column_letter(len(cols))}{head_row + max(1, len(rows))}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{head_row}:{head_row}"
    ws.cell(head_row + len(rows) + 2, 1, f"Total de registros: {len(rows)}").font = Font(bold=True)
    for i, line in enumerate(extra):
        ws.cell(head_row + len(rows) + 3 + i, 1, line)
    buf = io.BytesIO()
    wb.save(buf)
    audit("informes", None, "exportar", {"informe": clave, "formato": "xlsx", "filas": len(rows)})
    commit()
    return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{clave}.xlsx"'})


def blueprints():
    return [bp]
