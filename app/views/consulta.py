"""Consulta pública (sin usuario ni contraseña) de Seguimiento y asistencia.

Es de SÓLO LECTURA:
- la ruta acepta únicamente GET (cualquier POST/PUT/DELETE responde 405);
- no crea sesión de usuario, por lo que el resto del sistema sigue exigiendo ingreso;
- no muestra datos personales (DNI, legajo, contacto, fichas) ni enlaces a otras pantallas;
- se puede deshabilitar desde Configuración (clave «consulta_publica»).
"""
from flask import Blueprint, abort, render_template, request

from .. import services
from ..db import get_config, norm, q

bp = Blueprint("consulta", __name__, url_prefix="/consulta")

# Únicos campos que se envían a la plantilla: nada de DNI, legajo, domicilio, teléfono, etc.
CAMPOS_PUBLICOS = ("alumno", "curso", "division", "especialidad", "numero", "anio", "empresa", "tutor",
                   "fecha_desde", "fecha_hasta", "fecha_hasta_estimada", "estado",
                   "horas_realizadas", "horas_requeridas", "porcentaje", "sin_alumnos")


def habilitada():
    return (get_config("consulta_publica", "1") or "1") == "1"


@bp.route("/", methods=["GET"])
def inicio():
    if not habilitada():
        abort(404)
    args = request.args
    ciclo = services.ciclo_activo()
    filtros = {
        "ciclo_id": args.get("ciclo_id", ciclo["id"] if ciclo and "ciclo_id" not in args else ""),
        "especialidad_id": args.get("especialidad_id"), "curso_id": args.get("curso_id"),
        "division_id": args.get("division_id"), "empresa_id": args.get("empresa_id"), "estado": args.get("estado"),
    }
    datos = services.ap_rows(filtros, order="a.apellido, a.nombre", scoped=False, con_docs=False)
    datos += practicas_sin_alumnos(filtros)
    if args.get("q"):  # búsqueda por nombre, empresa o N° de práctica (no por DNI)
        t = norm(args["q"])
        datos = [r for r in datos if t in norm(f"{r['alumno']} {r['empresa']} {r['numero']}")]
    rows = [{k: r.get(k) for k in CAMPOS_PUBLICOS} for r in datos]
    total_req = sum(r["horas_requeridas"] or 0 for r in rows)
    total_hs = sum(r["horas_realizadas"] for r in rows)
    return render_template("consulta/inicio.html", rows=rows, f=filtros, args=args,
                           ciclos=services.lista_ciclos(), especialidades=services.lista_especialidades(),
                           cursos=services.lista_cursos(), divisiones=services.lista_divisiones(),
                           empresas=services.lista_empresas(False), estados=services.ESTADOS_PP,
                           total_hs=total_hs, total_req=total_req,
                           cumplimiento=round(total_hs * 100 / total_req, 1) if total_req else 0)


def practicas_sin_alumnos(f):
    """Prácticas cargadas que todavía no tienen alumnos asignados (también deben verse en la consulta)."""
    if f.get("curso_id") or f.get("division_id"):  # estos filtros son del alumno: no aplican
        return []
    sql = """SELECT p.numero, p.fecha_inicio, p.fecha_fin_estimada, p.fecha_fin_real, p.estado, p.horas_requeridas,
                    em.razon_social AS empresa, ci.anio, e.nombre AS especialidad,
                    doc.apellido || ', ' || doc.nombre AS tutor
             FROM practicas p
             JOIN empresas em ON em.id = p.empresa_id
             JOIN ciclos_lectivos ci ON ci.id = p.ciclo_id
             LEFT JOIN especialidades e ON e.id = p.especialidad_id
             LEFT JOIN docentes doc ON doc.id = p.tutor_id
             WHERE p.eliminado_en IS NULL
               AND NOT EXISTS (SELECT 1 FROM alumnos_practicas ap WHERE ap.practica_id = p.id AND ap.eliminado_en IS NULL)"""
    params = []
    for clave, col in (("ciclo_id", "p.ciclo_id"), ("especialidad_id", "p.especialidad_id"),
                       ("empresa_id", "p.empresa_id"), ("estado", "p.estado")):
        if f.get(clave):
            sql += f" AND {col} = ?"
            params.append(f[clave])
    filas = []
    for r in q(sql + " ORDER BY p.numero", params):
        filas.append({
            "alumno": "", "curso": "", "division": "", "especialidad": r["especialidad"], "numero": r["numero"],
            "anio": r["anio"], "empresa": r["empresa"], "tutor": r["tutor"], "estado": r["estado"],
            "fecha_desde": r["fecha_inicio"], "fecha_hasta": r["fecha_fin_real"] or r["fecha_fin_estimada"],
            "fecha_hasta_estimada": not r["fecha_fin_real"] and bool(r["fecha_fin_estimada"]),
            "horas_realizadas": 0.0, "horas_requeridas": 0, "porcentaje": 0, "sin_alumnos": True})
    return filas


def blueprints():
    return [bp]
