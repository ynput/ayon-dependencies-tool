"""Get runtime python modules from build using this script.

Execute this script using venv python executable to get runtime python modules.
The script is using 'pkg_resources' to get runtime modules and their versions.
Output is stored to a json file that must be provided by last argument.
"""


import contextlib
import sys
import json
from pathlib import Path


def get_runtime_modules(runtime_root: str) -> dict[str, str]:
    """Get runtime python modules and their versions.

    One of the dependencies from runtime dependencies must be imported
    so 'pkg_resources' have them available in `working_set`.

    This approach makes sure that we use right version that are really
    installed in runtime dependencies directory. Keep in mind that some
    dependencies have other modules as requirements that may not be
    listed in pyproject.toml and there might not be explicit version.
    Also using version from modules require to import them and be lucky
    that version is available and that installed module have same name
    as pip package (e.g. 'PIL' vs. 'Pillow').

    Todo:
        Find a better way how to define one dependency to import
        Randomly chosen module inside runtime dependencies

    Returns:
        dict[str, str]: Mapping of package name to version.

    """
    sys.path.insert(0, runtime_root)

    try:
        from importlib.metadata import distributions
    except ImportError:
        from importlib_metadata import distributions  # backport for older Pythons

    runtime_root = Path(runtime_root)
    output = {}

    for dist in distributions(path=[str(runtime_root)]):
        # Try to get the canonical package name from metadata, fall back to dist.name
        name = None
        with contextlib.suppress(Exception):
            name = dist.metadata["Name"]
        if not name:
            name = getattr(dist, "name", None)

        # As a last resort, infer a top-level name from the distribution files
        if not name:
            files = list(dist.files or [])
            name = files[0].parts[0] if files else None
        if name:
            output[name] = dist.version

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
