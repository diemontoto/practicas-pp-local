"""Sistema de Gestión de Prácticas Profesionalizantes - fábrica de la aplicación."""
import os
import secrets
from datetime import timedelta

from flask import Flask, g, render_template, request

from . import auth, db, filters, services

__version__ = "1.1.0"


def _secret_key(instance_path):
    env = os.environ.get("PP_SECRET_KEY")
    if env:
        return env
    path = os.path.join(instance_path, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path) as fh:
        return fh.read().strip()


def create_app(config=None):
    app = Flask(__name__, instance_path=os.environ.get("PP_INSTANCE", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")))
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        SECRET_KEY=_secret_key(app.instance_path),
        DATABASE=os.path.join(app.instance_path, "practicas.db"),
        UPLOAD_DIR=os.path.join(app.instance_path, "uploads"),
        BACKUP_DIR=os.path.join(app.instance_path, "backups"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("PP_HTTPS", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        JSON_AS_ASCII=False,
    )
    if config:
        app.config.update(config)
    for d in (app.config["UPLOAD_DIR"], app.config["BACKUP_DIR"]):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(app.config["DATABASE"]):
        db.init_db(app.config["DATABASE"])
        from .bootstrap import init_base
        con = db.connect(app.config["DATABASE"])
        init_base(con)
        con.close()
    db.migrate(app.config["DATABASE"])

    db.init_app(app)
    filters.init_app(app)
    auth.init_app(app)

    from .views import (admin, alumnos, catalogos, consulta, docentes, documentos, empresas, evaluaciones, informes,
                        main, practicas, seguimiento)
    for mod in (main, catalogos, alumnos, empresas, docentes, practicas, seguimiento, documentos,
                evaluaciones, informes, admin, consulta):
        for bp in mod.blueprints():
            app.register_blueprint(bp)

    @app.context_processor
    def inject():
        publico = request.endpoint in ("auth.login", "consulta.inicio")
        cfg = db.all_config() if getattr(g, "user", None) or publico else {}
        return dict(escuela=cfg, version=__version__, ciclo_activo=services.ciclo_activo() if getattr(g, "user", None) else None,
                    alert_count=_alert_count,
                    consulta_publica=publico and cfg.get("consulta_publica", "1") == "1")

    def _alert_count():
        if not getattr(g, "user", None) or not auth.can("practicas.ver"):
            return 0
        if not hasattr(g, "_alert_n"):
            ciclo = services.ciclo_activo()
            a = services.alertas({"ciclo_id": ciclo["id"]} if ciclo else {})
            g._alert_n = sum(len(v) for k, v in a.items() if k != "completas") + len(a["completas"])
        return g._alert_n

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if app.config["SESSION_COOKIE_SECURE"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def _error(code, titulo, msg):
        return render_template("error.html", code=code, titulo=titulo, msg=msg), code

    @app.errorhandler(400)
    def e400(e):
        return _error(400, "Solicitud inválida", getattr(e, "description", "") or "La solicitud no pudo procesarse.")

    @app.errorhandler(403)
    def e403(e):
        return _error(403, "Acceso denegado", "Su perfil no tiene permisos para realizar esta acción.")

    @app.errorhandler(404)
    def e404(e):
        return _error(404, "No encontrado", "El registro o la página solicitada no existe.")

    @app.errorhandler(413)
    def e413(e):
        return _error(413, "Archivo demasiado grande", "El archivo supera el tamaño máximo permitido (25 MB).")

    @app.errorhandler(500)
    def e500(e):
        return _error(500, "Error interno", "Ocurrió un error inesperado. El incidente quedó registrado.")

    return app
