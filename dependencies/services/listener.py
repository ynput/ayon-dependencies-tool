import os
import sys
import time
import signal
import socket
from typing import Any, Callable, Union

import ayon_api
from nxtools import logging


EVENT_TYPES = [
    "Shotgun_{0}_New",  # a new entity was created.
    "Shotgun_{0}_Change",  # an entity was modified.
    "Shotgun_{0}_Retirement",  # an entity was deleted.
    "Shotgun_{0}_Revival",  # an entity was revived.
]

# To be revised once we usin links
IGNORE_ATTRIBUTE_NAMES = [
    "assets",
    "parent_shots",
    "retirement_date",
    "shots"
]


class DependenciesToolListener:
    def __init__(self, func: Union[Callable, None] = None):
        """ Ensure both Ayon and Shotgrid connections are available.

        Set up common needed attributes and handle shotgrid connection
        closure via signal handlers.

        Args:
            func (Callable, None): In case we want to override the default
                function we cast to the processed events.
        """
        logging.info("Initializing the Dependencies Tool Listener.")
        if func is None:
            self.func = self.send_event_to_server
        else:
            self.func = func

        logging.debug(f"Callback method is {self.func}.")

        signal.signal(signal.SIGINT, self._signal_teardown_handler)
        signal.signal(signal.SIGTERM, self._signal_teardown_handler)

    def _signal_teardown_handler(self, signalnum, frame):
        logging.warning("Process stop requested. Terminating process.")
        logging.warning("Termination finished.")
        sys.exit(0)

    def send_event_to_server(self):
        pass

    def start_listening(self):
        """ Main loop querying the AYON event loop
        """
        logging.info("Start listening for Ayon Events...")

        filters = None
        last_event_id = "459ba8a4f8bc11edaa2f0242c0a89005"
        filters = [
            ["id", "greater_than", last_event_id]
        ]

        while True:
            events = ayon_api.get_events(["log.error"])
            logging.info("typ::{}".format(events))
            logging.info("events::{}".format(list(events)))

            time.sleep(2)

