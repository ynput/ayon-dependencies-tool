import os
import sys
import time
import signal
import platform

import ayon_api
from nxtools import logging

from dependencies import main


class DependenciesToolListener:
    def __init__(self):
        """ Listener on "dependencies.start_create.{platform_name}" topic.

        This topic should contain events triggered by Dependency addon by
        admin to start full creation of dependency package.

        After consuming event from `start_create` topic new event is created on
        "dependencies.creating_package.{platform_name}" to follow creation job.

        There might be multiple processing workers, each for specific OS. Each
        is automatically listening on separate topic.

        It is responsibility of server (or addon) to trigger so many events on
        so many topics as it is required.
        """
        logging.info("Initializing the Dependencies Tool Listener.")

        self.worker_id = "my_id"  # TODO get from Server

        signal.signal(signal.SIGINT, self._signal_teardown_handler)
        signal.signal(signal.SIGTERM, self._signal_teardown_handler)

    def _signal_teardown_handler(self, signalnum, frame):
        logging.warning("Process stop requested. Terminating process.")
        logging.warning("Termination finished.")
        sys.exit(0)

    def start_listening(self):
        """ Main loop querying the AYON event loop
        """
        logging.info("Start listening for Ayon Events...")

        platform_name = platform.system().lower()
        source_topic = f"dependencies.start_create.{platform_name}"
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
