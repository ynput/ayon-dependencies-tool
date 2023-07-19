"""Launch listener locally for testing purposes.

This script will add 'dependencies' to 'sys.path' to be able to launch listener.
"""

import os
import sys

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

import listener


if __name__ == "__main__":
    listener.main()
