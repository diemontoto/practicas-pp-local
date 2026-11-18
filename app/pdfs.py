"""Generación de PDF (reportlab): fichas, informes finales y reportes tabulares con encabezado institucional."""
import io
from datetime import datetime
from xml.sax.saxutils import escape

from flask import g
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import services
from .db import all_config, q, q1
from .filters import fecha, fechahora, num

NAVY = colors.HexColor("#1e3a5f")
GRID = colors.HexColor("#c9d0db")
STRIPE = colors.HexColor("#f4f6fa")

_base = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_base["Title"], fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, alignment=0, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=_base["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("B", parent=_base["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
SMALL = ParagraphStyle("S", parent=BODY, fontSize=8, leading=10)
CELL = ParagraphStyle("C", parent=BODY, fontSize=8, leading=9.5)
CELLH = ParagraphStyle("CH", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
MUTED = ParagraphStyle("M", parent=SMALL, textColor=colors.HexColor("#6b7280"))


def P(text, style=BODY):
    return Paragraph(escape("" if text is None else str(text)).replace("\n", "<br/>"), style)


class NumberedCanvas(rl_canvas.Canvas):
    """Canvas que conoce el total de páginas para 'Página X de Y'."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#6b7280"))
            w, _h = self._pagesize
            self.drawRightString(w - 15 * mm, 9 * mm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def render(title, story, landscape_mode=False):
    cfg = all_config()
    buf = io.BytesIO()
    size = landscape(A4) if landscape_mode else A4
    emitido = datetime.now().strftime("%d/%m/%Y %H:%M")
    usuario = g.user["nombre"] if getattr(g, "user", None) else ""

    def header_footer(c, doc):
        w, h = size
        c.saveState()
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(15 * mm, h - 14 * mm, cfg.get("escuela_nombre") or "Escuela Técnica")
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#4b5563"))
        linea = " · ".join(x for x in (
            f"CUE {cfg['escuela_cue']}" if cfg.get("escuela_cue") else "", cfg.get("escuela_direccion"),
            cfg.get("escuela_localidad"), f"Tel. {cfg['escuela_telefono']}" if cfg.get("escuela_telefono") else "",
            cfg.get("escuela_email")) if x)
        if linea:
            c.drawString(15 * mm, h - 18.5 * mm, linea)
        c.drawString(15 * mm, h - 22.5 * mm, "Prácticas Profesionalizantes")
        c.drawRightString(w - 15 * mm, h - 14 * mm, f"Fecha de emisión: {emitido}")
        c.drawRightString(w - 15 * mm, h - 18.5 * mm, f"Emitido por: {usuario}")
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.line(15 * mm, h - 25 * mm, w - 15 * mm, h - 25 * mm)
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6b7280"))
        c.drawString(15 * mm, 9 * mm, "Documento generado por el Sistema de Gestión de Prácticas Profesionalizantes")
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=30 * mm,
                            bottomMargin=16 * mm, title=title, author=cfg.get("escuela_nombre") or "")
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer, canvasmaker=NumberedCanvas)
    return buf.getvalue()


def kv_table(pairs, cols=2, width=180 * mm):
    """Tabla de pares etiqueta/valor distribuida en `cols` columnas."""
    rows = []
    for i in range(0, len(pairs), cols):
        row = []
        for k, v in pairs[i:i + cols]:
            row += [P(k, ParagraphStyle("k", parent=SMALL, textColor=colors.HexColor("#6b7280"))), P(v if v not in (None, "") else "—")]
        while len(row) < cols * 2:
            row += ["", ""]
        rows.append(row)
    cw = [width * 0.17, width * 0.33] * cols if cols == 2 else [width / (cols * 2)] * (cols * 2)
    t = Table(rows, colWidths=cw)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID)]))
    return t


def data_table(headers, rows, widths=None, total_width=180 * mm):
    data = [[Paragraph(escape(h), CELLH) for h in headers]]
    for r in rows:
        data.append([Paragraph(escape("" if c is None else str(c)), CELL) for c in r])
    if widths:
        s = float(sum(widths))
        cw = [total_width * w / s for w in widths]
    else:
        cw = [total_width / len(headers)] * len(headers)
    t = Table(data, colWidths=cw, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, GRID),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), STRIPE))
    t.setStyle(TableStyle(style))
    return t


# ----------------------------------------------------------------- reportes
def reporte_tabla(titulo, columnas, filas, filtros, apaisado=True, resumen=None):
    """columnas: [(encabezado, peso)]; filas: [[str,...]]; filtros: [(nombre, valor)]"""
    page_w = (297 if apaisado else 210) * mm - 30 * mm
    story = [P(titulo, H1)]
    if filtros:
        story.append(P("Filtros aplicados: " + " · ".join(f"{k}: {v}" for k, v in filtros), MUTED))
    else:
        story.append(P("Filtros aplicados: ninguno (todos los registros)", MUTED))
    story.append(Spacer(1, 4 * mm))
    story.append(data_table([c[0] for c in columnas], filas, [c[1] for c in columnas], page_w))
    story.append(Spacer(1, 3 * mm))
    story.append(P(f"Total de registros: {len(filas)}", SMALL))
    for linea in resumen or []:
        story.append(P(linea, SMALL))
    return render(titulo, story, apaisado)


# ------------------------------------------------------------ ficha alumno
def ficha_alumno(alumno_id):
    a = q1("""SELECT a.*, c.nombre AS curso, d.nombre AS division, e.nombre AS especialidad, ci.anio AS ciclo
              FROM alumnos a JOIN cursos c ON c.id = a.curso_id LEFT JOIN divisiones d ON d.id = a.division_id
              LEFT JOIN especialidades e ON e.id = a.especialidad_id LEFT JOIN ciclos_lectivos ci ON ci.id = a.ciclo_id
              WHERE a.id = ? AND a.eliminado_en IS NULL""", (alumno_id,))
    if not a:
        return None
    e = services.edad(a["fecha_nacimiento"])
    story = [P("Ficha individual del alumno", H1), P(f"{a['apellido']}, {a['nombre']}", H2),
             P("Datos personales", H2),
             kv_table([("Legajo", a["legajo"]), ("DNI", a["dni"]), ("Fecha de nacimiento", fecha(a["fecha_nacimiento"]) + (f" ({e} años)" if e is not None else "")),
                       ("Teléfono", a["telefono"]), ("Domicilio", a["domicilio"]), ("Correo electrónico", a["email"])]),
             P("Datos académicos", H2),
             kv_table([("Curso", a["curso"]), ("División", a["division"]), ("Especialidad", a["especialidad"]),
                       ("Año de cursada", a["ciclo"]), ("Estado académico", a["estado_academico"]), ("Observaciones", a["observaciones"])]),
             P("Responsable / tutor legal", H2),
             kv_table([("Nombre", a["tutor_legal_nombre"]), ("Parentesco", a["tutor_legal_parentesco"]),
                       ("Teléfono", a["tutor_legal_telefono"]), ("Correo electrónico", a["tutor_legal_email"])]),
             P("Historial de prácticas profesionalizantes", H2)]
    aps = services.ap_rows({"alumno_id": alumno_id}, order="ci.anio DESC, p.fecha_inicio DESC", scoped=False)
    if aps:
        story.append(data_table(
            ["Año", "N° práctica", "Empresa", "Estado", "Horas", "Docs.", "Resultado"],
            [[r["anio"], r["numero"], r["empresa"], r["estado"], f"{num(r['horas_realizadas'])} / {r['horas_requeridas']}",
              r["docs_estado"], (f"{r['resultado']} ({num(r['calificacion_final'])})" if r["resultado"] else "—")] for r in aps],
            [0.6, 1.3, 2.4, 1.8, 1.1, 1.5, 1.4]))
    else:
        story.append(P("El alumno no registra prácticas profesionalizantes.", MUTED))
    return render(f"Ficha de {a['apellido']}, {a['nombre']}", story)


# -------------------------------------------------- informe final evaluación
def informe_evaluacion(ap_id):
    d = services.evaluacion_data(ap_id, scoped=False)
    if not d:
        return None
    ap, evals, final, ref = d["ap"], d["evals"], d["final"], d["referente"]
    story = [P("Informe final de evaluación", H1), P("Práctica Profesionalizante " + ap["numero"], H2),
             kv_table([("Alumno", ap["alumno"]), ("DNI", ap["dni"]), ("Legajo", ap["legajo"]), ("Curso", f"{ap['curso']} {ap['division'] or ''}"),
                       ("Especialidad", ap["especialidad"]), ("Ciclo lectivo", ap["anio"]),
                       ("Empresa / institución", ap["empresa"]), ("Proyecto", ap["proyecto"]),
                       ("Docente tutor", ap["tutor"]), ("Referente de la empresa", ref["nombre"] if ref else None),
                       ("Inicio", fecha(ap["ap_inicio"] or ap["fecha_inicio"])), ("Finalización", fecha(ap["ap_fin"]))]),
             P("Cumplimiento de horas", H2),
             kv_table([("Horas requeridas", ap["horas_requeridas"]), ("Horas cumplidas", num(ap["horas_realizadas"])),
                       ("Cumplimiento", f"{num(ap['porcentaje'])} %"), ("Horas descontadas por inasistencias", num(ap["horas_descontadas"])),
                       ("Días de práctica", ap["dias_programados"]), ("Faltas injustificadas", ap["faltas_injust"]),
                       ("Días asistidos", ap["dias_presente"]), ("Faltas justificadas", ap["faltas_just"])]),
             P("Evaluación por criterios (escala 1 a 10)", H2)]
    t, r = evals.get("Tutor"), evals.get("Referente")
    rows = [[lbl, t[k] if t else "—", r[k] if r else "—"] for k, lbl in services.CRITERIOS]
    rows.append(["Promedio", num(t["promedio"], 2) if t else "—", num(r["promedio"], 2) if r else "—"])
    story.append(data_table(["Criterio", "Docente tutor" + (f" ({t['evaluador_nombre']})" if t and t["evaluador_nombre"] else ""),
                             "Referente de la empresa" + (f" ({r['evaluador_nombre']})" if r and r["evaluador_nombre"] else "")],
                            rows, [3, 2, 2]))
    for who, ev in (("Observaciones del docente tutor", t), ("Observaciones del referente de la empresa", r)):
        if ev and ev["observaciones"]:
            story += [P(who, H2), P(ev["observaciones"])]
    story.append(P("Resultado final", H2))
    story.append(kv_table([("Calificación final", num(final["calificacion_final"], 2) if final["calificacion_final"] is not None else "—"),
                           ("Resultado", final["resultado"] or "Sin definir"), ("Fecha", fecha(final["fecha_resultado"])),
                           ("Observaciones finales", final["observaciones_finales"])]))
    firmas = Table([["", "", "", ""], ["Docente tutor", "Referente de la empresa", "Coordinación de PP", "Dirección"]],
                   colWidths=[45 * mm] * 4, rowHeights=[22 * mm, 6 * mm])
    firmas.setStyle(TableStyle([("LINEABOVE", (0, 1), (-1, 1), 0.6, colors.black), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                ("FONTSIZE", (0, 1), (-1, 1), 8), ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#4b5563"))]))
    story += [Spacer(1, 6 * mm), KeepTogether([firmas])]
    return render(f"Informe de evaluación {ap['numero']}", story)
