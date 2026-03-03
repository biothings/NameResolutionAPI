import importlib.resources
from typing import Callable

import tornado.web

import nameres
from nameres.handlers.health import NameResolutionHealthHandler
from nameres.handlers.lookup import NameResolutionLookupHandler, NameResolutionBulkLookupHandler
from nameres.handlers.synonyms import NameResolutionSynonymsHandler
from nameres.handlers.version import VersionHandler


def build_handlers() -> dict[str, tuple[str, Callable]]:
    """Generate our handler mapping for the nameres API."""

    handler_collection = [
        (r"/bulk-lookup?", NameResolutionBulkLookupHandler),
        (r"/lookup?", NameResolutionLookupHandler),
        (r"/status?", NameResolutionHealthHandler),
        (r"/synonyms?", NameResolutionSynonymsHandler),
        (r"/status?", NameResolutionHealthHandler),
        (r"/version", VersionHandler),
    ]
    # build static file frontend
    package_directory = importlib.resources.files(nameres)
    webapp_directory = package_directory.joinpath("webapp")

    # This points to all the assets available to use via the webapp for our swaggerui
    asset_handler = (r"/webapp/(.*)", tornado.web.StaticFileHandler, {"path": str(webapp_directory)})
    handler_collection.append(asset_handler)

    index_handler = (
        r"/()",
        tornado.web.StaticFileHandler,
        {
            "path": str(webapp_directory),
            "default_filename": "index.html",
        },
    )
    handler_collection.append(index_handler)

    # This redirect ensures we default so the favicon icon can be found in the webapp directory
    favicon_handler = (r"/favicon.ico", tornado.web.RedirectHandler, {"url": "/webapp/swaggerui/favicon-32x32.png"})
    handler_collection.append(favicon_handler)

    handlers = {handler[0]: handler for handler in handler_collection}
    return handlers
