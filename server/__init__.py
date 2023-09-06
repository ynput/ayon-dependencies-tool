from ayon_server.addons import BaseServerAddon
from .version import __version__

from nxtools import logging


class DependenciesAddon(BaseServerAddon):
    name = "dependencies_tool"
    title = "Dependencies Tool"
    version = __version__

    services = {
        "Dependencies": {"image": "ynput/ayon-dependencies-tool:0.0.2"}
    }

    def initialize(self):
        logging.info("Initializing Dependencies Addon.")
