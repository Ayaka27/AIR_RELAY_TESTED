"""Flask application factory hosting the dashboard and API."""

from __future__ import annotations

import secrets

from flask import Flask, jsonify, render_template

from ..util.logger import get_logger
from .api import api
from .context import AppContext
from .security import TokenStore

log = get_logger("web")


def create_app(context: AppContext) -> Flask:
    paths = context.paths
    app = Flask(
        __name__,
        template_folder=str(paths.dashboard_dir / "templates"),
        static_folder=str(paths.dashboard_dir / "static"),
    )
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.config["ctx"] = context
    context.auth = TokenStore(paths.secrets_file)
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "name": "airrelay"})

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"ok": False, "error": "not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        log.exception("server error")
        return jsonify({"ok": False, "error": "internal error"}), 500

    return app
