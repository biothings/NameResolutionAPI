"""Shared handler behavior for NameResolution API endpoints."""

import json

from biothings.web.handlers import BaseHandler


class NameResolutionBaseHandler(BaseHandler):
    """Base handler that keeps the lightweight BioThings handler plus CORS."""

    cors_methods = "GET, POST, OPTIONS"
    cors_max_age = "600"

    def set_default_headers(self):
        origin = self.request.headers.get("Origin")
        if origin is None:
            return

        requested_headers = self.request.headers.get("Access-Control-Request-Headers")

        self.set_header("Access-Control-Allow-Origin", origin)
        self.set_header("Access-Control-Allow-Credentials", "true")
        self.set_header("Access-Control-Allow-Methods", self.cors_methods)
        self.set_header("Access-Control-Allow-Headers", requested_headers or "*")
        self.set_header("Access-Control-Max-Age", self.cors_max_age)
        self.set_header("Vary", "Origin")

    def options(self, *args, **kwargs):
        self.finish()

    def finish_json(self, response: dict | list) -> None:
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        super().finish(json.dumps(response))
