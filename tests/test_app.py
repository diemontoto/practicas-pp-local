"""Pruebas de integración: ejecutar con  python -m unittest discover -s tests -v"""
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import Workbook  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pp_test_")
os.environ["PP_INSTANCE"] = TMP
from app import create_app, seed  # noqa: E402
from app.db import connect  # noqa: E402

APP = create_app()
APP.config["TESTING"] = False
with APP.app_context():
    from app.db import get_db
    get_db()
    seed.run()

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def db():
    con = connect(APP.config["DATABASE"])
    return con


def scalar(sql, p=()):
    con = db()
    try:
        row = con.execute(sql, p).fetchone()
        return row[0] if row else None
    finally:
        con.close()


class Client:
    def __init__(self):
        self.c = APP.test_client()

    def token(self, path="/login"):
        html = self.c.get(path).get_data(as_text=True)
        m = re.search(r'name="_csrf" value="([^"]+)"', html)
        return m.group(1) if m else None

    def login(self, user, pw="Demo1234"):
        t = self.token("/login")
        return self.c.post("/login", data={"username": user, "password": pw, "_csrf": t})

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, page=None, **kw):
        data = dict(data or {})
        data["_csrf"] = self.token(page or "/")
        return self.c.post(url, data=data, **kw)


def as_user(u, pw="Demo1234"):
    c = Client()
    r = c.login(u, pw)
    assert r.status_code == 302, (u, r.status_code)
    return c


class Seguridad(unittest.TestCase):
    def test_requiere_login(self):
        c = Client()
        for url in ("/", "/alumnos/", "/usuarios/", "/informes/", "/configuracion/respaldos"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])
        self.assertEqual(c.get("/salud").status_code, 200)

    def test_login_incorrecto_y_bloqueo(self):
        c = Client()
        for _ in range(5):
            r = c.login("consulta", "mala")
            self.assertEqual(r.status_code, 200)
        r = c.login("consulta", "Demo1234")
        self.assertEqual(r.status_code, 200, "cuenta bloqueada tras 5 intentos")
        con = db()
        con.execute("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE username = 'consulta'")
        con.commit()
        con.close()
        self.assertEqual(as_user("consulta").get("/").status_code, 200)

    def test_csrf(self):
        c = as_user("admin")
        r = c.c.post("/alumnos/1/baja", data={})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(scalar("SELECT eliminado_en FROM alumnos WHERE id = 1"))
        r = c.c.post("/alumnos/nuevo", data={"_csrf": "falso", "apellido": "x"})
        self.assertEqual(r.status_code, 400)

    def test_cabeceras(self):
        r = as_user("admin").get("/")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_clave_hasheada(self):
        h = scalar("SELECT password_hash FROM usuarios WHERE username = 'admin'")
        self.assertNotIn("Demo1234", h)
        self.assertTrue(h.startswith(("scrypt:", "pbkdf2:")))

    def test_inyeccion_sql_y_xss(self):
        c = as_user("admin")
        for term in ("' OR 1=1 --", "%", "'; DROP TABLE alumnos; --", "<script>alert(1)</script>"):
            self.assertEqual(c.get("/alumnos/", query_string={"q": term}).status_code, 200)
            self.assertEqual(c.get("/buscar", query_string={"q": term}).status_code, 200)
        self.assertGreater(scalar("SELECT COUNT(*) FROM alumnos"), 0)
        r = c.post("/alumnos/nuevo", {"legajo": "XSS-1", "apellido": "<script>alert(9)</script>", "nombre": "Zed", "dni": "30111222", "curso_id": 1,
                                       "ciclo_id": 1, "estado_academico": "Regular"}, page="/alumnos/nuevo")
        self.assertEqual(r.status_code, 302)
        aid = scalar("SELECT id FROM alumnos WHERE legajo = 'XSS-1'")
        html = c.get(f"/alumnos/{aid}").get_data(as_text=True)
        self.assertNotIn("<script>alert(9)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class Permisos(unittest.TestCase):
    def test_consulta_solo_lectura(self):
        c = as_user("consulta")
        self.assertEqual(c.get("/alumnos/").status_code, 200)
        self.assertEqual(c.get("/alumnos/nuevo").status_code, 403)
        r = c.post("/alumnos/1/baja", page="/alumnos/")
        self.assertEqual(r.status_code, 403)
        for url in ("/usuarios/", "/configuracion/respaldos", "/configuracion/roles", "/configuracion/historial"):
            self.assertEqual(c.get(url).status_code, 403, url)
        self.assertIsNone(scalar("SELECT eliminado_en FROM alumnos WHERE id = 1"))

    def test_coordinador(self):
        c = as_user("coordinador")
        self.assertEqual(c.get("/alumnos/nuevo").status_code, 200)
        self.assertEqual(c.get("/usuarios/").status_code, 403)
        self.assertEqual(c.get("/configuracion/respaldos").status_code, 403)
        r = c.post("/alumnos/1/baja", page="/alumnos/")
        self.assertEqual(r.status_code, 403, "el coordinador no da de baja alumnos")

    def test_tutor_alcance(self):
        c = as_user("tutor1")
        doc = scalar("SELECT docente_id FROM usuarios WHERE username = 'tutor1'")
        propios = {r[0] for r in db().execute("SELECT alumno_id FROM asignaciones_tutor WHERE docente_id = ?", (doc,))}
        ajenos = [r[0] for r in db().execute("SELECT id FROM alumnos WHERE eliminado_en IS NULL") if r[0] not in propios]
        self.assertTrue(propios and ajenos)
        html = c.get("/alumnos/", query_string={"per_page": 100}).get_data(as_text=True)
        for a in ajenos[:5]:
            self.assertNotIn(f'/alumnos/{a}"', html)
            self.assertIn(c.get(f"/alumnos/{a}").status_code, (403, 404))
            self.assertIn(c.get(f"/alumnos/{a}/ficha.pdf").status_code, (403, 404))
        self.assertEqual(c.get(f"/alumnos/{next(iter(propios))}").status_code, 200)
        self.assertEqual(c.get("/alumnos/nuevo").status_code, 403)
        ap_ajeno = scalar("SELECT ap.id FROM alumnos_practicas ap WHERE ap.alumno_id = ? LIMIT 1", (ajenos[0],))
        if ap_ajeno:
            self.assertIn(c.get(f"/seguimiento/ap/{ap_ajeno}").status_code, (403, 404))
            self.assertIn(c.get(f"/evaluaciones/ap/{ap_ajeno}").status_code, (403, 404))

    def test_ultimo_admin_protegido(self):
        c = as_user("admin")
        uid = scalar("SELECT id FROM usuarios WHERE username = 'admin'")
        c.post(f"/usuarios/{uid}/baja", page="/usuarios/")
        self.assertIsNone(scalar("SELECT eliminado_en FROM usuarios WHERE id = ?", (uid,)))


class Crud(unittest.TestCase):
    def setUp(self):
        self.c = as_user("admin")

    def test_alumno_crud_y_validaciones(self):
        base = {"legajo": "T-100", "apellido": "Prueba", "nombre": "Ana", "dni": "40123456", "curso_id": 6, "ciclo_id": 1, "estado_academico": "Regular"}
        r = self.c.post("/alumnos/nuevo", base, page="/alumnos/nuevo")
        self.assertEqual(r.status_code, 302)
        aid = scalar("SELECT id FROM alumnos WHERE legajo = 'T-100'")
        self.assertIsNotNone(scalar("SELECT creado_por FROM alumnos WHERE id = ?", (aid,)))
        # DNI duplicado y DNI inválido
        r = self.c.post("/alumnos/nuevo", {**base, "legajo": "T-101"}, page="/alumnos/nuevo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE legajo = 'T-101'"), 0)
        r = self.c.post("/alumnos/nuevo", {**base, "legajo": "T-102", "dni": "12ab"}, page="/alumnos/nuevo")
        self.assertEqual(r.status_code, 200)
        # edición con auditoría
        r = self.c.post(f"/alumnos/{aid}/editar", {**base, "apellido": "Modificada"}, page=f"/alumnos/{aid}/editar")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(scalar("SELECT apellido FROM alumnos WHERE id = ?", (aid,)), "Modificada")
        self.assertIsNotNone(scalar("SELECT modificado_en FROM alumnos WHERE id = ?", (aid,)))
        self.assertGreater(scalar("SELECT COUNT(*) FROM historial_cambios WHERE tabla = 'alumnos' AND registro_id = ? AND accion = 'modificar'", (aid,)), 0)
        # baja lógica + restaurar
        self.c.post(f"/alumnos/{aid}/baja", page="/alumnos/")
        self.assertIsNotNone(scalar("SELECT eliminado_en FROM alumnos WHERE id = ?", (aid,)))
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE id = ?", (aid,)), 1, "baja lógica: el registro persiste")
        self.c.post(f"/alumnos/{aid}/restaurar", page="/alumnos/")
        self.assertIsNone(scalar("SELECT eliminado_en FROM alumnos WHERE id = ?", (aid,)))

    def test_menor_requiere_responsable(self):
        nac = (date.today() - timedelta(days=16 * 366)).isoformat()
        d = {"legajo": "M-1", "apellido": "Menor", "nombre": "Test", "dni": "50111222", "curso_id": 6, "ciclo_id": 1, "estado_academico": "Regular", "fecha_nacimiento": nac}
        self.c.post("/alumnos/nuevo", d, page="/alumnos/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE legajo = 'M-1'"), 0)

    def test_empresa_cuit(self):
        d = {"razon_social": "Empresa Test SA", "cuit": "20-11111111-1", "vacantes": 2}
        r = self.c.post("/empresas/nuevo", d, page="/empresas/nuevo")
        self.assertEqual(r.status_code, 200, "CUIT con dígito verificador incorrecto")
        d["cuit"] = "30-71234567-1"
        con = db()
        row = con.execute("SELECT cuit FROM empresas LIMIT 1").fetchone()
        con.close()
        d["cuit"] = row[0].replace(row[0][-1], "0" if row[0][-1] != "0" else "1")  # cambia el dígito verificador
        r = self.c.post("/empresas/nuevo", d, page="/empresas/nuevo")
        self.assertEqual(r.status_code, 200)

    def test_usuario_nuevo_y_clave_debil(self):
        d = {"username": "nuevo1", "nombre": "Nuevo Uno", "rol_id": 4, "password": "abc", "activo": "1"}
        r = self.c.post("/usuarios/nuevo", d, page="/usuarios/nuevo")
        self.assertEqual(r.status_code, 200)
        d["password"] = "Segura2026"
        r = self.c.post("/usuarios/nuevo", d, page="/usuarios/nuevo")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(as_user("nuevo1", "Segura2026").get("/").status_code in (200, 302), True)


class Practicas(unittest.TestCase):
    def setUp(self):
        self.c = as_user("coordinador")

    def _nueva(self):
        d = {"ciclo_id": 1, "empresa_id": 1, "proyecto": "Proyecto de prueba", "horas_requeridas": 10, "fecha_inicio": date.today().isoformat(),
             "dias_semana": ["1", "2", "3", "4", "5", "6", "7"], "horas_dia": "4",
             "fecha_fin_estimada": (date.today() + timedelta(days=30)).isoformat()}
        r = self.c.post("/practicas/nuevo", d, page="/practicas/nuevo")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return scalar("SELECT id FROM practicas ORDER BY id DESC LIMIT 1")

    def test_flujo_completo(self):
        pid = self._nueva()
        self.assertRegex(scalar("SELECT numero FROM practicas WHERE id = ?", (pid,)), r"^PP-\d{4}-\d{4}$")
        libre = scalar("SELECT id FROM alumnos WHERE id NOT IN (SELECT alumno_id FROM alumnos_practicas) AND eliminado_en IS NULL LIMIT 1")
        self.c.post(f"/practicas/{pid}/alumnos", {"alumno_ids": libre, "horas": 8}, page=f"/practicas/{pid}")
        apid = scalar("SELECT id FROM alumnos_practicas WHERE practica_id = ? AND alumno_id = ?", (pid, libre))
        self.assertIsNotNone(apid)
        self.assertEqual(scalar("SELECT horas_requeridas FROM alumnos_practicas WHERE id = ?", (apid,)), 8)
        # sin documentación no puede habilitarse
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "En curso"}, page=f"/practicas/{pid}")
        self.assertNotEqual(scalar("SELECT estado FROM alumnos_practicas WHERE id = ?", (apid,)), "En curso")
        # no puede aprobarse a mano
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "Aprobada"}, page=f"/practicas/{pid}")
        self.assertNotEqual(scalar("SELECT estado FROM alumnos_practicas WHERE id = ?", (apid,)), "Aprobada")
        # forzar queda auditado
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "En curso", "forzar": "1"}, page=f"/practicas/{pid}")
        self.assertEqual(scalar("SELECT estado FROM alumnos_practicas WHERE id = ?", (apid,)), "En curso")
        # la asistencia corre sola; sólo se cargan inasistencias (y sólo en días de práctica)
        hoy = date.today().isoformat()
        self.c.post("/seguimiento/asistencias/nuevo", {"alumno_practica_id": apid, "fecha": (date.today() - timedelta(days=1)).isoformat(),
                                                        "tipo": "Ausente injustificado"}, page="/seguimiento/asistencias/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM asistencias WHERE alumno_practica_id = ?", (apid,)), 0)   # anterior al inicio
        self.c.post("/seguimiento/asistencias/nuevo", {"alumno_practica_id": apid, "fecha": hoy, "tipo": "Presente"}, page="/seguimiento/asistencias/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM asistencias WHERE alumno_practica_id = ?", (apid,)), 0)   # ya no se cargan presentes
        self.assertEqual(self.c.get(f"/seguimiento/ap/{apid}").status_code, 200)
        # evaluación y cierre: no se aprueba con horas incompletas sin forzar
        for quien in ("Tutor", "Referente"):
            self.c.post("/evaluaciones/cargar/nuevo", {"alumno_practica_id": apid, "evaluador": quien, "fecha": hoy, "desempeno_tecnico": 8, "responsabilidad": 8,
                                                        "puntualidad": 9, "trabajo_equipo": 8, "resolucion_problemas": 7, "aplicacion_conocimientos": 8},
                        page="/evaluaciones/cargar/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM evaluaciones WHERE alumno_practica_id = ? AND eliminado_en IS NULL", (apid,)), 2)
        self.c.post(f"/evaluaciones/ap/{apid}/final", {"calificacion_final": "8", "resultado": "Aprobado"}, page=f"/evaluaciones/ap/{apid}")
        self.assertNotEqual(scalar("SELECT estado FROM alumnos_practicas WHERE id = ?", (apid,)), "Aprobada")
        self.c.post(f"/evaluaciones/ap/{apid}/final", {"calificacion_final": "8", "resultado": "Aprobado", "forzar": "1"}, page=f"/evaluaciones/ap/{apid}")
        self.assertEqual(scalar("SELECT estado FROM alumnos_practicas WHERE id = ?", (apid,)), "Aprobada")
        r = self.c.get(f"/evaluaciones/ap/{apid}/informe.pdf")
        self.assertEqual(r.headers["Content-Type"], "application/pdf")
        self.assertTrue(r.data.startswith(b"%PDF"))

    def _practica_en_curso(self, dias_atras=13, dias_semana=("1", "2", "3", "4", "5", "6", "7"), horas_dia="4", requeridas=100):
        """Práctica con un alumno «En curso» iniciada hace `dias_atras` días. Devuelve (practica_id, ap_id)."""
        ini = date.today() - timedelta(days=dias_atras)
        d = {"ciclo_id": 1, "empresa_id": 1, "proyecto": "Asistencia automática", "horas_requeridas": requeridas, "fecha_inicio": ini.isoformat(),
             "fecha_fin_estimada": (date.today() + timedelta(days=30)).isoformat(), "dias_semana": list(dias_semana), "horas_dia": horas_dia}
        r = self.c.post("/practicas/nuevo", d, page="/practicas/nuevo")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        pid = scalar("SELECT id FROM practicas ORDER BY id DESC LIMIT 1")
        libre = scalar("SELECT id FROM alumnos WHERE id NOT IN (SELECT alumno_id FROM alumnos_practicas) AND eliminado_en IS NULL LIMIT 1")
        self.c.post(f"/practicas/{pid}/alumnos", {"alumno_ids": libre}, page=f"/practicas/{pid}")
        apid = scalar("SELECT id FROM alumnos_practicas WHERE practica_id = ? AND alumno_id = ?", (pid, libre))
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "En curso", "forzar": "1"}, page=f"/practicas/{pid}")
        return pid, apid

    def _horas(self, apid):
        from app import services
        with APP.app_context():
            r = services.ap_rows({"ap_id": apid}, scoped=False, con_docs=False)[0]
        return r

    def test_asistencia_automatica(self):
        pid, apid = self._practica_en_curso(dias_atras=13)            # 14 días corridos, todos de práctica, 4 h
        r = self._horas(apid)
        self.assertEqual((r["dias_programados"], r["horas_programadas"], r["horas_realizadas"]), (14, 56, 56))
        # una inasistencia de jornada completa y otra parcial descuentan horas
        f1, f2 = (date.today() - timedelta(days=3)).isoformat(), (date.today() - timedelta(days=2)).isoformat()
        self.c.post("/seguimiento/asistencias/nuevo", {"alumno_practica_id": apid, "fecha": f1, "tipo": "Ausente injustificado"}, page="/seguimiento/asistencias/nuevo")
        self.c.post("/seguimiento/asistencias/nuevo", {"alumno_practica_id": apid, "fecha": f2, "tipo": "Ausente justificado", "horas": "1.5"}, page="/seguimiento/asistencias/nuevo")
        r = self._horas(apid)
        self.assertEqual((r["horas_descontadas"], r["horas_realizadas"], r["faltas_injust"], r["faltas_just"]), (5.5, 50.5, 1, 1))
        # rechazos: duplicada, futura, más horas que la jornada
        fut = (date.today() + timedelta(days=1)).isoformat()
        for datos in ({"fecha": f1, "tipo": "Ausente justificado"}, {"fecha": fut, "tipo": "Ausente justificado"},
                      {"fecha": date.today().isoformat(), "tipo": "Ausente justificado", "horas": "9"}):
            self.c.post("/seguimiento/asistencias/nuevo", dict(datos, alumno_practica_id=apid), page="/seguimiento/asistencias/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM asistencias WHERE alumno_practica_id = ? AND eliminado_en IS NULL", (apid,)), 2)
        # carga en lote del día: marcar ausente y luego volver a presente quita la inasistencia
        hoy = date.today().isoformat()
        self.c.post(f"/seguimiento/practica/{pid}/dia", {"fecha": hoy, f"tipo_{apid}": "Ausente injustificado"}, page=f"/seguimiento/practica/{pid}/dia")
        self.assertEqual(self._horas(apid)["horas_realizadas"], 46.5)
        self.c.post(f"/seguimiento/practica/{pid}/dia", {"fecha": hoy, f"tipo_{apid}": ""}, page=f"/seguimiento/practica/{pid}/dia")
        self.assertEqual(self._horas(apid)["horas_realizadas"], 50.5)
        for url in (f"/seguimiento/ap/{apid}", f"/seguimiento/practica/{pid}/dia", f"/practicas/{pid}", f"/practicas/{pid}/editar", "/informes/asistencia"):
            self.assertEqual(self.c.get(url).status_code, 200, url)

    def test_dias_de_la_semana_feriados_y_fin(self):
        from app import services
        # sólo lunes, miércoles y viernes, 3 h; período fijo de dos semanas
        dias = services.dias_de_practica("2026-03-02", "2026-03-15", "1,3,5")
        self.assertEqual(dias, ["2026-03-02", "2026-03-04", "2026-03-06", "2026-03-09", "2026-03-11", "2026-03-13"])
        self.assertEqual(len(services.dias_de_practica("2026-03-02", "2026-03-15", "1,3,5", [("2026-03-04", "2026-03-09")])), 3)
        pid, apid = self._practica_en_curso(dias_atras=9)
        self.assertEqual(self._horas(apid)["horas_realizadas"], 40)
        # un feriado general deja de sumar ese día y no admite inasistencias
        fer = (date.today() - timedelta(days=4)).isoformat()
        r = self.c.post("/configuracion/dias_sin_actividad/nuevo", {"fecha_desde": fer, "motivo": "Feriado de prueba"}, page="/configuracion/dias_sin_actividad/nuevo")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._horas(apid)["horas_realizadas"], 36)
        self.c.post("/seguimiento/asistencias/nuevo", {"alumno_practica_id": apid, "fecha": fer, "tipo": "Ausente injustificado"}, page="/seguimiento/asistencias/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM asistencias WHERE alumno_practica_id = ?", (apid,)), 0)
        self.assertEqual(self.c.get("/configuracion/dias_sin_actividad/").status_code, 200)
        fid = scalar("SELECT id FROM dias_sin_actividad WHERE motivo = 'Feriado de prueba'")
        as_user("admin").post(f"/configuracion/dias_sin_actividad/{fid}/baja", page="/configuracion/dias_sin_actividad/")
        self.assertEqual(self._horas(apid)["horas_realizadas"], 40)
        # las horas dejan de correr en la fecha de finalización
        con = db()
        con.execute("UPDATE practicas SET fecha_fin_estimada = ? WHERE id = ?", ((date.today() - timedelta(days=5)).isoformat(), pid))
        con.commit()
        con.close()
        self.assertEqual(self._horas(apid)["horas_realizadas"], 20)

    def test_suspension_detiene_las_horas(self):
        pid, apid = self._practica_en_curso(dias_atras=4)
        self.assertEqual(self._horas(apid)["horas_realizadas"], 20)
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "Suspendida"}, page=f"/practicas/{pid}")
        self.assertEqual(self._horas(apid)["horas_realizadas"], 16)          # hoy ya no suma
        con = db()                                                            # la suspensión empezó hace 2 días
        con.execute("UPDATE dias_sin_actividad SET fecha_desde = ? WHERE alumno_practica_id = ?", ((date.today() - timedelta(days=2)).isoformat(), apid))
        con.commit()
        con.close()
        self.assertEqual(self._horas(apid)["horas_realizadas"], 8)
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "En curso", "forzar": "1"}, page=f"/practicas/{pid}")
        self.assertEqual(self._horas(apid)["horas_realizadas"], 12)          # vuelve a sumar desde hoy
        # antes de comenzar no corren horas
        self.c.post(f"/practicas/ap/{apid}/estado", {"estado": "Habilitada para comenzar", "forzar": "1"}, page=f"/practicas/{pid}")
        self.assertEqual(self._horas(apid)["horas_realizadas"], 0)

    def test_migracion_de_base_anterior(self):
        import sqlite3
        from app.db import migrate
        path = os.path.join(TMP, "vieja.db")
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE practicas (id INTEGER PRIMARY KEY, numero TEXT);
            CREATE TABLE alumnos_practicas (id INTEGER PRIMARY KEY, practica_id INTEGER, estado TEXT, modificado_en TEXT, eliminado_en TEXT);
            CREATE TABLE asistencias (id INTEGER PRIMARY KEY, alumno_practica_id INTEGER, fecha TEXT, tipo TEXT, horas REAL, eliminado_en TEXT);
            CREATE TABLE usuarios (id INTEGER PRIMARY KEY);
            CREATE TABLE historial_cambios (id INTEGER PRIMARY KEY, fecha TEXT, usuario_id INTEGER, usuario_nombre TEXT, tabla TEXT, registro_id INTEGER, accion TEXT, detalle TEXT, ip TEXT);
            INSERT INTO practicas VALUES (1, 'PP-1');
            INSERT INTO alumnos_practicas VALUES (1, 1, 'En curso', NULL, NULL), (2, 1, 'Suspendida', '2026-05-04 10:00:00', NULL);
            INSERT INTO asistencias VALUES (1, 1, '2026-05-04', 'Presente', 4, NULL), (2, 1, '2026-05-05', 'Ausente justificado', 0, NULL);
        """)
        con.commit()
        con.close()
        migrate(path)
        migrate(path)                                                         # idempotente
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT dias_semana, horas_dia FROM practicas").fetchone(), ("1,2,3,4,5", 4))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM asistencias WHERE eliminado_en IS NULL").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT horas FROM asistencias WHERE id = 2").fetchone()[0], 4)
        self.assertEqual(con.execute("SELECT fecha_desde, alumno_practica_id FROM dias_sin_actividad").fetchall(), [("2026-05-04", 2)])
        con.close()

    def test_alumno_no_duplicado_en_practica(self):
        pid = self._nueva()
        libre = scalar("SELECT id FROM alumnos WHERE id NOT IN (SELECT alumno_id FROM alumnos_practicas) AND eliminado_en IS NULL LIMIT 1")
        for _ in range(2):
            self.c.post(f"/practicas/{pid}/alumnos", {"alumno_ids": libre}, page=f"/practicas/{pid}")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos_practicas WHERE practica_id = ? AND alumno_id = ?", (pid, libre)), 1)

    def test_fechas_incoherentes(self):
        d = {"ciclo_id": 1, "empresa_id": 1, "proyecto": "Fechas malas", "horas_requeridas": 10, "fecha_inicio": "2026-06-10", "fecha_fin_estimada": "2026-06-01"}
        r = self.c.post("/practicas/nuevo", d, page="/practicas/nuevo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(scalar("SELECT COUNT(*) FROM practicas WHERE proyecto = 'Fechas malas'"), 0)


class Documentos(unittest.TestCase):
    def test_subida_y_descarga(self):
        c = as_user("coordinador")
        tipo = scalar("SELECT id FROM tipos_documentos WHERE nombre LIKE 'Constancia%'")
        aid = scalar("SELECT id FROM alumnos WHERE id NOT IN (SELECT alumno_id FROM documentos WHERE alumno_id IS NOT NULL) LIMIT 1")
        malo = {"tipo_documento_id": tipo, "alumno_id": aid, "archivo": (io.BytesIO(b"MZ\x90 ejecutable"), "virus.exe")}
        c.post("/documentos/nuevo", malo, page="/documentos/nuevo", content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM documentos WHERE nombre_original = 'virus.exe'"), 0)
        falso = {"tipo_documento_id": tipo, "alumno_id": aid, "archivo": (io.BytesIO(b"no soy un pdf"), "falso.pdf")}
        c.post("/documentos/nuevo", falso, page="/documentos/nuevo", content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM documentos WHERE nombre_original = 'falso.pdf'"), 0, "firma de archivo inválida")
        ok = {"tipo_documento_id": tipo, "alumno_id": aid, "archivo": (io.BytesIO(PDF), "constancia_prueba.pdf")}
        c.post("/documentos/nuevo", ok, page="/documentos/nuevo", content_type="multipart/form-data")
        did = scalar("SELECT id FROM documentos WHERE nombre_original = 'constancia_prueba.pdf'")
        self.assertIsNotNone(did)
        r = c.get(f"/documentos/{did}/descargar")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, PDF)
        self.assertEqual(Client().get(f"/documentos/{did}/descargar").status_code, 302, "descarga requiere sesión")
        c.post(f"/documentos/{did}/estado", {"estado": "Aprobado"}, page="/documentos/")
        self.assertEqual(scalar("SELECT estado FROM documentos WHERE id = ?", (did,)), "Aprobado")


class ImportExport(unittest.TestCase):
    def test_importar_excel(self):
        c = as_user("admin")
        wb = Workbook()
        ws = wb.active
        ws.append(["Legajo", "Apellido", "Nombre", "DNI", "Fecha de nacimiento", "Curso", "División", "Especialidad", "Año de cursada"])
        ws.append(["IMP-1", "Importada", "Uno", "41000001", "2007-05-10", "6° año", "1ª", "Programación", date.today().year])
        ws.append(["IMP-2", "Importada", "Dos", "41000002", "2007-06-11", "6° año", "1ª", "Programación", date.today().year])
        ws.append(["IMP-3", "", "Roto", "xx", "", "", "", "", ""])
        buf = io.BytesIO()
        wb.save(buf)
        data = buf.getvalue()
        c.post("/alumnos/importar", {"archivo": (io.BytesIO(data), "alumnos.xlsx"), "solo_validar": "1"}, page="/alumnos/importar", content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE legajo LIKE 'IMP-%'"), 0, "validar no guarda")
        c.post("/alumnos/importar", {"archivo": (io.BytesIO(data), "alumnos.xlsx")}, page="/alumnos/importar", content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE legajo IN ('IMP-1','IMP-2')"), 2)
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE legajo = 'IMP-3'"), 0, "la fila inválida se rechaza")
        c.post("/alumnos/importar", {"archivo": (io.BytesIO(data), "alumnos.xlsx")}, page="/alumnos/importar", content_type="multipart/form-data")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos WHERE dni = '41000001'"), 1, "reimportar no duplica")
        r = c.get("/alumnos/plantilla.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"PK"))

    def test_todos_los_informes(self):
        from app.views.informes import REPORTES
        c = as_user("admin")
        for clave in REPORTES:
            self.assertEqual(c.get(f"/informes/{clave}").status_code, 200, clave)
            r = c.get(f"/informes/{clave}.pdf")
            self.assertEqual((r.status_code, r.data[:4]), (200, b"%PDF"), clave)
            r = c.get(f"/informes/{clave}.xlsx")
            self.assertEqual((r.status_code, r.data[:2]), (200, b"PK"), clave)
        self.assertEqual(c.get("/informes/inexistente").status_code, 404)

    def test_fichas_pdf_y_excel_de_listados(self):
        c = as_user("admin")
        r = c.get("/alumnos/1/ficha.pdf")
        self.assertEqual((r.status_code, r.data[:4]), (200, b"%PDF"))
        for url in ("/alumnos/", "/empresas/", "/practicas/", "/docentes/", "/documentos/"):
            r = c.get(url, query_string={"export": "xlsx"})
            self.assertEqual(r.status_code, 200, url)
            self.assertTrue(r.data[:2] == b"PK" or b"<html" in r.data[:300].lower(), url)


class Navegacion(unittest.TestCase):
    def test_todas_las_pantallas(self):
        c = as_user("admin")
        con = db()
        ids = {"alumnos.ficha": "alumnos", "empresas.ficha": "empresas", "docentes.ficha": "docentes", "practicas.ficha": "practicas"}
        urls = ["/", "/alertas", "/buscar?q=a", "/documentos/control", "/seguimiento/", "/evaluaciones/", "/informes/", "/configuracion/", "/configuracion/roles",
                "/configuracion/respaldos", "/configuracion/historial", "/usuarios/", "/cuenta/clave", "/alumnos/importar", "/practicas/nuevo",
                "/documentos/nuevo", "/empresas/referentes/nuevo"]
        urls += [f"/{t}/{i[0]}" for t in ("alumnos", "empresas", "docentes", "practicas") for i in con.execute(f"SELECT id FROM {t} LIMIT 4")]
        urls += [f"/seguimiento/ap/{i[0]}" for i in con.execute("SELECT id FROM alumnos_practicas LIMIT 5")]
        urls += [f"/evaluaciones/ap/{i[0]}" for i in con.execute("SELECT id FROM alumnos_practicas LIMIT 5")]
        urls += [f"/seguimiento/practica/{i[0]}/dia" for i in con.execute("SELECT id FROM practicas WHERE estado = 'En curso' LIMIT 2")]
        con.close()
        for u in urls:
            self.assertEqual(c.get(u).status_code, 200, u)
        for u in ("/alumnos/99999", "/practicas/99999", "/no-existe"):
            self.assertEqual(c.get(u).status_code, 404, u)

    def test_dashboard_filtros(self):
        c = as_user("admin")
        self.assertEqual(c.get("/", query_string={"especialidad_id": 1, "curso_id": 6, "division_id": 1}).status_code, 200)
        self.assertEqual(c.get("/", query_string={"ciclo_id": "abc"}).status_code, 200)

    def test_cambiar_clave_forzado(self):
        con = db()
        con.execute("UPDATE usuarios SET debe_cambiar_clave = 1 WHERE username = 'consulta'")
        con.commit()
        con.close()
        c = as_user("consulta")
        r = c.get("/alumnos/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/cuenta/clave", r.headers["Location"])
        c.post("/cuenta/clave", {"actual": "Demo1234", "nueva": "Nueva2026x", "repetir": "Nueva2026x"}, page="/cuenta/clave")
        self.assertEqual(c.get("/alumnos/").status_code, 200)
        con = db()
        from app.auth import hash_password
        con.execute("UPDATE usuarios SET password_hash = ? WHERE username = 'consulta'", (hash_password("Demo1234"),))
        con.commit()
        con.close()


class Respaldos(unittest.TestCase):
    def test_backup_y_restauracion(self):
        c = as_user("admin")
        c.post("/configuracion/respaldos/crear", page="/configuracion/respaldos")
        files = [f for f in os.listdir(APP.config["BACKUP_DIR"]) if f.startswith("respaldo_")]
        self.assertTrue(files)
        n0 = scalar("SELECT COUNT(*) FROM alumnos")
        c.post("/alumnos/nuevo", {"legajo": "POST-BK", "apellido": "Posterior", "nombre": "Backup", "dni": "39999111", "curso_id": 6, "ciclo_id": 1,
                                  "estado_academico": "Regular"}, page="/alumnos/nuevo")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos"), n0 + 1)
        r = c.post("/configuracion/respaldos/restaurar", {"nombre": files[0], "confirmar": "no"}, page="/configuracion/respaldos")
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos"), n0 + 1, "sin confirmación no restaura")
        r = c.post("/configuracion/respaldos/restaurar", {"nombre": files[0], "confirmar": "RESTAURAR"}, page="/configuracion/respaldos")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(scalar("SELECT COUNT(*) FROM alumnos"), n0, "volvió al estado del respaldo")
        self.assertTrue([f for f in os.listdir(APP.config["BACKUP_DIR"]) if f.startswith("antes_de_restaurar")])
        # path traversal en descarga
        c2 = as_user("admin")
        self.assertEqual(c2.get("/configuracion/respaldos/..%2Fsecret.key/descargar").status_code, 404)
        r = c2.post("/configuracion/respaldos/restaurar", {"confirmar": "RESTAURAR"}, page="/configuracion/respaldos")
        self.assertEqual(r.status_code, 302)

    def test_zip_invalido(self):
        c = as_user("admin")
        c.post("/configuracion/respaldos/restaurar", {"archivo": (io.BytesIO(b"no es zip"), "x.zip"), "confirmar": "RESTAURAR"},
               page="/configuracion/respaldos", content_type="multipart/form-data")
        self.assertGreater(scalar("SELECT COUNT(*) FROM alumnos"), 0)


class ReferenteEnSeguimiento(unittest.TestCase):
    """Visitas y observaciones: se puede indicar el referente de la empresa de la práctica."""

    def test_referente_en_visitas(self):
        con = db()
        try:
            p = con.execute("SELECT p.id, p.empresa_id FROM practicas p WHERE p.eliminado_en IS NULL AND EXISTS "
                            "(SELECT 1 FROM referentes_empresas r WHERE r.empresa_id = p.empresa_id AND r.eliminado_en IS NULL AND r.activo = 1) "
                            "LIMIT 1").fetchone()
            ref = con.execute("SELECT id, nombre FROM referentes_empresas WHERE empresa_id = ? AND eliminado_en IS NULL AND activo = 1",
                              (p[1],)).fetchone()
            otro = con.execute("SELECT id, nombre FROM referentes_empresas WHERE empresa_id <> ? AND eliminado_en IS NULL LIMIT 1",
                               (p[1],)).fetchone()
        finally:
            con.close()
        adm = as_user("admin")
        form_url = f"/seguimiento/visitas/nuevo?practica_id={p[0]}"
        html = adm.get(form_url).get_data(as_text=True)
        self.assertIn("Referente de la empresa", html)
        self.assertIn(ref[1], html)
        self.assertNotIn(otro[1], html)  # sólo los referentes de la empresa de la práctica
        datos = {"practica_id": p[0], "fecha": "2026-11-10", "tipo": "Visita", "descripcion": "Visita con referente"}
        adm.post("/seguimiento/visitas/nuevo", dict(datos, referente_id=otro[0]), page=form_url)
        self.assertIsNone(scalar("SELECT id FROM seguimiento WHERE descripcion = 'Visita con referente'"))
        adm.post("/seguimiento/visitas/nuevo", dict(datos, referente_id=ref[0]), page=form_url)
        self.assertEqual(scalar("SELECT referente_id FROM seguimiento WHERE descripcion = 'Visita con referente'"), ref[0])
        ficha = adm.get(f"/practicas/{p[0]}").get_data(as_text=True)
        self.assertIn(f"Referente:</span> {ref[1]}", ficha)

    def test_migracion_agrega_columna(self):
        import sqlite3
        import tempfile
        from app import db as dbmod
        ruta = os.path.join(tempfile.mkdtemp(), "vieja.db")
        dbmod.init_db(ruta)
        con = sqlite3.connect(ruta)
        con.execute("ALTER TABLE seguimiento DROP COLUMN referente_id")
        con.commit()
        con.close()
        dbmod.migrate(ruta)
        dbmod.migrate(ruta)  # idempotente
        con = sqlite3.connect(ruta)
        self.assertIn("referente_id", [r[1] for r in con.execute("PRAGMA table_info(seguimiento)")])
        con.close()


class ConsultaPublica(unittest.TestCase):
    """Acceso libre sin usuario: sólo lectura de Seguimiento y asistencia, sin datos personales."""

    def test_acceso_sin_usuario_y_sin_datos_personales(self):
        c = Client()
        self.assertIn("/consulta/", c.get("/login").get_data(as_text=True))
        r = c.get("/consulta/?ciclo_id=")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Sólo consulta", html)
        self.assertIn("<th>Inicio</th><th>Fin</th>", html)
        con = db()
        try:
            alumno = con.execute("SELECT a.apellido, a.dni, a.legajo FROM alumnos a JOIN alumnos_practicas ap ON ap.alumno_id = a.id "
                                 "WHERE ap.eliminado_en IS NULL AND a.eliminado_en IS NULL LIMIT 1").fetchone()
            dnis = [row[0] for row in con.execute("SELECT dni FROM alumnos WHERE dni IS NOT NULL AND dni <> ''")]
        finally:
            con.close()
        self.assertIn(alumno[0], html)
        for dni in dnis:
            self.assertNotIn(dni, html)
        for ruta in ("/alumnos/", "/practicas/", "/seguimiento/ap/", "/empresas/", "/documentos/", "/logout", "_csrf"):
            self.assertNotIn(ruta, html)
        # no se puede buscar por DNI
        self.assertNotIn(alumno[0], c.get(f"/consulta/?ciclo_id=&q={alumno[1]}").get_data(as_text=True))

    def test_no_permite_modificar_ni_salir_de_la_consulta(self):
        c = Client()
        c.get("/consulta/")
        for metodo in (c.c.post, c.c.put, c.c.delete):
            self.assertIn(metodo("/consulta/").status_code, (400, 405))
        for url in ("/", "/seguimiento/", "/alumnos/", "/alumnos/1", "/seguimiento/ap/1", "/seguimiento/asistencias/nuevo", "/buscar?q=a"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])

    def test_se_puede_deshabilitar(self):
        adm = as_user("admin")
        datos = {k: scalar("SELECT valor FROM configuracion WHERE clave = ?", (k,)) or "" for k in
                 ("escuela_nombre", "escuela_cue", "escuela_direccion", "escuela_localidad", "escuela_telefono", "escuela_email",
                  "nota_aprobacion", "dias_alerta_vencimiento", "umbral_ritmo")}
        adm.post("/configuracion/", datos, page="/configuracion/")  # sin la casilla: deshabilita
        c = Client()
        self.assertEqual(c.get("/consulta/").status_code, 404)
        self.assertNotIn("/consulta/", c.get("/login").get_data(as_text=True))
        adm.post("/configuracion/", dict(datos, consulta_publica="1"), page="/configuracion/")
        self.assertEqual(Client().get("/consulta/").status_code, 200)

    def test_practica_nueva_se_ve_al_instante(self):
        con = db()
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(practicas)") if r[1] != "id"]
            fila = dict(zip(cols, con.execute(f"SELECT {', '.join(cols)} FROM practicas LIMIT 1").fetchone()))
            fila.update(numero="PP-PUBLICA-0001", eliminado_en=None, eliminado_por=None)
            con.execute(f"INSERT INTO practicas ({', '.join(fila)}) VALUES ({', '.join('?' * len(fila))})", list(fila.values()))
            con.commit()
            pid = con.execute("SELECT id FROM practicas WHERE numero = 'PP-PUBLICA-0001'").fetchone()[0]
        finally:
            con.close()
        html = Client().get("/consulta/?ciclo_id=").get_data(as_text=True)
        self.assertIn("PP-PUBLICA-0001", html)
        self.assertIn("Sin alumnos asignados", html)
        con = db()
        try:
            alumno_id = con.execute("SELECT id FROM alumnos WHERE eliminado_en IS NULL LIMIT 1").fetchone()[0]
            con.execute("INSERT INTO alumnos_practicas (practica_id, alumno_id, estado, horas_requeridas) VALUES (?, ?, ?, 200)",
                        (pid, alumno_id, "Pendiente de asignación"))
            con.commit()
        finally:
            con.close()
        html = Client().get("/consulta/?ciclo_id=").get_data(as_text=True)
        self.assertIn("PP-PUBLICA-0001", html)

    def test_fechas_en_seguimiento(self):
        html = as_user("admin").get("/seguimiento/?ciclo_id=").get_data(as_text=True)
        self.assertIn("<th>Inicio</th><th>Fin</th>", html)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
