"""Datos base imprescindibles (roles, permisos, parámetros, catálogos por defecto)."""
from datetime import date

MODULOS = {
    "alumnos": "Alumnos",
    "empresas": "Empresas e instituciones",
    "docentes": "Docentes y tutores",
    "practicas": "Prácticas profesionalizantes",
    "seguimiento": "Seguimiento y asistencia",
    "documentos": "Documentación",
    "evaluaciones": "Evaluaciones",
    "informes": "Informes y estadísticas",
    "catalogos": "Catálogos (cursos, divisiones, especialidades, tipos de documento)",
    "usuarios": "Usuarios",
    "configuracion": "Configuración, copias de seguridad e historial",
}
ACCIONES = {"ver": "Consultar", "editar": "Crear y modificar", "eliminar": "Dar de baja"}

ROLES = [
    ("admin", "Administrador", "Acceso completo, gestión de usuarios y configuración general."),
    ("coordinador", "Coordinador de Prácticas Profesionalizantes",
     "Gestiona alumnos, empresas, prácticas, documentación, seguimiento e informes."),
    ("tutor", "Docente tutor", "Consulta y evalúa a sus alumnos asignados; registra visitas e inasistencias."),
    ("consulta", "Consulta institucional", "Acceso de sólo lectura a la información autorizada."),
]

_VER = ["alumnos", "empresas", "docentes", "practicas", "seguimiento", "documentos", "evaluaciones", "informes", "catalogos"]
ROL_PERMISOS = {
    "admin": "*",
    "coordinador": (
        [f"{m}.ver" for m in _VER]
        + ["alumnos.editar", "empresas.editar", "docentes.editar", "practicas.editar", "seguimiento.editar",
           "seguimiento.eliminar", "documentos.editar", "documentos.eliminar", "evaluaciones.editar", "catalogos.editar"]
    ),
    "tutor": ["alumnos.ver", "practicas.ver", "seguimiento.ver", "seguimiento.editar", "documentos.ver",
              "evaluaciones.ver", "evaluaciones.editar", "informes.ver", "empresas.ver"],
    "consulta": [f"{m}.ver" for m in _VER if m != "catalogos"],
}

CONFIG_DEFAULT = {
    "escuela_nombre": "Escuela de Educación Técnica N° 1",
    "escuela_cue": "",
    "escuela_direccion": "",
    "escuela_localidad": "",
    "escuela_telefono": "",
    "escuela_email": "",
    "escuela_direccion_general": "",
    "nota_aprobacion": "6",
    "dias_alerta_vencimiento": "15",
    "umbral_ritmo": "25",
}

TIPOS_DOCUMENTOS = [
    ("Convenio con la empresa", "empresa", 1),
    ("Plan de prácticas", "practica", 1),
    ("Acuerdo individual", "practica", 1),
    ("Autorización de padres o tutores", "alumno", 1),
    ("Constancia de alumno regular", "alumno", 1),
    ("Seguro / cobertura", "practica", 1),
    ("Planilla de asistencia", "practica", 0),
    ("Informe de seguimiento", "practica", 0),
    ("Evaluación", "practica", 0),
    ("Certificado de finalización", "practica", 0),
]


def init_base(con, ciclo_anio=None):
    """Inserta datos base. `con` es una conexión sqlite3 abierta."""
    cur = con.cursor()
    for cod, nombre, desc in ROLES:
        cur.execute("INSERT OR IGNORE INTO roles(codigo, nombre, descripcion) VALUES (?,?,?)", (cod, nombre, desc))
    for m, nombre_m in MODULOS.items():
        for a, nombre_a in ACCIONES.items():
            if m == "informes" and a != "ver":
                continue
            cur.execute("INSERT OR IGNORE INTO permisos(codigo, modulo, descripcion) VALUES (?,?,?)",
                        (f"{m}.{a}", m, f"{nombre_a}: {nombre_m}"))
    cur.execute("INSERT OR IGNORE INTO permisos(codigo, modulo, descripcion) VALUES (?,?,?)",
                ("configuracion.respaldo", "configuracion", "Crear, descargar y restaurar copias de seguridad"))
    cur.execute("INSERT OR IGNORE INTO permisos(codigo, modulo, descripcion) VALUES (?,?,?)",
                ("configuracion.historial", "configuracion", "Ver el historial de operaciones del sistema"))
    permisos = {r[0]: r[1] for r in cur.execute("SELECT codigo, id FROM permisos")}
    roles = {r[0]: r[1] for r in cur.execute("SELECT codigo, id FROM roles")}
    for rol, lista in ROL_PERMISOS.items():
        codigos = permisos.keys() if lista == "*" else lista
        for c in codigos:
            cur.execute("INSERT OR IGNORE INTO rol_permisos(rol_id, permiso_id) VALUES (?,?)", (roles[rol], permisos[c]))
    for k, v in CONFIG_DEFAULT.items():
        cur.execute("INSERT OR IGNORE INTO configuracion(clave, valor) VALUES (?,?)", (k, v))
    for nombre, ambito, obl in TIPOS_DOCUMENTOS:
        if not cur.execute("SELECT 1 FROM tipos_documentos WHERE lower(nombre) = lower(?)", (nombre,)).fetchone():
            cur.execute("INSERT INTO tipos_documentos(nombre, ambito, obligatorio) VALUES (?,?,?)", (nombre, ambito, obl))
    for i in range(1, 8):
        if not cur.execute("SELECT 1 FROM cursos WHERE nombre = ?", (f"{i}° año",)).fetchone():
            cur.execute("INSERT INTO cursos(nombre, orden, requiere_pp) VALUES (?,?,?)", (f"{i}° año", i, 1 if i >= 6 else 0))
    for nombre in ("1ª", "2ª", "3ª", "4ª"):
        if not cur.execute("SELECT 1 FROM divisiones WHERE nombre = ?", (nombre,)).fetchone():
            cur.execute("INSERT INTO divisiones(nombre) VALUES (?)", (nombre,))
    anio = ciclo_anio or date.today().year
    if not cur.execute("SELECT 1 FROM ciclos_lectivos").fetchone():
        cur.execute("INSERT INTO ciclos_lectivos(anio, nombre, fecha_inicio, fecha_fin, activo) VALUES (?,?,?,?,1)",
                    (anio, f"Ciclo lectivo {anio}", f"{anio}-03-01", f"{anio}-12-15"))
    con.commit()
