"""Reglas de negocio y consultas compartidas entre módulos."""
from datetime import date, datetime, timedelta

from . import auth
from .db import get_config, q, q1, today, ex

ESTADOS_PP = [
    "Pendiente de asignación",
    "En proceso de asignación",
    "Documentación pendiente",
    "Habilitada para comenzar",
    "En curso",
    "Suspendida",
    "Finalizada",
    "Aprobada",
    "Desaprobada",
]
PRE_INICIO = ESTADOS_PP[:4]
OCUPAN_VACANTE = ("En proceso de asignación", "Documentación pendiente", "Habilitada para comenzar", "En curso", "Suspendida")
CERRADOS = ("Finalizada", "Aprobada", "Desaprobada")
REQUIERE_DOCS = ("Habilitada para comenzar", "En curso")


def choices(rows, value="id", label="nombre"):
    return [(r[value], r[label]) for r in rows]


def ciclo_activo():
    row = q1("SELECT * FROM ciclos_lectivos WHERE activo = 1 AND eliminado_en IS NULL ORDER BY anio DESC LIMIT 1")
    if not row:
        row = q1("SELECT * FROM ciclos_lectivos WHERE eliminado_en IS NULL ORDER BY anio DESC LIMIT 1")
    return row


def lista_ciclos():
    return q("SELECT id, anio, nombre, activo FROM ciclos_lectivos WHERE eliminado_en IS NULL ORDER BY anio DESC")


def lista_especialidades():
    return q("SELECT id, nombre, horas_pp FROM especialidades WHERE eliminado_en IS NULL ORDER BY nombre")


def lista_cursos():
    return q("SELECT id, nombre FROM cursos WHERE eliminado_en IS NULL ORDER BY orden, nombre")


def lista_divisiones():
    return q("SELECT id, nombre FROM divisiones WHERE eliminado_en IS NULL ORDER BY nombre")


def lista_docentes(solo_activos=True):
    w = "AND activo = 1" if solo_activos else ""
    return q(f"SELECT id, apellido || ', ' || nombre AS nombre FROM docentes WHERE eliminado_en IS NULL {w} ORDER BY apellido, nombre")


def lista_empresas(solo_activas=True):
    w = "AND activo = 1" if solo_activas else ""
    return q(f"SELECT id, razon_social AS nombre FROM empresas WHERE eliminado_en IS NULL {w} ORDER BY razon_social")


def siguiente_numero(ciclo_id):
    c = q1("SELECT anio FROM ciclos_lectivos WHERE id = ?", (ciclo_id,))
    anio = c["anio"] if c else date.today().year
    prefijo = f"PP-{anio}-"
    row = q1("SELECT numero FROM practicas WHERE numero LIKE ? ORDER BY numero DESC LIMIT 1", (prefijo + "%",))
    n = 1
    if row:
        try:
            n = int(row["numero"].split("-")[-1]) + 1
        except ValueError:
            n = 1
    return f"{prefijo}{n:04d}"


def asegurar_asignacion_tutor(docente_id, alumno_id, ciclo_id):
    if docente_id and alumno_id and ciclo_id:
        ex("INSERT OR IGNORE INTO asignaciones_tutor(docente_id, alumno_id, ciclo_id, creado_en, creado_por) "
           "VALUES (?,?,?, datetime('now','localtime'), ?)",
           (docente_id, alumno_id, ciclo_id, auth.g.user["id"] if getattr(auth.g, "user", None) else None), commit=False)


# ---------------------------------------------------------------- vacantes
def ocupacion_empresas():
    ph = ",".join("?" * len(OCUPAN_VACANTE))
    rows = q(f"SELECT p.empresa_id, COUNT(*) AS n FROM alumnos_practicas ap JOIN practicas p ON p.id = ap.practica_id "
             f"WHERE ap.estado IN ({ph}) AND ap.eliminado_en IS NULL AND p.eliminado_en IS NULL GROUP BY p.empresa_id",
             OCUPAN_VACANTE)
    return {r["empresa_id"]: r["n"] for r in rows}


def vacantes_disponibles_total():
    ocup = ocupacion_empresas()
    total = 0
    for e in q("SELECT id, vacantes FROM empresas WHERE activo = 1 AND eliminado_en IS NULL"):
        total += max(0, e["vacantes"] - ocup.get(e["id"], 0))
    return total


# ------------------------------------------------ asistencia automática
# Las horas de cada alumno corren solas: cada día de práctica (según los días de la semana y las horas
# diarias de la práctica) entre la fecha de inicio de la asignación y la de finalización suma la jornada.
# Sólo se cargan las inasistencias, que descuentan sus horas. No suman los feriados/recesos ni los
# períodos de suspensión (tabla dias_sin_actividad).
DIAS_SEMANA = [(1, "Lunes"), (2, "Martes"), (3, "Miércoles"), (4, "Jueves"), (5, "Viernes"), (6, "Sábado"), (7, "Domingo")]
CORREN_HORAS = ("En curso", "Suspendida", "Finalizada", "Aprobada", "Desaprobada")
TIPOS_INASISTENCIA = ["Ausente injustificado", "Ausente justificado"]


def parse_dias(txt):
    """'1,2,3' -> {1, 2, 3} (1 = lunes ... 7 = domingo). Vacío o inválido: lunes a viernes."""
    dias = {int(x) for x in str(txt or "").split(",") if x.strip().isdigit() and 1 <= int(x.strip()) <= 7}
    return dias or {1, 2, 3, 4, 5}


def dias_texto(txt):
    dias = sorted(parse_dias(txt))
    nombres = dict(DIAS_SEMANA)
    if len(dias) > 2 and dias == list(range(dias[0], dias[-1] + 1)):
        return f"{nombres[dias[0]]} a {nombres[dias[-1]].lower()}"
    return ", ".join(nombres[d] for d in dias).capitalize() if dias else ""


def _sin_actividad():
    """Períodos sin actividad: (generales, {practica_id: [...]}, {ap_id: [...]}) como pares (desde, hasta)."""
    gen, por_p, por_ap = [], {}, {}
    for r in q("SELECT fecha_desde, fecha_hasta, practica_id, alumno_practica_id FROM dias_sin_actividad WHERE eliminado_en IS NULL"):
        par = (r["fecha_desde"], r["fecha_hasta"] or "9999-12-31")
        if r["alumno_practica_id"]:
            por_ap.setdefault(r["alumno_practica_id"], []).append(par)
        elif r["practica_id"]:
            por_p.setdefault(r["practica_id"], []).append(par)
        else:
            gen.append(par)
    return gen, por_p, por_ap


def dias_de_practica(inicio, hasta, dias_semana, excluidos=()):
    """Fechas ISO de los días de práctica entre dos fechas (inclusive), sin los períodos excluidos."""
    ini, fin = _parse(inicio), _parse(hasta)
    if not ini or not fin or fin < ini:
        return []
    dias, out, d = parse_dias(dias_semana), [], ini
    if (fin - ini).days > 3660:          # tope defensivo ante fechas mal cargadas
        fin = ini + timedelta(days=3660)
    while d <= fin:
        if d.isoweekday() in dias:
            iso = d.isoformat()
            if not any(a <= iso <= b for a, b in excluidos):
                out.append(iso)
        d += timedelta(days=1)
    return out


def periodo_ap(r, hoy=None):
    """(inicio, hasta) del período en que corren las horas de una asignación; hasta nunca supera hoy."""
    hoy = hoy or today()
    ini = r["ap_inicio"] or r["fecha_inicio"]
    fin = r["ap_fin"] or r["p_fin_real"] or r["fecha_fin_estimada"]
    return ini, (min(fin, hoy) if fin else hoy)


def calcular_horas(rows):
    """Agrega a cada asignación las horas programadas a la fecha, las descontadas por inasistencias y las cumplidas."""
    if not rows:
        return
    gen, por_p, por_ap = _sin_actividad()
    ids = [r["ap_id"] for r in rows]
    sql = "SELECT alumno_practica_id, fecha, tipo, horas FROM asistencias WHERE eliminado_en IS NULL AND tipo <> 'Presente'"
    faltas = {}
    if len(ids) <= 200:
        aus = q(sql + f" AND alumno_practica_id IN ({','.join('?' * len(ids))})", ids)
    else:
        aus = q(sql)
    for a in aus:
        faltas.setdefault(a["alumno_practica_id"], []).append(a)
    hoy = today()
    for r in rows:
        jornada = float(r["horas_dia"] or 0)
        ini, hasta = periodo_ap(r, hoy)
        dias = []
        if r["estado"] in CORREN_HORAS and ini:
            dias = dias_de_practica(ini, hasta, r["dias_semana"],
                                    gen + por_p.get(r["practica_id"], []) + por_ap.get(r["ap_id"], []))
        prog = set(dias)
        desc, just, injust = 0.0, 0, 0
        for a in faltas.get(r["ap_id"], []):
            if a["fecha"] not in prog:
                continue                     # fuera del calendario: no hay horas que descontar
            desc += min(a["horas"] if a["horas"] and a["horas"] > 0 else jornada, jornada)
            if a["tipo"] == "Ausente justificado":
                just += 1
            else:
                injust += 1
        r["dias_prog"] = prog
        r["dias_programados"] = len(dias)
        r["horas_programadas"] = round(len(dias) * jornada, 2)
        r["horas_descontadas"] = round(desc, 2)
        r["horas_realizadas"] = max(0.0, round(len(dias) * jornada - desc, 2))
        r["faltas_just"], r["faltas_injust"] = just, injust
        r["dias_presente"] = len(dias) - just - injust
        r["periodo_desde"], r["periodo_hasta"] = ini, hasta


def horas_plan(p):
    """Horas que suma el calendario completo de una práctica (inicio a fin estimado); None si faltan fechas."""
    if not p["fecha_inicio"] or not p["fecha_fin_estimada"]:
        return None
    gen, por_p, _ = _sin_actividad()
    dias = dias_de_practica(p["fecha_inicio"], p["fecha_fin_estimada"], p["dias_semana"], gen + por_p.get(p["id"], []))
    return round(len(dias) * float(p["horas_dia"] or 0), 2)


def abrir_suspension(ap_id):
    """Al suspender a un alumno las horas dejan de correr desde hoy."""
    if not q1("SELECT 1 FROM dias_sin_actividad WHERE alumno_practica_id = ? AND fecha_hasta IS NULL AND eliminado_en IS NULL", (ap_id,)):
        ex("INSERT INTO dias_sin_actividad(fecha_desde, motivo, alumno_practica_id, creado_en, creado_por) VALUES (?,?,?, datetime('now','localtime'), ?)",
           (today(), "Suspensión", ap_id, auth.g.user["id"] if getattr(auth.g, "user", None) else None), commit=False)


def cerrar_suspension(ap_id):
    """Al levantar la suspensión las horas vuelven a correr desde hoy."""
    ayer = (date.today() - timedelta(days=1)).isoformat()
    for s in q("SELECT id, fecha_desde FROM dias_sin_actividad WHERE alumno_practica_id = ? AND fecha_hasta IS NULL AND eliminado_en IS NULL", (ap_id,)):
        if s["fecha_desde"] > ayer:      # suspendida y reanudada el mismo día: no hubo días perdidos
            ex("UPDATE dias_sin_actividad SET eliminado_en = datetime('now','localtime') WHERE id = ?", (s["id"],), commit=False)
        else:
            ex("UPDATE dias_sin_actividad SET fecha_hasta = ? WHERE id = ?", (ayer, s["id"]), commit=False)


# --------------------------------------------------------- horas y alumnos
AP_SQL = """
SELECT ap.id AS ap_id, ap.practica_id, ap.alumno_id, ap.estado, ap.horas_requeridas,
       ap.calificacion_final, ap.resultado, ap.fecha_inicio AS ap_inicio, ap.fecha_fin_real AS ap_fin,
       a.legajo, a.apellido, a.nombre, a.dni, a.curso_id, a.division_id, a.especialidad_id,
       a.apellido || ', ' || a.nombre AS alumno,
       c.nombre AS curso, d.nombre AS division, e.nombre AS especialidad,
       p.numero, p.ciclo_id, p.empresa_id, p.proyecto, p.fecha_inicio, p.fecha_fin_estimada,
       p.tutor_id, p.estado AS practica_estado, p.referente_id, p.dias_semana, p.horas_dia, p.fecha_fin_real AS p_fin_real,
       em.razon_social AS empresa, ci.anio, doc.apellido || ', ' || doc.nombre AS tutor
FROM alumnos_practicas ap
JOIN practicas p   ON p.id = ap.practica_id AND p.eliminado_en IS NULL
JOIN alumnos a     ON a.id = ap.alumno_id AND a.eliminado_en IS NULL
JOIN cursos c      ON c.id = a.curso_id
LEFT JOIN divisiones d ON d.id = a.division_id
LEFT JOIN especialidades e ON e.id = a.especialidad_id
JOIN empresas em   ON em.id = p.empresa_id
JOIN ciclos_lectivos ci ON ci.id = p.ciclo_id
LEFT JOIN docentes doc ON doc.id = p.tutor_id
WHERE ap.eliminado_en IS NULL
"""

FILTER_COLS = {
    "ciclo_id": "p.ciclo_id",
    "especialidad_id": "a.especialidad_id",
    "curso_id": "a.curso_id",
    "division_id": "a.division_id",
    "empresa_id": "p.empresa_id",
    "estado": "ap.estado",
    "tutor_id": "p.tutor_id",
    "alumno_id": "ap.alumno_id",
    "practica_id": "ap.practica_id",
    "ap_id": "ap.id",
}


def _clean(v):
    if v in (None, "", "0"):
        return None
    return int(v) if str(v).isdigit() else v


def ap_rows(filtros=None, order="a.apellido, a.nombre", scoped=True, con_docs=True):
    """Alumnos-prácticas enriquecidos con horas, porcentaje y estado de documentación."""
    where, params = [], []
    for key, val in (filtros or {}).items():
        val = _clean(val)
        if val is not None and key in FILTER_COLS:
            where.append(f"{FILTER_COLS[key]} = ?")
            params.append(val)
    if scoped:
        sql, sp = auth.scope_ap("ap", "p")
        where.append(sql)
        params += sp
    sql = AP_SQL + (" AND " + " AND ".join(where) if where else "") + f" ORDER BY {order}"
    rows = [dict(r) for r in q(sql, params)]
    calcular_horas(rows)
    docs = doc_status_map(rows) if con_docs else {}
    for r in rows:
        req = r["horas_requeridas"] or 1
        r["porcentaje"] = min(100.0, round(r["horas_realizadas"] * 100.0 / req, 1))
        r["horas_faltan"] = max(0.0, round(req - r["horas_realizadas"], 2))
        # Fechas de la práctica del alumno (las propias de la asignación prevalecen sobre las de la práctica)
        r["fecha_desde"] = r["ap_inicio"] or r["fecha_inicio"]
        r["fecha_hasta"] = r["ap_fin"] or r["p_fin_real"] or r["fecha_fin_estimada"]
        r["fecha_hasta_estimada"] = not (r["ap_fin"] or r["p_fin_real"]) and bool(r["fecha_fin_estimada"])
        if con_docs:
            st, faltan = docs.get(r["ap_id"], ("Completa", []))
            r["docs_estado"], r["docs_faltan"] = st, faltan
    return rows


def doc_detail_map(rows):
    """({tipos obligatorios}, {ap_id: {tipo_id: 'ok'|'pend'|'falta'|'venc'}}) según los documentos cargados.

    El ámbito del tipo decide a qué registro se vincula: alumno -> alumno_id; práctica -> practica_id
    (y, si tiene alumno_id, sólo vale para ese alumno); empresa -> empresa_id."""
    tipos = [dict(t) for t in q("SELECT id, nombre, ambito FROM tipos_documentos "
                                "WHERE obligatorio = 1 AND activo = 1 AND eliminado_en IS NULL ORDER BY nombre")]
    if not rows:
        return tipos, {}
    hoy = today()
    ambito = {t["id"]: t["ambito"] for t in tipos}
    idx = {}
    for d in q("SELECT tipo_documento_id AS t, alumno_id, empresa_id, practica_id, estado, vencimiento "
               "FROM documentos WHERE eliminado_en IS NULL"):
        amb = ambito.get(d["t"])
        if amb is None:
            continue
        venc = bool(d["vencimiento"] and d["vencimiento"] < hoy)
        if amb == "alumno" and d["alumno_id"]:
            key = (d["t"], "a", d["alumno_id"])
        elif amb == "empresa" and d["empresa_id"]:
            key = (d["t"], "e", d["empresa_id"])
        elif amb == "practica" and d["practica_id"]:
            key = (d["t"], "p", d["practica_id"], d["alumno_id"])
        else:
            continue
        idx.setdefault(key, []).append("venc" if venc else d["estado"])
    out = {}
    for r in rows:
        det = {}
        for t in tipos:
            if t["ambito"] == "alumno":
                estados = idx.get((t["id"], "a", r["alumno_id"]), [])
            elif t["ambito"] == "empresa":
                estados = idx.get((t["id"], "e", r["empresa_id"]), [])
            else:
                estados = idx.get((t["id"], "p", r["practica_id"], None), []) + idx.get((t["id"], "p", r["practica_id"], r["alumno_id"]), [])
            validos = [e for e in estados if e not in ("Observado", "venc")]
            if "Aprobado" in validos:
                det[t["id"]] = "ok"
            elif validos:
                det[t["id"]] = "pend"
            elif "venc" in estados:
                det[t["id"]] = "venc"
            else:
                det[t["id"]] = "falta"
        out[r["ap_id"]] = det
    return tipos, out


def doc_status_map(rows):
    """{ap_id: (estado, [documentos faltantes])} según los documentos obligatorios."""
    tipos, det = doc_detail_map(rows)
    nombres = {t["id"]: t["nombre"] for t in tipos}
    out = {}
    for ap_id, d in det.items():
        faltan = [nombres[t] + (" (vencido)" if s == "venc" else "") for t, s in d.items() if s in ("falta", "venc")]
        pend = any(s == "pend" for s in d.values())
        out[ap_id] = ("Incompleta" if faltan else ("Pendiente de revisión" if pend else "Completa"), faltan)
    return out


def calc_horas(entrada, salida):
    """Horas (decimal) entre dos horarios HH:MM. Devuelve None si es inválido."""
    try:
        h1, m1 = map(int, entrada.split(":"))
        h2, m2 = map(int, salida.split(":"))
    except (ValueError, AttributeError):
        return None
    mins = (h2 * 60 + m2) - (h1 * 60 + m1)
    if mins <= 0:
        return None
    return round(mins / 60.0, 2)


# ------------------------------------------------------------------ alertas
def alertas(filtros=None):
    """Alertas de documentación, vencimiento de prácticas y horas insuficientes."""
    dias = int(get_config("dias_alerta_vencimiento", 15))
    umbral = float(get_config("umbral_ritmo", 25))
    hoy = date.today()
    rows = ap_rows(filtros or {}, order="p.fecha_fin_estimada, a.apellido")
    res = {"docs": [], "vencimientos": [], "horas": [], "completas": []}
    for r in rows:
        est = r["estado"]
        if est in ("En proceso de asignación", "Documentación pendiente", "Habilitada para comenzar", "En curso") \
                and r["docs_estado"] != "Completa":
            r2 = dict(r)
            r2["motivo"] = ("Faltan: " + ", ".join(r["docs_faltan"])) if r["docs_faltan"] else "Documentos pendientes de revisión"
            res["docs"].append(r2)
        fin = _parse(r["fecha_fin_estimada"])
        ini = _parse(r["ap_inicio"]) or _parse(r["fecha_inicio"])
        if est in ("Habilitada para comenzar", "En curso", "Suspendida") and fin:
            left = (fin - hoy).days
            if left <= dias:
                r2 = dict(r)
                r2["dias_restantes"] = left
                r2["motivo"] = f"Venció hace {-left} día(s)" if left < 0 else f"Vence en {left} día(s)"
                res["vencimientos"].append(r2)
        if est == "En curso":
            if r["horas_realizadas"] >= r["horas_requeridas"]:
                r2 = dict(r)
                r2["motivo"] = "Completó las horas requeridas: corresponde finalizar la práctica"
                res["completas"].append(r2)
            elif fin and fin < hoy:
                r2 = dict(r)
                r2["motivo"] = f"Fecha de fin vencida: faltan {r['horas_faltan']:.0f} h"
                res["horas"].append(r2)
            elif fin and ini and fin > ini:
                transcurrido = (hoy - ini).days / max(1, (fin - ini).days) * 100
                if transcurrido > 30 and transcurrido - r["porcentaje"] >= umbral:
                    r2 = dict(r)
                    r2["motivo"] = f"Ritmo insuficiente: {transcurrido:.0f}% del plazo y {r['porcentaje']:.0f}% de las horas"
                    res["horas"].append(r2)
    return res


def _parse(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date() if s else None
    except ValueError:
        return None


def edad(fecha_nac):
    d = _parse(fecha_nac)
    if not d:
        return None
    hoy = date.today()
    return hoy.year - d.year - ((hoy.month, hoy.day) < (d.month, d.day))


CRITERIOS = [
    ("desempeno_tecnico", "Desempeño técnico"),
    ("responsabilidad", "Responsabilidad"),
    ("puntualidad", "Puntualidad"),
    ("trabajo_equipo", "Trabajo en equipo"),
    ("resolucion_problemas", "Resolución de problemas"),
    ("aplicacion_conocimientos", "Aplicación de conocimientos"),
]


def evaluacion_data(ap_id, scoped=True):
    """Datos completos de evaluación de una asignación alumno-práctica."""
    rows = ap_rows({"ap_id": ap_id}, scoped=scoped)
    if not rows:
        return None
    ap = rows[0]
    evals = {e["evaluador"]: e for e in q(
        "SELECT * FROM evaluaciones WHERE alumno_practica_id = ? AND eliminado_en IS NULL", (ap_id,))}
    final = q1("SELECT * FROM alumnos_practicas WHERE id = ?", (ap_id,))
    ref = q1("SELECT * FROM referentes_empresas WHERE id = ?", (ap["referente_id"],)) if ap["referente_id"] else None
    proms = [e["promedio"] for e in evals.values()]
    sugerida = round(sum(proms) / len(proms), 2) if proms else None
    return {"ap": ap, "evals": evals, "final": final, "referente": ref, "sugerida": sugerida}


def sincronizar_estado_practica(practica_id):
    """Si todas las asignaciones están cerradas con resultado, refleja el resultado en la práctica."""
    estados = [r["estado"] for r in q("SELECT estado FROM alumnos_practicas WHERE practica_id = ? AND eliminado_en IS NULL", (practica_id,))]
    if not estados:
        return
    if all(e in ("Aprobada", "Desaprobada") for e in estados):
        nuevo = "Aprobada" if all(e == "Aprobada" for e in estados) else ("Desaprobada" if all(e == "Desaprobada" for e in estados) else "Finalizada")
        ex("UPDATE practicas SET estado = ? WHERE id = ?", (nuevo, practica_id), commit=False)
