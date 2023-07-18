import os
import sys


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from dependencies.cli import main

    main()
