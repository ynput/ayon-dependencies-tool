"""Get runtime python modules from build using this script.

Execute this script using venv python executable to get runtime python modules.
The script is using 'pkg_resources' to get runtime modules and their versions.
Output is stored to a json file that must be provided by last argument.
"""

import sys
import json
from pathlib import Path


def get_runtime_modules(runtime_root):
    sys.path.insert(0, runtime_root)
    # Import 'pkg_resources' after adding runtime root to 'sys.path'
    import pkg_resources

    runtime_root = Path(runtime_root)

    # One of the dependencies from runtime dependencies must be imported
    #   so 'pkg_resources' have them available in 'working_set'
    # This approach makes sure that we use right version that are really
    #   installed in runtime dependencies directory. Keep in mind that some
    #   dependencies have other modules as requirements that may not be
    #   listed in pyproject.toml and there might not be explicit version.
    #   Also using version from modules require to import them and be lucky
    #   that version is available and that installed module have same name
    #   as pip package (e.g. 'PIL' vs. 'Pillow').
    # TODO find a better way how to define one dependency to import
    # Randomly chosen module inside runtime dependencies

    output = {}
    for package in pkg_resources.working_set:
        package_path = Path(package.module_path)
        if package_path.is_relative_to(runtime_root):
            output[package.project_name] = package.version
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
