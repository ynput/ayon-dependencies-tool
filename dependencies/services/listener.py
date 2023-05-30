import os
import sys
import time
import signal
import platform
from typing import Callable, Union

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
                                              "Creating dependency package",
                                              False)
            if event:
                logging.info("typ::{}".format(event))
                src_job = ayon_api.get_event(event["dependsOn"])
                try:
                    package_name = self.process_create_dependency()
                    description = f"{package_name} created"
                    status = "finished"
                except Exception as e:
                    status = "failed"
                    description = f"Creation of package failed \n {str(e)}"

                ayon_api.update_event(
                    event["id"],
                    sender=self.worker_id,
                    status=status,
                    description=description,
                )

            time.sleep(2)

    def process_create_dependency(self):
        """Calls full creation dependency package process

        Expects env vars:
            AYON_SERVER_URL
            AYON_API_KEY

        Returns:
            (str): created package name
        """
        try:
            package_name = main(os.environ["AYON_SERVER_URL"],
                                os.environ["AYON_API_KEY"],
                                "..\\tests\\resources\\pyproject_clean.toml")
            return package_name
        except:
            raise
