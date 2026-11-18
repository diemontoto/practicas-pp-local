"""Copias de seguridad y recuperación: base de datos + documentos adjuntos en un único ZIP."""
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.zip$")


def _dirs(app):
    return app.config["DATABASE"], app.config["UPLOAD_DIR"], app.config["BACKUP_DIR"]


def create_backup(app, prefix="respaldo"):
    """Genera un ZIP consistente (usa la API de backup de SQLite, seguro con la base en uso). Devuelve el nombre."""
    db_path, upload_dir, backup_dir = _dirs(app)
    os.makedirs(backup_dir, exist_ok=True)
    name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    target = os.path.join(backup_dir, name)
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "practicas.db")
        src, dst = sqlite3.connect(db_path), sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "practicas.db")
            n = 0
            for fn in sorted(os.listdir(upload_dir)):
                fp = os.path.join(upload_dir, fn)
                if os.path.isfile(fp):
                    z.write(fp, f"uploads/{fn}")
                    n += 1
            z.writestr("manifest.json", json.dumps({"app": "practicas-pp", "fecha": datetime.now().isoformat(timespec="seconds"),
                                                    "documentos": n}, ensure_ascii=False))
    return name


def list_backups(app):
    _, _, backup_dir = _dirs(app)
    out = []
    if os.path.isdir(backup_dir):
        for fn in os.listdir(backup_dir):
            fp = os.path.join(backup_dir, fn)
            if NAME_RE.match(fn) and os.path.isfile(fp):
                st = os.stat(fp)
                out.append({"nombre": fn, "tamano": st.st_size, "fecha": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return sorted(out, key=lambda x: x["fecha"], reverse=True)


def prune(app, keep=30):
    for b in list_backups(app)[keep:]:
        os.remove(os.path.join(app.config["BACKUP_DIR"], b["nombre"]))


def restore_backup(app, zip_source):
    """Restaura desde un ZIP (ruta o archivo en memoria). Antes crea un respaldo automático del estado actual."""
    db_path, upload_dir, _ = _dirs(app)
    with zipfile.ZipFile(zip_source) as z:
        names = z.namelist()
        if "practicas.db" not in names or "manifest.json" not in names:
            raise ValueError("El archivo no es un respaldo válido de este sistema.")
        for n in names:
            if n.startswith("/") or ".." in n.split("/"):
                raise ValueError("El respaldo contiene rutas no permitidas.")
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "practicas.db")
            with z.open("practicas.db") as fin, open(snap, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            chk = sqlite3.connect(snap)
            try:
                ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                tablas = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            except sqlite3.DatabaseError:
                ok, tablas = False, set()
            if not ok or not {"usuarios", "alumnos", "practicas", "historial_cambios"} <= tablas:
                chk.close()
                raise ValueError("La base de datos del respaldo está dañada o no corresponde a este sistema.")
            create_backup(app, prefix="antes_de_restaurar")
            dst = sqlite3.connect(db_path)
            with dst:
                chk.backup(dst)
            dst.close()
            chk.close()
            from .db import migrate
            migrate(db_path)        # un respaldo anterior puede traer el esquema viejo
        for n in names:
            if n.startswith("uploads/") and not n.endswith("/"):
                base = os.path.basename(n)
                if re.fullmatch(r"[0-9a-f]{32}\.[a-z]{3,4}", base):
                    with z.open(n) as fin, open(os.path.join(upload_dir, base), "wb") as fout:
                        shutil.copyfileobj(fin, fout)
