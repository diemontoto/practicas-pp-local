"""Catálogos institucionales: especialidades, cursos, divisiones, tipos de documento y ciclos lectivos."""
from ..crud import C, Entity, F
from ..db import ex, q

DEPS_ESP = (
    ("SELECT COUNT(*) FROM alumnos WHERE especialidad_id = ? AND eliminado_en IS NULL", "alumno(s)"),
    ("SELECT COUNT(*) FROM docentes WHERE especialidad_id = ? AND eliminado_en IS NULL", "docente(s)"),
)

especialidades = Entity(
    "especialidades", "especialidades", "Especialidad", "Especialidades técnicas", "catalogos",
    fields=[
        F("codigo", "Código", maxlen=20),
        F("nombre", "Nombre", required=True, unique=True),
        F("horas_pp", "Horas de PP requeridas", "number", required=True, default=200, min=1, max=2000,
          help="Valor sugerido al crear una práctica de esta especialidad."),
        F("activo", "Activa", "checkbox", default=1),
    ],
    columns=[C("codigo", "Código"), C("nombre", "Especialidad"), C("horas_pp", "Horas PP"), C("activo", "Activa", "bool")],
    search=("t.nombre", "t.codigo"), order="t.nombre", deps=DEPS_ESP, nuevo_label="Nueva especialidad",
)

cursos = Entity(
    "cursos", "cursos", "Curso", "Cursos", "catalogos",
    fields=[
        F("nombre", "Nombre", required=True, unique=True, placeholder="6° año"),
        F("orden", "Orden", "number", required=True, default=1, min=0, max=20),
        F("requiere_pp", "Los alumnos de este curso deben realizar PP", "checkbox", cols=2),
        F("activo", "Activo", "checkbox", default=1),
    ],
    columns=[C("nombre", "Curso"), C("orden", "Orden"), C("requiere_pp", "Requiere PP", "bool"), C("activo", "Activo", "bool")],
    search=("t.nombre",), order="t.orden, t.nombre",
    deps=(("SELECT COUNT(*) FROM alumnos WHERE curso_id = ? AND eliminado_en IS NULL", "alumno(s)"),),
)

divisiones = Entity(
    "divisiones", "divisiones", "División", "Divisiones", "catalogos",
    fields=[F("nombre", "Nombre", required=True, unique=True, placeholder="1ª"), F("activo", "Activa", "checkbox", default=1)],
    columns=[C("nombre", "División"), C("activo", "Activa", "bool")],
    search=("t.nombre",), order="t.nombre", nuevo_label="Nueva división",
    deps=(("SELECT COUNT(*) FROM alumnos WHERE division_id = ? AND eliminado_en IS NULL", "alumno(s)"),),
)

tipos_documentos = Entity(
    "tipos_documentos", "tipos_documentos", "Tipo de documento", "Tipos de documento", "catalogos",
    fields=[
        F("nombre", "Nombre", required=True, unique=True),
        F("ambito", "Corresponde a", "select", required=True, choices=[("alumno", "Alumno"), ("practica", "Práctica"), ("empresa", "Empresa")],
          help="Define a qué registro se vincula el documento."),
        F("obligatorio", "Obligatorio para habilitar la práctica", "checkbox", cols=2),
        F("activo", "Activo", "checkbox", default=1),
    ],
    columns=[C("nombre", "Tipo de documento"), C("ambito", "Ámbito"), C("obligatorio", "Obligatorio", "bool"), C("activo", "Activo", "bool")],
    search=("t.nombre",), order="t.nombre",
    deps=(("SELECT COUNT(*) FROM documentos WHERE tipo_documento_id = ? AND eliminado_en IS NULL", "documento(s)"),),
)


def _ciclo_after(id, data, es_nuevo, row):
    if data.get("activo"):
        ex("UPDATE ciclos_lectivos SET activo = 0 WHERE id <> ?", (id,), commit=False)


def _ciclo_before(vals, row, errors):
    if vals.get("fecha_inicio") and vals.get("fecha_fin") and vals["fecha_fin"] < vals["fecha_inicio"]:
        errors["fecha_fin"] = "La fecha de fin no puede ser anterior al inicio."


ciclos = Entity(
    "ciclos", "ciclos_lectivos", "Ciclo lectivo", "Ciclos lectivos", "configuracion",
    fields=[
        F("anio", "Año", "number", required=True, unique=True, min=2000, max=2100),
        F("nombre", "Nombre", required=True, placeholder="Ciclo lectivo 2026"),
        F("fecha_inicio", "Inicio", "date"),
        F("fecha_fin", "Fin", "date"),
        F("horas_pp_default", "Horas de PP por defecto", "number", required=True, default=200, min=1, max=2000),
        F("activo", "Ciclo activo (se usa por defecto en filtros y altas)", "checkbox"),
        F("observaciones", "Observaciones", "textarea", cols=2),
    ],
    columns=[C("anio", "Año"), C("nombre", "Nombre"), C("fecha_inicio", "Inicio", "date"), C("fecha_fin", "Fin", "date"),
             C("horas_pp_default", "Horas PP"), C("activo", "Activo", "bool")],
    search=("t.nombre",), order="t.anio DESC", before_save=_ciclo_before, after_save=_ciclo_after, nuevo_label="Nuevo ciclo lectivo",
    deps=(("SELECT COUNT(*) FROM practicas WHERE ciclo_id = ? AND eliminado_en IS NULL", "práctica(s)"),),
)


def _dsa_before(vals, row, errors):
    if vals.get("fecha_desde") and "fecha_desde" not in errors:
        if not vals.get("fecha_hasta"):
            vals["fecha_hasta"] = vals["fecha_desde"]          # un solo día
        elif "fecha_hasta" not in errors and vals["fecha_hasta"] < vals["fecha_desde"]:
            errors["fecha_hasta"] = "No puede ser anterior a la fecha inicial."


def _practicas_choices():
    return [(p["id"], f"{p['numero']} · {p['empresa']}") for p in q(
        "SELECT p.id, p.numero, em.razon_social AS empresa FROM practicas p JOIN empresas em ON em.id = p.empresa_id "
        "WHERE p.eliminado_en IS NULL ORDER BY p.numero DESC")]


dias_sin_actividad = Entity(
    "dias_sin_actividad", "dias_sin_actividad", "Día sin actividad", "Feriados y recesos", "catalogos",
    fields=[
        F("fecha_desde", "Desde", "date", required=True),
        F("fecha_hasta", "Hasta", "date", help="Déjelo vacío si es un solo día."),
        F("motivo", "Motivo", required=True, maxlen=150, placeholder="Feriado nacional, receso invernal, cierre de la empresa…"),
        F("practica_id", "Aplica sólo a la práctica", "select", coerce=int, choices=_practicas_choices,
          help="Sin práctica elegida aplica a todas (feriado o receso general)."),
    ],
    columns=[C("fecha_desde", "Desde", "date"), C("fecha_hasta", "Hasta", "date"), C("motivo", "Motivo"), C("practica", "Práctica")],
    select="SELECT t.*, COALESCE(p.numero, 'Todas') AS practica FROM dias_sin_actividad t LEFT JOIN practicas p ON p.id = t.practica_id",
    search=("t.motivo",), order="t.fecha_desde DESC", before_save=_dsa_before, nuevo_label="Nuevo día sin actividad",
    scope=lambda: ("t.alumno_practica_id IS NULL", []),
    subtitle="En estas fechas no corren las horas automáticas de las prácticas: no suman horas ni admiten inasistencias.",
)


def blueprints():
    return [e.blueprint(f"/configuracion/{e.slug}") for e in (especialidades, cursos, divisiones, tipos_documentos, ciclos, dias_sin_actividad)]
