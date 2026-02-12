"""Get runtime python modules from build using this script.

Execute this script using venv python executable to get runtime python modules.
The script is using 'pkg_resources' to get runtime modules and their versions.
Output is stored to a json file that must be provided by last argument.
"""

import sys
import json
from pathlib import Path

if sys.version_info >= (3, 10):
    from importlib.metadata import distributions
else:
    from importlib_metadata import distributions


def get_runtime_modules(runtime_root):
    sys.path.insert(0, runtime_root)

    runtime_root = Path(runtime_root)

    runtime_dep_root = runtime_root / "vendor" / "python"

    output = {}
    for dist in distributions():
        try:
            # Get the location of the distribution
            if dist.locate_file(''):
                dist_path = Path(dist.locate_file(''))
                if dist_path.is_relative_to(runtime_dep_root):
                    output[dist.name] = dist.version
        except (AttributeError, TypeError):
            # Handle cases where locate_file might not work
            continue
    
    return output


def main():
    output_path = sys.argv[-1]
    with open(output_path, "r") as stream:
        data = json.load(stream)

    data["runtime_dependencies"] = get_runtime_modules(
        data["runtime_site_packages"]
    )

    print(f"Storing output to {output_path}")
    with open(output_path, "w") as stream:
        json.dump(data, stream, indent=4)


if __name__ == "__main__":
    main()
