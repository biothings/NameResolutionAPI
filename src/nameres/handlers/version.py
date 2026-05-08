from nameres.handlers.base import NameResolutionBaseHandler
from nameres.version import get_version


class VersionHandler(NameResolutionBaseHandler):
    name = "version"

    async def get(self, *args, **kwargs):
        self.write({"version": get_version()})
