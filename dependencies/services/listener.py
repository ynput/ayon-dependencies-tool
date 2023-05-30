import os
import sys
import time
import signal
import platform
from typing import Any, Callable, Union

import ayon_api
from nxtools import logging

from dependencies import main


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

        self.worker_id = "my_id"  # TODO get from Server

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

        source_topic = "dependencies.start_create"
        platform_name = platform.system().lower()
        target_topic = f"dependencies.creating_package.{platform_name}"

        while True:
            event = ayon_api.enroll_event_job(source_topic,
                                              target_topic,
                                              self.worker_id,
                                              "Create new dependency package",
                                              False)
            if event:
                logging.info("typ::{}".format(event))
                src_job = ayon_api.get_event(event["dependsOn"])
                result = self.process_create_dependency()

                status = "failure" if result == 1 else "finished"

                ayon_api.update_event(
                    event["id"],
                    sender=self.worker_id,
                    status=status,
                    description="New finished description",
                )

            time.sleep(2)

    def process_create_dependency(self):
        try:
            main(os.environ["AYON_SERVER_URL"],
                 os.environ["AYON_API_KEY"],
                 "C:\\Users\\pypeclub\\Documents\\ayon\\openpypev4-dependencies-tool\\dependencies\\tests\\resources\\pyproject_clean.toml")
        except:
            raise

        return 0

