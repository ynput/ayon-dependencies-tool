import sys
import os

code_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, code_dir)

import listener


if __name__ == "__main__":
    listener.main()
