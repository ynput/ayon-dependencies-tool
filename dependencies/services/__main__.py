import sys

import listener


if __name__ == "__main__":
    print("__main__")
    listener = listener.DependenciesToolListener()
    sys.exit(listener.start_listening())
