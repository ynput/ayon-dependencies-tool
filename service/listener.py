import sys
import time
import signal
import platform

import ayon_api
from nxtools import logging
from nxtools import log_traceback

from dependencies import create_package

SOURCE_TOPIC = "bundle.created"


class DependenciesToolListener:
    def __init__(self):
        """ Listener on "bundle.updated" topic.

        This topic should contain events triggered by Server after bundle is
        created or updated to start full creation of dependency package.

        After consuming event from `"bundle.updated"` topic new event is
        created on "dependencies.creating_package.{platform_name}" to follow
        state of creation job.

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
        target_topic = f"dependencies.creating_package.{platform_name}"

        while True:
            event = ayon_api.enroll_event_job(
                SOURCE_TOPIC,
                target_topic,
                self.worker_id,
                "Creating dependency package",
                # Creation of dependency packages is not sequential process
                sequential=False,
            )
            if not event:
                time.sleep(2)
                continue

            src_job = ayon_api.get_event(event["dependsOn"])
            bundle_name = src_job["summary"]["name"]
            try:
                package_name = self.process_create_dependency(bundle_name)
                description = f"{package_name} created"
                status = "finished"
            except Exception as e:
                status = "finished"
                description = f"Creation of package for {bundle_name} failed\n{str(e)}"  # noqa
                log_traceback(e)

            ayon_api.update_event(
                event["id"],
                sender=self.worker_id,
                status=status,
                description=description,
            )

    def process_create_dependency(self, bundle_name):
        """Calls full creation dependency package process

        Expects env vars:
            AYON_SERVER_URL
            AYON_API_KEY

        Args:
            bundle_name (str): for which bundle dependency packages should be
                created

        Returns:
            (str): created package name
        """
        return create_package(bundle_name)


def main():
    listener = DependenciesToolListener()
    sys.exit(listener.start_listening())


if __name__ == "__main__":
    main()