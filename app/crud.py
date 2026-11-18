"""CRUD genérico y declarativo: listado con búsqueda/filtros/paginación, alta, edición,
baja lógica, restauración, exportación a Excel y auditoría de cada operación."""
import io
import re
import sqlite3
from datetime import date
from urllib.parse import urlencode

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for)
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .auth import can, perm_required, safe_next
from .db import audit, commit, diff, ex, norm, now, q, q1, get_db
from .filters import plain

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def F(name, label=None, type="text", **kw):
    """Define un campo de formulario."""
    d = dict(name=name, label=label or name.replace("_", " ").capitalize(), type=type, required=False,
             choices=None, default=None, unique=False, help="", cols=1, maxlen=None, min=None, max=None,
             coerce=None, readonly=False, virtual=False, decimal=False, attrs=None, placeholder="")
    d.update(kw)
    return d


def C(key, label, kind="text", link=False):
    """Define una columna de listado."""
    return dict(key=key, label=label, kind=kind, link=link)


def valid_cuit(digits):
    if not re.fullmatch(r"\d{11}", digits):
        return False
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(d) * p for d, p in zip(digits[:10], pesos))
    dv = 11 - (s % 11)
    dv = 0 if dv == 11 else dv
    return dv != 10 and dv == int(digits[10])


def resolve_choices(f):
    ch = f["choices"]
    if callable(ch):
        ch = ch()
    out = []
    for item in ch or []:
        item = tuple(item)
        out.append(item if len(item) == 3 else (item[0], item[1], None))
    return out


def parse_field(f, form, errors):
    name, t = f["name"], f["type"]
    if t == "checkbox":
        return 1 if form.get(name) in ("1", "on", "true") else 0
    if t == "weekdays":
        dias = sorted({int(x) for x in form.getlist(name) if x in ("1", "2", "3", "4", "5", "6", "7")})
        if not dias:
            errors[name] = "Marque al menos un día."
            return None
        return ",".join(map(str, dias))
    raw = (form.get(name) or "").strip()
    if raw == "":
        if f["required"]:
            errors[name] = "Campo obligatorio."
        return None
    if t == "password":
        return raw
    if t in ("text", "textarea", "tel"):
        limit = f["maxlen"] or (4000 if t == "textarea" else 200)
        if len(raw) > limit:
            errors[name] = f"Máximo {limit} caracteres."
        return raw
    if t == "email":
        raw = raw.lower()
        if not EMAIL_RE.match(raw) or len(raw) > 200:
            errors[name] = "Correo electrónico inválido."
        return raw
    if t == "dni":
        d = re.sub(r"[.\s-]", "", raw)
        if not re.fullmatch(r"\d{7,9}", d):
            errors[name] = "DNI inválido (7 a 9 dígitos)."
        return d
    if t == "cuit":
        d = re.sub(r"\D", "", raw)
        if not valid_cuit(d):
            errors[name] = "CUIT inválido (verifique los 11 dígitos)."
            return raw
        return f"{d[:2]}-{d[2:10]}-{d[10]}"
    if t == "date":
        try:
            date.fromisoformat(raw)
        except ValueError:
            errors[name] = "Fecha inválida."
        return raw
    if t == "time":
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", raw):
            errors[name] = "Hora inválida (HH:MM)."
        return raw
    if t == "number":
        try:
            v = float(raw.replace(",", "."))
            if not f["decimal"]:
                if v != int(v):
                    raise ValueError
                v = int(v)
        except ValueError:
            errors[name] = "Número inválido."
            return None
        if f["min"] is not None and v < f["min"]:
            errors[name] = f"Debe ser mayor o igual a {f['min']}."
        if f["max"] is not None and v > f["max"]:
            errors[name] = f"Debe ser menor o igual a {f['max']}."
        return v
    if t in ("select", "hidden"):
        if t == "select":
            valid = {str(c[0]) for c in resolve_choices(f)}
            if raw not in valid:
                errors[name] = "Opción inválida."
                return None
        if f["coerce"]:
            try:
                return f["coerce"](raw)
            except ValueError:
                errors[name] = "Valor inválido."
                return None
        return raw
    return raw


class Entity:
    def __init__(self, slug, table, singular, plural, module, fields, columns, select=None, search=(),
                 filters=(), order="t.id DESC", soft=True, listable=True, detail=False,
                 before_save=None, after_save=None, deps=(), scope=None, access=None, form_extra=None,
                 per_page=25, nuevo_label=None, export=True, subtitle="", list_extra=None, on_delete=None):
        self.slug, self.table, self.singular, self.plural, self.module = slug, table, singular, plural, module
        self.fields = fields
        self.columns = columns
        self.select = select or f"SELECT t.* FROM {table} t"
        self.search = search
        self.filters = filters
        self.order = order
        self.soft = soft
        self.listable = listable
        self.detail = detail
        self.before_save = before_save
        self.after_save = after_save
        self.deps = deps
        self.scope = scope
        self.access = access
        self.form_extra = form_extra
        self.per_page = per_page
        self.nuevo_label = nuevo_label or f"Nuevo {singular.lower()}"
        self.export = export
        self.subtitle = subtitle
        self.list_extra = list_extra
        self.on_delete = on_delete

    # ------------------------------------------------------------ helpers
    def get_fields(self, row=None):
        return self.fields(row) if callable(self.fields) else self.fields

    def get(self, id, allow_deleted=False):
        row = q1(f"SELECT * FROM {self.table} WHERE id = ?", (id,))
        if not row or (self.soft and row["eliminado_en"] and not allow_deleted):
            abort(404)
        if self.access:
            self.access(id)
        return row

    def after_url(self, id=None):
        nxt = safe_next(request.values.get("next"))
        if nxt:
            return nxt
        if self.detail and id:
            return url_for(f"{self.slug}.ficha", id=id)
        return url_for(f"{self.slug}.lista") if self.listable else url_for("main.inicio")

    # ------------------------------------------------------------- listado
    def _query(self, paginate=True):
        args = request.args
        where, params = [], []
        if self.soft:
            if args.get("baja") == "1" and can(f"{self.module}.eliminar"):
                where.append("t.eliminado_en IS NOT NULL")
            else:
                where.append("t.eliminado_en IS NULL")
        term = (args.get("q") or "").strip()
        if term and self.search:
            for w in norm(term).split():
                ors = " OR ".join(f"norm(COALESCE({e}, '')) LIKE ?" for e in self.search)
                where.append(f"({ors})")
                params += [f"%{w}%"] * len(self.search)
        active_filters = []
        for flt in self.filters:
            val = args.get(flt["name"], "")
            active_filters.append(dict(flt, value=val, options=[(c[0], c[1]) for c in resolve_choices(flt)]))
            if val != "":
                where.append(flt["sql"] if flt.get("sql") else f"{flt['expr']} = ?")
                params.append(int(val) if val.isdigit() else val)
        if self.scope:
            sql, sp = self.scope()
            where.append(sql)
            params += sp
        base = f"{self.select} WHERE {' AND '.join(where) if where else '1=1'}"
        total = q1(f"SELECT COUNT(*) AS n FROM ({base})", params)["n"]
        sql = f"{base} ORDER BY {self.order}"
        page = 1
        if paginate:
            page = max(1, int(args.get("page", 1)) if args.get("page", "1").isdigit() else 1)
            sql += " LIMIT ? OFFSET ?"
            params = params + [self.per_page, (page - 1) * self.per_page]
        return q(sql, params), total, page, active_filters, term

    def lista(self):
        if request.args.get("exportar") == "xlsx" and self.export:
            return self.exportar()
        rows, total, page, flts, term = self._query()
        pages = max(1, -(-total // self.per_page))
        extra = self.list_extra() if self.list_extra else {}
        args = request.args.to_dict()
        args.pop("page", None)
        args["exportar"] = "xlsx"
        extra["export_url"] = "?" + urlencode(args)
        return render_template("crud/list.html", e=self, rows=rows, total=total, page=page, pages=pages,
                               filters=flts, term=term, baja=request.args.get("baja") == "1", **extra)

    def exportar(self):
        rows, total, _p, flts, term = self._query(paginate=False)
        wb = Workbook()
        ws = wb.active
        ws.title = self.plural[:30]
        ws.append([c["label"] for c in self.columns])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1E3A5F")
        for r in rows:
            ws.append([plain(r[c["key"]], c["kind"]) for c in self.columns])
        for i, c in enumerate(self.columns, 1):
            width = max([len(str(c["label"]))] + [len(plain(r[c["key"]], c["kind"])) for r in rows[:200]]) + 2
            ws.column_dimensions[get_column_letter(i)].width = min(width, 50)
        ws.freeze_panes = "A2"
        buf = io.BytesIO()
        wb.save(buf)
        audit(self.table, None, "exportar", {"filas": total})
        commit()
        return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{self.slug}.xlsx"'})

    # ------------------------------------------------------------ formulario
    def form(self, id=None):
        row = self.get(id) if id else None
        fields = [dict(f) for f in self.get_fields(row)]
        for f in fields:
            if f["type"] in ("select",):
                f["options"] = resolve_choices(f)
        errors, values = {}, {}
        if request.method == "POST":
            vals = {}
            for f in fields:
                if f["readonly"]:
                    continue
                vals[f["name"]] = parse_field(f, request.form, errors)
            for f in fields:
                v = vals.get(f["name"])
                if f["unique"] and v is not None and f["name"] not in errors:
                    sql = f"SELECT 1 FROM {self.table} WHERE lower({f['name']}) = lower(?)"
                    prm = [v]
                    if self.soft:
                        sql += " AND eliminado_en IS NULL"
                    if row:
                        sql += " AND id <> ?"
                        prm.append(row["id"])
                    if q1(sql, prm):
                        errors[f["name"]] = "Ya existe un registro con este valor."
            if self.before_save:
                self.before_save(vals, row, errors)
            if not errors:
                try:
                    new_id = self._save(fields, vals, row)
                    return redirect(self.after_url(new_id))
                except sqlite3.IntegrityError as exc:
                    get_db().rollback()
                    flash(f"No se pudo guardar: {self._friendly(exc)}", "danger")
            else:
                flash("Revise los campos marcados en el formulario.", "danger")
            values = {f["name"]: (request.form.get(f["name"]) if f["type"] != "checkbox"
                                  else request.form.get(f["name"]) in ("1", "on", "true")) for f in fields}
            for f in fields:
                if f["type"] == "weekdays":
                    values[f["name"]] = ",".join(request.form.getlist(f["name"]))
        elif row:
            values = dict(row)
        else:
            for f in fields:
                d = f["default"]() if callable(f["default"]) else f["default"]
                values[f["name"]] = request.args.get(f["name"], d) if f["type"] == "hidden" or request.args.get(f["name"]) else d
        form_extra = self.form_extra(row) if callable(self.form_extra) else {}
        return render_template("crud/form.html", e=self, fields=fields, values=values, errors=errors, row=row,
                               next=request.values.get("next", ""), **form_extra)

    @staticmethod
    def _friendly(exc):
        msg = str(exc)
        if "UNIQUE" in msg:
            return "ya existe un registro con esos datos (valor duplicado)."
        if "FOREIGN KEY" in msg:
            return "hace referencia a un registro inexistente."
        if "CHECK" in msg:
            return "algún valor no cumple las restricciones permitidas."
        return msg

    def _save(self, fields, vals, row):
        virtual = {f["name"] for f in fields if f["virtual"]}
        data = {k: v for k, v in vals.items() if k not in virtual}
        data = {k: v for k, v in data.items()}
        safe_log = {k: v for k, v in data.items() if "password" not in k}
        if row is None:
            cols = list(data) + ["creado_en", "creado_por"]
            params = list(data.values()) + [now(), g.user["id"]]
            cur = ex(f"INSERT INTO {self.table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     params, commit=False)
            new_id = cur.lastrowid
            audit(self.table, new_id, "crear", safe_log)
        else:
            new_id = row["id"]
            antes = {k: row[k] for k in data if k in row.keys()}
            cambios = {k: v for k, v in diff(antes, data).items() if "password" not in k}
            sets = [f"{k} = ?" for k in data] + ["modificado_en = ?", "modificado_por = ?"]
            ex(f"UPDATE {self.table} SET {', '.join(sets)} WHERE id = ?",
               list(data.values()) + [now(), g.user["id"], new_id], commit=False)
            if cambios or any("password" in k for k in data):
                audit(self.table, new_id, "modificar", cambios)
        if self.after_save:
            self.after_save(new_id, data, row is None, row)
        commit()
        flash(f"{self.singular} guardado correctamente.", "success")
        return new_id

    # ------------------------------------------------------------ baja/restaurar
    def baja(self, id):
        row = self.get(id)
        for sql, label in self.deps:
            n = q1(sql, (id,))[0]
            if n:
                flash(f"No se puede dar de baja: tiene {n} {label} asociado(s). Quítelos o desactívelos primero.", "danger")
                return redirect(self.after_url())
        if self.on_delete:
            msg = self.on_delete(row)
            if msg:
                flash(msg, "danger")
                return redirect(self.after_url())
        if self.soft:
            ex(f"UPDATE {self.table} SET eliminado_en = ?, eliminado_por = ? WHERE id = ?",
               (now(), g.user["id"], id), commit=False)
        else:
            ex(f"DELETE FROM {self.table} WHERE id = ?", (id,), commit=False)
        audit(self.table, id, "baja", {k: row[k] for k in row.keys() if k in ("nombre", "apellido", "razon_social", "numero", "legajo", "username", "proyecto")})
        commit()
        flash(f"{self.singular} dado de baja.", "success")
        return redirect(self.after_url())

    def restaurar(self, id):
        row = self.get(id, allow_deleted=True)
        if not row["eliminado_en"]:
            return redirect(self.after_url())
        try:
            ex(f"UPDATE {self.table} SET eliminado_en = NULL, eliminado_por = NULL, modificado_en = ?, modificado_por = ? WHERE id = ?",
               (now(), g.user["id"], id), commit=False)
            audit(self.table, id, "restaurar")
            commit()
            flash(f"{self.singular} restaurado.", "success")
        except sqlite3.IntegrityError as exc:
            get_db().rollback()
            flash(f"No se pudo restaurar: {self._friendly(exc)}", "danger")
        return redirect(self.after_url())

    # ------------------------------------------------------------ blueprint
    def blueprint(self, url_prefix=None):
        bp = Blueprint(self.slug, __name__, url_prefix=url_prefix or f"/{self.slug}")
        ver, edit, borrar = f"{self.module}.ver", f"{self.module}.editar", f"{self.module}.eliminar"
        if self.listable:
            bp.add_url_rule("/", "lista", perm_required(ver)(self.lista))
        bp.add_url_rule("/nuevo", "nuevo", perm_required(edit)(lambda: self.form()), methods=["GET", "POST"])
        bp.add_url_rule("/<int:id>/editar", "editar", perm_required(edit)(lambda id: self.form(id)), methods=["GET", "POST"])
        bp.add_url_rule("/<int:id>/baja", "baja", perm_required(borrar)(self.baja), methods=["POST"])
        bp.add_url_rule("/<int:id>/restaurar", "restaurar", perm_required(borrar)(self.restaurar), methods=["POST"])
        return bp
