"""
Main entrypoint for launching the NameResolution web service
"""

import logging

from tornado.options import define, options

from nameres.application import NameResolutionAPI
from nameres.namespace import NameResolutionAPINamespace
from nameres.server import NameResolutionWebServer

logger = logging.getLogger(__name__)


# Command Line Options
# --------------------------

# Web Server Settings
# --------------------------
define("host", default=None, help="web server host ipv4 address")
define("port", default=None, help="web server host ipv4 port")

# Configuration Settings
# --------------------------
define("conf", default=None, help="override configuration file for settings configuration")


def main():
    """
    Entrypoint for the nameresolution api application launcher

    Ported from the biothings.web.launcher

    We only have one "plugin" in this case to load, so we can short-cut some of
    the logic used from the pending.api application that assumes more than one
    """
    options.parse_command_line()
    configuration_namespace = NameResolutionAPINamespace(options)
    application_instance = NameResolutionAPI.get_app(configuration_namespace)
    webserver = NameResolutionWebServer(application_instance, configuration_namespace)
    webserver.start()


if __name__ == "__main__":
    main()
