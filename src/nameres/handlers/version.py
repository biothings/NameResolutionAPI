import asyncio

from nameres.handlers.base import NameResolutionBaseHandler
from nameres.version import get_version


class VersionHandler(NameResolutionBaseHandler):
    name = "version"

    async def get(self, *args, **kwargs):
        version = await asyncio.to_thread(get_version)
        self.write({"version": version})
