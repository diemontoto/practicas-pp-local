"""Datos de demostración (ficticios). Usuarios: admin / coordinador / tutor1 / tutor2 / consulta  —  clave: Demo1234"""
import os
import random
import uuid
from datetime import date, datetime, timedelta

from flask import current_app

from . import services
from .auth import hash_password
from .db import commit, ex, q, q1

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
APELLIDOS = ["González", "Rodríguez", "Gómez", "Fernández", "López", "Díaz", "Martínez", "Pérez", "Romero", "Sánchez", "Acosta", "Benítez",
             "Medina", "Herrera", "Suárez", "Aguirre", "Giménez", "Gutiérrez", "Peralta", "Rojas", "Molina", "Castro", "Ortiz", "Silva"]
NOMBRES = ["Lucas", "Martina", "Mateo", "Sofía", "Thiago", "Valentina", "Joaquín", "Camila", "Bautista", "Julieta", "Santiago", "Florencia",
           "Facundo", "Agustina", "Nicolás", "Milagros", "Tomás", "Lucía", "Franco", "Micaela", "Ezequiel", "Brenda", "Lautaro", "Abril"]
EMPRESAS = [("Metalúrgica Del Sur S.R.L.", "Del Sur", "Fabricación de piezas metálicas", 4), ("Sistemas Andinos S.A.", "Andinos IT", "Desarrollo de software", 5),
            ("Electro Industrial Norte S.A.", "ElectroNorte", "Instalaciones eléctricas industriales", 4), ("Taller Mecánico Hermanos Paz", "Taller Paz", "Reparación de maquinaria", 3),
            ("Constructora Horizonte S.R.L.", "Horizonte", "Obras civiles", 4), ("Municipalidad - Dirección de Sistemas", "Sistemas Municipales", "Administración pública", 3),
            ("Frigorífico Los Aromos S.A.", "Los Aromos", "Industria alimenticia", 3), ("Agro Mecánica Pampeana S.A.", "AgroPampa", "Maquinaria agrícola", 4),
            ("Redes y Telecomunicaciones SRL", "RedTel", "Telecomunicaciones", 3), ("Cooperativa Eléctrica Local", "Coop. Eléctrica", "Servicios eléctricos", 2)]
TUTORES = [("Ramírez", "Gustavo"), ("Villalba", "Mariana"), ("Cabrera", "Hernán"), ("Domínguez", "Laura"), ("Ibarra", "Pablo"), ("Quiroga", "Silvia"), ("Navarro", "Diego")]
ESPECIALIDADES = [("PROG", "Programación"), ("ELEC", "Electrónica"), ("ELMEC", "Electromecánica"), ("MMO", "Maestro Mayor de Obras")]
PROYECTOS = {"PROG": ["Sistema de gestión de stock", "Portal web institucional", "Automatización de reportes"], "ELEC": ["Montaje de tableros de control", "Mantenimiento de equipos electrónicos"],
             "ELMEC": ["Mantenimiento preventivo de maquinaria", "Diseño de utillajes"], "MMO": ["Relevamiento y planos de obra", "Seguimiento de obra civil"]}


def _cuit(rng):
    base = "30" + "".join(str(rng.randint(0, 9)) for _ in range(8))
    mult = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    r = 11 - sum(int(a) * b for a, b in zip(base, mult)) % 11
    dv = {11: 0, 10: 9}.get(r, r)
    return f"{base[:2]}-{base[2:]}-{dv}"


def _archivo():
    name = uuid.uuid4().hex + ".pdf"
    with open(os.path.join(current_app.config["UPLOAD_DIR"], name), "wb") as f:
        f.write(PDF)
    return name, len(PDF)


def _doc(tipo_id, nombre, estado="Aprobado", venc=None, **ids):
    arch, size = _archivo()
    ex("INSERT INTO documentos(tipo_documento_id, alumno_id, empresa_id, practica_id, nombre_original, archivo, mime, tamano, estado, vencimiento) "
       "VALUES (?,?,?,?,?,?,?,?,?,?)", (tipo_id, ids.get("alumno_id"), ids.get("empresa_id"), ids.get("practica_id"), nombre, arch, "application/pdf", size, estado, venc), commit=False)


def run(force=False):
    if q1("SELECT 1 FROM alumnos") and not force:
        raise SystemExit("Ya hay alumnos cargados. Use --force para agregar los datos de demostración igualmente.")
    rng = random.Random(2026)
    hoy = date.today()
    ciclo = services.ciclo_activo()
    cid = ciclo["id"]
    esp = {}
    for cod, nom in ESPECIALIDADES:
        row = q1("SELECT id FROM especialidades WHERE codigo = ? AND eliminado_en IS NULL", (cod,))
        esp[cod] = row["id"] if row else ex("INSERT INTO especialidades(codigo, nombre, horas_pp) VALUES (?,?,200)", (cod, nom), commit=False).lastrowid
    cursos = [r["id"] for r in q("SELECT id FROM cursos WHERE requiere_pp = 1 ORDER BY orden")]
    divs = [r["id"] for r in q("SELECT id FROM divisiones ORDER BY id LIMIT 3")]
    # docentes y usuarios
    docs = []
    for i, (ap, no) in enumerate(TUTORES):
        docs.append(ex("INSERT INTO docentes(apellido, nombre, dni, email, telefono, especialidad_id, cargo) VALUES (?,?,?,?,?,?,?)",
                       (ap, no, str(20000000 + rng.randint(100000, 9999999)), f"{no.lower()}.{ap.lower()}@escuela.demo".replace("í", "i").replace("á", "a"),
                        f"11-5{rng.randint(100, 999)}-{rng.randint(1000, 9999)}", list(esp.values())[i % 4], "Docente de taller / Tutor de PP"), commit=False).lastrowid)
    pw = hash_password("Demo1234")
    roles = {r["codigo"]: r["id"] for r in q("SELECT id, codigo FROM roles")}
    for u, n, r, d in [("admin", "Administrador del sistema", "admin", None), ("coordinador", "Coordinación de PP", "coordinador", None),
                       ("tutor1", f"{TUTORES[0][1]} {TUTORES[0][0]}", "tutor", docs[0]), ("tutor2", f"{TUTORES[1][1]} {TUTORES[1][0]}", "tutor", docs[1]),
                       ("consulta", "Dirección (consulta)", "consulta", None)]:
        if not q1("SELECT 1 FROM usuarios WHERE username = ?", (u,)):
            ex("INSERT INTO usuarios(username, nombre, password_hash, rol_id, docente_id, activo, debe_cambiar_clave) VALUES (?,?,?,?,?,1,0)",
               (u, n, pw, roles[r], d), commit=False)
    # empresas y referentes
    emp, ref = [], {}
    for rs, nf, act, vac in EMPRESAS:
        e = ex("INSERT INTO empresas(razon_social, nombre_fantasia, cuit, direccion, localidad, telefono, email, actividad, vacantes) VALUES (?,?,?,?,?,?,?,?,?)",
               (rs, nf, _cuit(rng), f"Calle {rng.randint(100, 999)} N° {rng.randint(100, 3000)}", "Ciudad Demo", f"011-4{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
                f"contacto@{nf.lower().replace(' ', '').replace('.', '')}.demo", act, vac), commit=False).lastrowid
        emp.append(e)
        a, n = rng.choice(APELLIDOS), rng.choice(NOMBRES)
        ref[e] = ex("INSERT INTO referentes_empresas(empresa_id, nombre, cargo, telefono, email) VALUES (?,?,?,?,?)",
                    (e, f"{n} {a}", "Responsable de capacitación", f"11-6{rng.randint(100, 999)}-{rng.randint(1000, 9999)}", f"{n.lower()}@empresa.demo".replace("í", "i").replace("á", "a")), commit=False).lastrowid
    # alumnos
    alum = []
    usados = set()
    for i in range(40):
        while True:
            ap, no = rng.choice(APELLIDOS), rng.choice(NOMBRES)
            if (ap, no) not in usados:
                usados.add((ap, no))
                break
        nac = date(hoy.year - rng.choice([17, 17, 18, 18, 19]), rng.randint(1, 12), rng.randint(1, 28))
        menor = (hoy - nac).days < 18 * 365.25
        es = list(esp.values())[i % 4]
        alum.append((ex("INSERT INTO alumnos(legajo, apellido, nombre, dni, fecha_nacimiento, domicilio, telefono, email, curso_id, division_id, especialidad_id, ciclo_id, "
                        "estado_academico, tutor_legal_nombre, tutor_legal_parentesco, tutor_legal_telefono) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"{hoy.year}-{i + 1:03d}", ap, no, str(45000000 + i * 7919 + rng.randint(0, 900)), nac.isoformat(), f"Barrio Demo, calle {rng.randint(1, 99)}",
                         f"11-3{rng.randint(100, 999)}-{rng.randint(1000, 9999)}", None, cursos[i % len(cursos)], divs[i % len(divs)], es, cid, "Regular",
                         f"{rng.choice(NOMBRES)} {ap}" if menor else None, "Madre/Padre" if menor else None,
                         f"11-2{rng.randint(100, 999)}-{rng.randint(1000, 9999)}" if menor else None), commit=False).lastrowid, es))
    tipos = {r["nombre"]: r["id"] for r in q("SELECT id, nombre FROM tipos_documentos")}
    for e in emp:
        _doc(tipos["Convenio con la empresa"], "convenio.pdf", empresa_id=e, venc=(hoy + timedelta(days=rng.choice([10, 200, 400]))).isoformat())
    # prácticas: (empresa, escenario, cantidad de alumnos)
    escenarios = ["aprobada", "aprobada", "desaprobada", "curso_ok", "curso_ok", "curso_ok", "curso_atrasada", "curso_atrasada", "habilitada",
                  "habilitada", "docs", "docs", "pendiente", "proceso", "suspendida"]
    pos = 0
    for k, esc in enumerate(escenarios):
        e = emp[k % len(emp)]
        grupo = alum[pos:pos + rng.choice([1, 2, 3])]
        pos += len(grupo)
        if not grupo:
            break
        cod = next(c for c, v in esp.items() if v == grupo[0][1])
        tutor = docs[k % len(docs)]
        dur = 40
        if esc in ("aprobada", "desaprobada"):
            ini = hoy - timedelta(days=100)
        elif esc == "curso_ok":
            ini = hoy - timedelta(days=30)
        elif esc == "curso_atrasada":
            ini = hoy - timedelta(days=45)
        elif esc == "habilitada":
            ini = hoy + timedelta(days=rng.randint(3, 20))
        elif esc == "suspendida":
            ini = hoy - timedelta(days=20)
        else:
            ini = hoy + timedelta(days=rng.randint(15, 40))
        fin = ini + timedelta(days=75 if esc != "curso_atrasada" else 20)
        estado = {"aprobada": "Aprobada", "desaprobada": "Desaprobada", "curso_ok": "En curso", "curso_atrasada": "En curso", "habilitada": "Habilitada para comenzar",
                  "docs": "Documentación pendiente", "pendiente": "Pendiente de asignación", "proceso": "En proceso de asignación", "suspendida": "Suspendida"}[esc]
        pid = ex("INSERT INTO practicas(numero, ciclo_id, empresa_id, especialidad_id, tutor_id, referente_id, proyecto, fecha_inicio, fecha_fin_estimada, horas_requeridas, "
                 "dias_horarios, objetivos, estado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (services.siguiente_numero(cid), cid, e, esp[cod], tutor, ref[e], rng.choice(PROYECTOS[cod]), ini.isoformat(), fin.isoformat(), 200,
                  "Lunes a viernes de 8:00 a 12:00", "Aplicar en un entorno real los conocimientos de la especialidad.", estado), commit=False).lastrowid
        if esc not in ("pendiente", "proceso"):
            for t in ("Plan de prácticas", "Seguro / cobertura"):
                _doc(tipos[t], t.lower().replace(" ", "_") + ".pdf", practica_id=pid, estado="Aprobado" if esc != "docs" or t == "Plan de prácticas" else "Pendiente de revisión")
        for aid, es in grupo:
            apid = ex("INSERT INTO alumnos_practicas(practica_id, alumno_id, horas_requeridas, estado, fecha_inicio) VALUES (?,?,?,?,?)",
                      (pid, aid, 200, estado, ini.isoformat() if esc in ("aprobada", "desaprobada", "curso_ok", "curso_atrasada", "suspendida") else None), commit=False).lastrowid
            ex("INSERT OR IGNORE INTO asignaciones_tutor(docente_id, alumno_id, ciclo_id) VALUES (?,?,?)", (tutor, aid, cid), commit=False)
            if esc not in ("pendiente", "proceso"):
                ok = esc != "docs"
                _doc(tipos["Autorización de padres o tutores"], "autorizacion.pdf", alumno_id=aid, estado="Aprobado" if ok else "Observado")
                if ok:
                    _doc(tipos["Constancia de alumno regular"], "constancia.pdf", alumno_id=aid)
                    _doc(tipos["Acuerdo individual"], "acuerdo.pdf", practica_id=pid)
            # inasistencias: las horas corren solas los días de práctica; sólo se cargan las faltas
            if esc in ("aprobada", "desaprobada", "curso_ok", "curso_atrasada", "suspendida"):
                d, ultimo = ini, min(hoy, fin)
                while d <= ultimo:
                    if d.weekday() < 5 and rng.random() < 0.06:
                        just = rng.random() < 0.6
                        ex("INSERT INTO asistencias(alumno_practica_id, fecha, tipo, horas, observaciones) VALUES (?,?,?,?,?)",
                           (apid, d.isoformat(), "Ausente justificado" if just else "Ausente injustificado", 4,
                            "Certificado médico" if just else None), commit=False)
                    d += timedelta(days=1)
            if esc == "suspendida":
                ex("INSERT INTO dias_sin_actividad(fecha_desde, motivo, alumno_practica_id) VALUES (?,?,?)",
                   ((hoy - timedelta(days=6)).isoformat(), "Suspensión", apid), commit=False)
            if esc in ("aprobada", "desaprobada", "curso_ok"):
                ex("INSERT INTO seguimiento(practica_id, alumno_practica_id, fecha, tipo, docente_id, descripcion) VALUES (?,?,?,?,?,?)",
                   (pid, apid, (ini + timedelta(days=10)).isoformat(), "Visita", tutor, "Visita a la empresa. El alumno se desempeña de acuerdo a lo planificado."), commit=False)
            if esc in ("aprobada", "desaprobada"):
                base = rng.randint(7, 10) if esc == "aprobada" else rng.randint(3, 5)
                prom_total = []
                for quien, nombre in (("Tutor", f"{TUTORES[k % len(TUTORES)][1]} {TUTORES[k % len(TUTORES)][0]}"), ("Referente", None)):
                    notas = [max(1, min(10, base + rng.randint(-1, 1))) for _ in services.CRITERIOS]
                    prom = round(sum(notas) / len(notas), 2)
                    prom_total.append(prom)
                    ex("INSERT INTO evaluaciones(alumno_practica_id, evaluador, evaluador_nombre, fecha, desempeno_tecnico, responsabilidad, puntualidad, trabajo_equipo, "
                       "resolucion_problemas, aplicacion_conocimientos, promedio, observaciones) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (apid, quien, nombre, (fin - timedelta(days=5)).isoformat(), *notas, prom, "Evaluación de demostración."), commit=False)
                nota = round(sum(prom_total) / 2)
                ex("UPDATE alumnos_practicas SET calificacion_final = ?, resultado = ?, fecha_fin_real = ?, fecha_resultado = ?, observaciones_finales = ? WHERE id = ?",
                   (nota, "Aprobado" if esc == "aprobada" else "Desaprobado", fin.isoformat(), fin.isoformat(), "Cierre de práctica (demo).", apid), commit=False)
        if esc in ("aprobada", "desaprobada"):
            ex("UPDATE practicas SET fecha_fin_real = ? WHERE id = ?", (fin.isoformat(), pid), commit=False)
    commit()
    print("Datos de demostración cargados. Usuarios: admin, coordinador, tutor1, tutor2, consulta — clave Demo1234")
