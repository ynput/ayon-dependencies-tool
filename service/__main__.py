import sys
import os

code_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, code_dir)

import listener


if __name__ == "__main__":
    # << for development only
    with open("../.env") as fp:
        for line in fp:
            if not line:
                continue
            key, value = line.split("=")
            os.environ[key] = value.strip()
    # >>
    listener = listener.DependenciesToolListener()
    sys.exit(listener.start_listening())
