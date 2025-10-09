import os
import re
import tempfile
import copy
import platform
import hashlib
import zipfile
import json
import subprocess
import collections
import shutil

from typing import Dict, Union, Any, List
from packaging import version
from dataclasses import dataclass

import toml
import requests
from poetry.core.constraints.version import (
    parse_constraint,
    EmptyConstraint,
    VersionConstraint,
    VersionRangeConstraint,
)
from poetry.core.packages.utils.link import Link
from poetry.core.packages.utils.utils import is_url
from poetry.core.vcs.git import ParsedUrl

import ayon_api
from ayon_api import create_dependency_package_basename

if platform.system().lower() == "linux":
    import distro
else:
    distro = None

from .utils import (
    run_subprocess,
    ZipFileLongPaths,
    get_venv_executable,
    get_venv_site_packages,
    PACKAGE_ROOT,
)
from .custom_solver import solve_dependencies

ConstraintClasses = (
    EmptyConstraint,
    VersionConstraint,
    VersionRangeConstraint,
)
ConstraintClassesHint = Union[
    EmptyConstraint,
    VersionConstraint,
    VersionRangeConstraint
]

POETRY_VERSION = "2.0.1"


@dataclass
class Bundle:
    name: str
    addons: Dict[str, str]
    dependency_packages: Dict[str, str]
    installer_version: Union[str, None]


def get_poetry_install_script() -> str:
    """Get Poetry install script path.

    Script is cached in downloads folder. If script is not cached ye, it
        will be downloaded.

    Returns:
        str: Path to poetry install script.
    """

    downloads_dir = os.path.join(PACKAGE_ROOT, "downloads")
    if not os.path.exists(downloads_dir):
        os.makedirs(downloads_dir)
    poetry_script_path = os.path.join(
        downloads_dir, f"poetry-install-script.py")
    if os.path.exists(poetry_script_path):
        return poetry_script_path
    response = requests.get("https://install.python-poetry.org")
    with open(poetry_script_path, "wb") as stream:
        stream.write(response.content)
    return poetry_script_path


def get_pyenv_arguments(
    output_root: str, python_version: str
) -> Union[List[str], None]:
    """Use pyenv to install python version and use for venv creation.

    Usage of pyenv is ideal as it allows to properly install runtime
        dependencies.

    Args:
        output_root (str): Path to processing root.
        python_version (str): Python version to install.

    Returns:
        Union[list[str], None]: List of arguments for subprocess or None.
    """

    pyenv_path = shutil.which("pyenv")
    if not pyenv_path:
        return
    print(f"Installing Python {python_version} with pyenv")
    install_args = [pyenv_path, "install", python_version, "--skip-existing"]
    if platform.system().lower() == "windows":
        install_args.append("--quiet")
    result = subprocess.run(install_args)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to install python {python_version}")
    subprocess.run(
        [pyenv_path, "local", python_version],
        cwd=output_root
    )
    output = subprocess.check_output(
        [pyenv_path, "which", "python"],
        cwd=output_root
    )
    python_path = output.decode().strip()
    return [python_path]


def get_python_arguments(output_root: str, python_version: str) -> List[str]:
    """Get arguments to run python.

    By default, is trying to use 'pyenv' to install python version and use
        it for venv creation. If 'pyenv' is not available, it will use
        system python.

    Args:
        output_root (str): Path to processing root.
        python_version (str): Python version to install.

    Returns:
        list[str]: List of arguments for subprocess.
    """

    args = get_pyenv_arguments(output_root, python_version)
    if args is not None:
        return args
    print(
        "Failed to use pyenv. Using system python, this may cause that"
        " package will be incompatible package with installer."
    )
    python_path = shutil.which("python3")
    if not python_path:
        python_path = shutil.which("python")
    return [python_path]


def get_bundles(con: ayon_api.ServerAPI) -> Dict[str, Bundle]:
    """Provides dictionary with available bundles

    Returns:
        (dict) of (Bundle) {"BUNDLE_NAME": Bundle}
    """
    bundles_by_name = {}
    for bundle_dict in con.get_bundles()["bundles"]:
        try:
            bundle = Bundle(
                name=bundle_dict["name"],
                installer_version=bundle_dict["installerVersion"],
                addons=bundle_dict["addons"],
                dependency_packages=bundle_dict["dependencyPackages"],
            )
        except KeyError:
            print(f"Wrong bundle definition for {bundle_dict['name']}")
            continue
        bundles_by_name[bundle.name] = bundle
    return bundles_by_name


def get_all_addon_tomls(con: ayon_api.ServerAPI) -> Dict[str, Dict[str, Any]]:
    """Provides list of dict containing addon tomls.

    Returns:
        dict[str, dict[str, Any]]: All addon toml files.
    """

    tomls = {}
    response = con.get_addons_info(details=True)
    for addon_dict in response["addons"]:
        addon_name = addon_dict["name"]
        addon_versions = addon_dict["versions"]

        for version_name, addon_version_dict in addon_versions.items():
            client_pyproject = addon_version_dict.get("clientPyproject")
            if not client_pyproject:
                continue
            full_name = f"{addon_name}_{version_name}"
            tomls[full_name] = client_pyproject

    return tomls


def get_bundle_addons_tomls(
    con: ayon_api.ServerAPI, bundle: Bundle
) -> Dict[str, Dict[str, Any]]:
    """Query addons for `bundle` to get their python dependencies.

    Returns:
        dict[str, dict[str, Any]]: {'core_1.0.0': {...toml content...}}
    """

    bundle_addons = {
        f"{key}_{value}"
        for key, value in bundle.addons.items()
        if value is not None
    }
    print("Getting dependencies for addons:")
    for addon in bundle_addons:
        print(f"  - {addon}")
    addon_tomls = get_all_addon_tomls(con)

    return {
        addon_full_name: toml
        for addon_full_name, toml in addon_tomls.items()
        if addon_full_name in bundle_addons
    }


def find_installer_by_name(
    con: ayon_api.ServerAPI,
    bundle_name: str,
    installer_name: str,
    platform_name: str,
) -> Dict[str, Any]:
    for installer in con.get_installers()["installers"]:
        if (
            installer["platform"] == platform_name
            and installer["version"] == installer_name
        ):
            return installer
    raise ValueError(f"{bundle_name} must have installer present.")


def get_installer_toml(installer: Dict[str, Any]) -> Dict[str, Any]:
    """Returns dict with format matching of .toml file for `installer_name`.

    Queries info from server for `bundle_name` and its `installer_name`,
    transforms its list of python dependencies into dictionary matching format
    of `.toml`

    Example output:
        {"tool": {"poetry": {"dependencies": {"somepymodule": "1.0.0"...}}}}

    Args:
        installer (dict[str, Any])

    Returns:
        dict[str, Any]: Installer toml content.
    """

    python_modules = copy.deepcopy(installer["pythonModules"])
    python_modules["python"] = installer["pythonVersion"]
    return {
        "tool": {
            "poetry": {
                # Create copy to avoid modifying original data
                "dependencies": python_modules,

                # These data have no effect, but are required by poetry
                "name": "AYONDepPackage",
                "version": "1.0.0",
                "description": "Dependency package for AYON",
                "authors": ["Ynput s.r.o. <info@openpype.io>"],
                "license": "MIT License",
            }
        },
        "ayon": {
            "runtimeDependencies": copy.deepcopy(
                installer["runtimePythonModules"]
            )
        }
    }


def is_valid_toml(toml: Dict[str, Any]) -> bool:
    """Validates that 'toml' contains all required fields.

    Args:
        toml (dict[str, Any])

    Returns:
        True if all required keys present

    Raises:
        KeyError
    """

    required_fields = ["tool.poetry"]
    for field in required_fields:
        fields = field.split(".")
        value = toml
        while fields:
            key = fields.pop(0)
            value = value.get(key)

            if not value:
                raise KeyError(f"Toml content must contain {field}")

    return True


def _merge_dependency(
    main_dep_info,
    dep_info,
    platform_name,
    dependency,
    addon_name
):
    if main_dep_info is None:
        return dep_info

    if isinstance(main_dep_info, dict):
        if platform_name in main_dep_info:
            main_dep_info = main_dep_info[platform_name]

    resolved_vers = _get_correct_version(main_dep_info, dep_info)
    if not isinstance(resolved_vers, ConstraintClasses):
        raise ValueError(
            "RuntimeDependency must be defined as version.")

    dep_info_c = parse_constraint(dep_info)
    if (
        resolved_vers.is_empty()
        or not dep_info_c.allows_all(resolved_vers)
    ):
        raise ValueError(
            f"Cannot result {dependency} with"
            f" {dep_info} for {addon_name}"
        )
    return str(dep_info_c.intersect(resolved_vers))


def merge_tomls_dependencies(
    main_toml: Dict[str, Dict[str, Any]],
    addon_toml: Dict[str, Dict[str, Any]],
    addon_name: str,
) -> Dict[str, Dict[str, Any]]:
    """Add dependencies from 'addon_toml' to 'main_toml'.

    Looks for mininimal compatible version from both tomls.

    Handles sections:
        - ["tool"]["poetry"]["dependencies"]
        - ["ayon"]["runtimeDependencies"]

    Returns:
        (dict): updated 'main_toml' with additional/updated dependencies

    Raises:
        ValueError if any tuple of main and addon dependency cannot be resolved
    """

    main_dependencies = (
        main_toml["tool"]["poetry"].setdefault("dependencies", {})
    )
    addon_dependencies = (
        addon_toml
        .get("tool", {})
        .get("poetry", {})
        .get("dependencies")
    ) or {}

    for dependency, dep_version in addon_dependencies.items():
        main_version = main_dependencies.get(dependency)
        resolved_vers = _get_correct_version(main_version, dep_version)
        if not main_version:
            main_version = "N/A"

        if (
            isinstance(resolved_vers, ConstraintClasses)
            and resolved_vers.is_empty()
        ):
            raise ValueError(
                f"Version {dep_version} cannot be resolved against"
                f" {main_version} for {dependency} in {addon_name}"
            )

        main_dependencies[dependency] = resolved_vers

    return main_toml


def merge_tomls_runtime(
    main_toml: Dict[str, Dict[str, Any]],
    addon_toml: Dict[str, Dict[str, Any]],
    addon_name: str,
    platform_name: str,
) -> Dict[str, Dict[str, Any]]:
    """Add dependencies from 'addon_toml' to 'main_toml'.

    Looks for mininimal compatible version from both tomls.

    Handles sections:
        - ["tool"]["poetry"]["dependencies"]
        - ["ayon"]["runtimeDependencies"]

    Returns:
        (dict): updated 'main_toml' with additional/updated dependencies

    Raises:
        ValueError: If any tuple of main and addon dependency
            cannot be resolved.
    """

    # handle runtime dependencies
    addon_poetry = addon_toml.get("ayon", {}).get("runtimeDependencies")
    if not addon_poetry:
        return main_toml

    main_dependencies = (
        main_toml["tool"]["poetry"].setdefault("dependencies", {})
    )
    main_runtime = main_toml["ayon"]["runtimeDependencies"]
    for dependency, dep_info in addon_poetry.items():
        if isinstance(dep_info, dict):
            if platform_name in dep_info:
                dep_info = dep_info[platform_name]

            if "version" in dep_info:
                dep_info = dep_info["version"]

        if dependency in main_dependencies:
            main_dependencies[dependency] = _merge_dependency(
                main_dependencies[dependency],
                dep_info,
                platform_name,
                dependency,
                addon_name
            )
            continue

        main_runtime[dependency] = _merge_dependency(
            main_runtime.get(dependency),
            dep_info,
            platform_name,
            dependency,
            addon_name
        )

    return main_toml


def _get_correct_version(
    main_version: Union[str, Dict[str, Any], ConstraintClassesHint],
    dep_version: Union[str, Dict[str, Any]]
) -> Union[ConstraintClassesHint, Dict[str, Any]]:
    """Return resolved version from two version (constraint).

    Warning:
        This function does not resolve if there are 2 sources of same
            module without version specification but with different source.
            e.g. git, url or path.
        In case this case happens first available source is used.

    Args:
        main_version (Union[str, dict, ConstraintClassesHint]): Version
            or constraint ("3.6.1", "^3.7")
        dep_version (Union[str, dict]): New dependency that should be merged.

    Returns:
        Union[ConstraintClassesHint, dict]: Constraint or dict.
    """

    # TODO find out how poetry handles multile dependencies defined with
    #   different constraints

    if main_version and isinstance(main_version, dict):
        return main_version

    if not main_version:
        if isinstance(dep_version, str):
            dep_version = parse_constraint(dep_version)
        return dep_version

    if isinstance(main_version, str):
        main_version = parse_constraint(main_version)

    if not dep_version:
        return main_version

    if isinstance(dep_version, str):
        dep_version = parse_constraint(dep_version)

    if hasattr(dep_version, "intersect"):
        return dep_version.intersect(main_version)
    return main_version


def _is_url_constraint(version: Any) -> bool:
    version = str(version)
    return "http" in version or "git" in version


def _version_parse(version_value):
    """Handles different formats of versions

    Parses:
        "^2.0.0"
        { version = "301", markers = "sys_platform == 'win32'" }
    """

    if isinstance(version_value, dict):
        return version_value.get("version")
    return version.parse(version_value)


def get_full_toml(base_toml_data, addon_tomls, platform_name):
    """Loops through list of local addon folder paths to create full .toml

    Full toml is used to calculate set of python dependencies for all enabled
    addons.

    Args:
        base_toml_data (dict[str, Any]): Content of pyproject.toml from
            ayon-launcher installer.
        addon_tomls (dict[str, Any]): Content of addon pyproject.toml
        platform_name (str): Platform name.

    Returns:
        (dict) updated base .toml
    """

    # Fix git sources of installer dependencies
    main_dependencies = base_toml_data["tool"]["poetry"]["dependencies"]
    modified_dependencies = {}
    for key, value in main_dependencies.items():
        if not isinstance(value, str):
            continue

        if not is_url(value) and not value.startswith("git+http"):
            continue

        new_value = None
        link = Link(value)
        # TODO handler other version-less contraints
        if link.scheme.startswith("git+"):
            url = ParsedUrl.parse(link.url)
            new_value = {"git": url.url}
            if url.rev:
                new_value["rev"] = url.rev

        elif link.scheme == "git":
            new_value = {
                "git": link.url_without_fragment
            }

        modified_dependencies[key] = new_value
    main_dependencies.update(modified_dependencies)
    for addon_name, addon_toml_data in tuple(addon_tomls.items()):
        if isinstance(addon_toml_data, str):
            addon_tomls[addon_name] = toml.loads(addon_toml_data)

    # Merge addon dependencies
    for addon_name, addon_toml_data in addon_tomls.items():
        print(f"Merging in {addon_name} dependencies")
        
        base_toml_data = merge_tomls_dependencies(
            base_toml_data, addon_toml_data, addon_name
        )

    for addon_name, addon_toml_data in addon_tomls.items():
        print(f"Merging in {addon_name} runtime dependencies")

        base_toml_data = merge_tomls_runtime(
            base_toml_data, addon_toml_data, addon_name, platform_name
        )

    # Convert all 'ConstraintClassesHint' to 'str'
    main_dependencies = base_toml_data["tool"]["poetry"]["dependencies"]
    modified_dependencies = {}
    for key, value in main_dependencies.items():
        if not isinstance(value, (str, dict)):
            modified_dependencies[key] = str(value)
    main_dependencies.update(modified_dependencies)

    print("Collected dependencies:")
    for key, value in sorted(main_dependencies.items()):
        print(f"  - {key} ({value})")

    return base_toml_data


class VenvInfo:
    def __init__(
        self, root, poetry_bin, poetry_env, venv_path, python_version
    ):
        self.root = root
        self.poetry_bin = poetry_bin
        self.poetry_env = poetry_env
        self.venv_path = venv_path
        self.python_version = python_version


def prepare_new_venv(output_root, installer):
    """Let Poetry create new venv in 'venv_folder' from 'full_toml_data'.

    Args:
        output_root (str): Path where venv should be created.
        installer (dict[str, Any]): Installer metadata.

    Raises:
        RuntimeError: Exception is raised if process finished with nonzero
            return code.
    """

    print(f"Preparing new venv in {output_root}")

    python_version = installer["pythonVersion"]

    python_args = get_python_arguments(output_root, python_version)

    poetry_script = get_poetry_install_script()
    poetry_home = os.path.join(output_root, ".poetry")
    poetry_bin = os.path.join(poetry_home, "bin", "poetry")
    venv_path = os.path.join(output_root, ".venv")

    env = dict(os.environ.items())
    env["POETRY_VERSION"] = POETRY_VERSION
    env["POETRY_HOME"] = poetry_home
    # Create poetry in output root
    subprocess.call(python_args + [poetry_script], env=env, cwd=output_root)

    # Create venv using poetry
    run_subprocess(
        python_args + ["-m", "venv", venv_path],
        env=env,
        cwd=output_root
    )
    env["VIRTUAL_ENV"] = venv_path
    # Change poetry config to ignore venv in poetry
    for config_key, config_value in (
        ("virtualenvs.create", "false"),
        ("virtualenvs.in-project", "false"),
    ):
        run_subprocess(
            [poetry_bin, "config", config_key, config_value, "--local"],
            env=env,
            cwd=output_root
        )

    return VenvInfo(
        output_root,
        poetry_bin,
        env,
        venv_path,
        python_version
    )


def install_poetry(
    full_toml_data, installer, venv_info: VenvInfo
):
    toml_path = os.path.join(venv_info.root, "pyproject.toml")

    _convert_url_constraints(full_toml_data)

    installer_runtime_dependencies = copy.deepcopy(
        installer["runtimePythonModules"]
    )
    runtime_dependencies = copy.deepcopy(
        full_toml_data["ayon"]["runtimeDependencies"]
    )
    # Remove installer dependencies to find out if there are any other
    #   dependencies
    for dep in installer_runtime_dependencies:
        runtime_dependencies.pop(dep, None)

    print("Runtime dependencies to install:")
    for package in sorted(runtime_dependencies):
        print(f"  - {package}")

    # Store installer runtime dependencies only if are installed
    installed_installer_runtime_deps = set()
    if runtime_dependencies:
        toml_dependencies = full_toml_data["tool"]["poetry"]["dependencies"]
        for package_name, package_version in (
            installer_runtime_dependencies.items()
        ):
            installed_installer_runtime_deps.add(package_name)
            toml_dependencies[package_name] = package_version

    with open(toml_path, "w") as stream:
        toml.dump(full_toml_data, stream)

    # Install dependencies from pyproject.toml
    return_code = run_subprocess(
        [venv_info.poetry_bin, "install", "--no-root", "--ansi"],
        env=venv_info.poetry_env,
        cwd=venv_info.venv_path
    )
    if return_code != 0:
        raise RuntimeError(f"Preparation of {venv_info.venv_path} failed!")

    runtime_root = os.path.join(venv_info.root, "runtime")
    os.makedirs(runtime_root, exist_ok=True)
    _install_runtime_dependencies(
        runtime_dependencies,
        runtime_root,
        venv_info.poetry_bin,
        venv_info.poetry_env
    )
    if platform.system().lower() == "windows":
        runtime_site_packages = os.path.join(
            runtime_root, "Lib", "site-packages"
        )
    else:
        lib_dir = os.path.join(runtime_root, "lib")
        # linux and macos create python{x}.{y} subfolder (should create
        #   only one)
        runtime_site_packages = os.path.join(
            lib_dir, f"python{venv_info.python_version[:3]}", "site-packages"
        )
        # Fill correct path only if exists
        if os.path.exists(lib_dir):
            python_subdirs = list(os.listdir(lib_dir))
            if python_subdirs:
                python_subdir = python_subdirs[0]
                runtime_site_packages = os.path.join(
                    lib_dir, python_subdir, "site-packages"
                )

    return runtime_site_packages, installed_installer_runtime_deps


def _install_runtime_dependencies(
    runtime_dependencies, runtime_root, poetry_bin, env
):
    """Install runtime dependencies from 'full_toml_data' to 'output_root'.

    Args:
        runtime_dependencies (dict[str, str]): Runtime dependencies with
            requested versions.
        runtime_root (str): Path where runtime dependencies should be created.
        poetry_bin (str): Path to poetry executable.
    """

    requirements_lines = []
    for package_name, package_version in runtime_dependencies.items():
        parsed_version: VersionConstraint = parse_constraint(package_version)
        if parsed_version.is_any():
            package_version = ""
        elif parsed_version.is_simple():
            package_version = f"=={package_version}"
        else:
            min_ver = str(parsed_version.min)
            if parsed_version.include_min:
                min_ver = f">={min_ver}"
            else:
                min_ver = f">{min_ver}"
            max_ver = str(parsed_version.max)
            if parsed_version.include_max:
                max_ver = f"<={max_ver}"
            else:
                max_ver = f"<{max_ver}"

            package_version = f"{min_ver},{max_ver}"

        requirements_lines.append(f"{package_name}{package_version}")

    requiements_path = os.path.join(runtime_root, "requirements.txt")
    with open(requiements_path, "w") as stream:
        stream.write("\n".join(requirements_lines))

    args = [
        poetry_bin, "run",
        "python", "-m", "pip", "install",
        "--upgrade",
        "-r", requiements_path,
        "--prefix", str(runtime_root)
    ]

    run_subprocess(
        args,
        env=env,
        cwd=runtime_root
    )


def _convert_url_constraints(full_toml_data):
    """Converts string occurences of "git+https" to dict required by Poetry"""
    dependency_keys = ["dependencies"]
    for key in dependency_keys:
        dependencies = full_toml_data["tool"]["poetry"].get(key)
        if not dependencies:
            continue
        for dependency, dep_version in dependencies.items():
            if isinstance(dep_version, dict):
                dependencies[dependency] = dep_version
                continue

            # TODO this is maybe not needed anymore
            #   Only source of git+https should be from installer which was
            #   created using pip freeze.
            if not _is_url_constraint(dep_version):
                continue

            revision = None
            if "@" in dep_version:
                parts = dep_version.split("@")
                dep_version = parts.pop(0)
                revision = "@".join(parts)

            if dep_version.startswith("http"):
                dependencies[dependency] = {"url": dep_version}
                continue

            if "git+" in dep_version:
                dep_version = dep_version.replace("git+", "")
                dependencies[dependency] = {"git": dep_version}
                continue

            if revision:
                dependencies[dependency]["rev"] = revision


def lock_to_toml_data(lock_path):
    """Create toml file with explicit version from lock file.

    Should be used to compare addon venv with client venv and purge existing
    libraries.

    Args:
        lock_path (str): path to base lock file (from build)
    Returns:
        (dict): dictionary representation of toml data with explicit library
            versions
    Raises:
        (FileNotFound)
    """

    if not os.path.exists(lock_path):
        raise ValueError(
            f"{lock_path} doesn't exist. Provide path to real toml."
        )

    with open(lock_path) as fp:
        parsed = toml.load(fp)

    dependencies = {
        package_info["name"]: package_info["version"]
        for package_info in parsed["package"]
    }

    return {"tool": {"poetry": {"dependencies": dependencies}}}


def remove_existing_from_venv(
    addons_venv_path,
    installer,
    installed_installer_runtime_deps
):
    """Loop through calculated addon venv and remove already installed libs.

    Args:
        addons_venv_path (str): Path to newly created merged venv for active
            addons.
        installer (dict[str, Any]): installer data from server.
        installed_installer_runtime_deps (set[str]): Installed runtime
            dependencies.

    Returns:
        (set) of folder/file paths that were removed from addon venv, used only
            for testing
    """

    pip_executable = get_venv_executable(addons_venv_path, "pip")
    print("Removing packages from venv")
    for package_name in sorted(
        set(installer["pythonModules"])
        | set(installed_installer_runtime_deps)
    ):
        # Fix 'Babel'
        # TODO fix in ayon-launcher
        if package_name == "Babel":
            package_name = "babel"
        print(f"- {package_name}")
        run_subprocess(
            [pip_executable, "uninstall", package_name, "--yes"],
            bound_output=False
        )


def zip_venv(venv_folder, runtime_site_packages, zip_filepath):
    """Zips newly created venv to single .zip file."""

    site_packages_roots = get_venv_site_packages(venv_folder)
    with ZipFileLongPaths(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        for site_packages_root in site_packages_roots:
            sp_root_len_start = len(site_packages_root) + 1
            for root, _, filenames in os.walk(site_packages_root):
                # Care only about files
                if not filenames:
                    continue

                # Skip __pycache__ folders
                root_name = os.path.basename(root)
                if root_name == "__pycache__":
                    continue

                dst_root = "dependencies"
                if len(root) > sp_root_len_start:
                    dst_root = os.path.join(dst_root, root[sp_root_len_start:])

                for filename in filenames:
                    src_path = os.path.join(root, filename)
                    dst_path = os.path.join(dst_root, filename)
                    zipf.write(src_path, dst_path)

        zip_runtime_root = "runtime"
        for root, _, filenames in os.walk(runtime_site_packages):
            # Care only about files
            if not filenames:
                continue

            dst_root = zip_runtime_root
            if root != runtime_site_packages:
                dst_root = os.path.join(
                    dst_root, root[len(runtime_site_packages) + 1:]
                )

            for filename in filenames:
                src_path = os.path.join(root, filename)
                dst_path = os.path.join(dst_root, filename)
                zipf.write(src_path, dst_path)


def prepare_zip_venv(venv_path, runtime_site_packages, output_root):
    """Handles creation of zipped venv.

    Args:
        venv_path (str): Path to created venv.
        runtime_site_packages (str): Path to runtime dependencies.
        output_root (str): Temp folder path.

    Returns:
        (str) path to zipped venv
    """
    basename = create_dependency_package_basename()
    if platform.system().lower() == "linux":
        basename += f"-{distro.id()}{distro.major_version()}"
    zip_file_name = f"{basename}.zip"
    venv_zip_path = os.path.join(output_root, zip_file_name)
    print(f"Zipping new venv to {venv_zip_path}")
    zip_venv(venv_path, runtime_site_packages, venv_zip_path)

    return venv_zip_path


def get_applicable_package(
    con: ayon_api.ServerAPI, new_toml: Dict[str, Any]
) -> Union[Dict[str, Any], None]:
    """Compares existing dependency packages to find matching.

    One dep package could contain same versions of python dependencies for
    different versions of addons (eg. no change in dependency, but change in
    functionality)

    Args:
        con (ayon_api.ServerApi): Connection to AYON server.
        new_toml (dict[str, Any]): Data of regular pyproject.toml file.

    Returns:
        Union[dict[str, Any], None]: Data of matching package.
    """

    toml_python_packages = dict(
        sorted(new_toml["tool"]["poetry"]["dependencies"].items())
    )
    for package in con.get_dependency_packages()["packages"]:
        package_python_packages = dict(sorted(
            package["pythonModules"].items())
        )
        if toml_python_packages == package_python_packages:
            return package


def get_python_modules(venv_path: str) -> Dict[str, str]:
    """Uses pip freeze to get installed libraries from `venv_path`.

    Args:
        venv_path (str): absolute path to created dependency package already
            with removed libraries from installer package

    Returns:
        dict[str, str] {'acre': '1.0.0',...}
    """

    pip_executable: str = get_venv_executable(venv_path, "pip")

    process: subprocess.Popen = subprocess.Popen(
        [pip_executable, "freeze", venv_path, "--no-color"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    _stdout, _stderr = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"Failed to freeze pip packages.")

    packages = {}
    for line in _stdout.decode("utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue

        match = re.match(r"^(.+?)(?:==|>=|<=|~=|!=|@)(.+)$", line)
        if match:
            package_name, package_version = match.groups()
            packages[package_name.rstrip()] = package_version.lstrip()
        else:
            packages[line] = None

    print("Installed python modules:")
    for package in sorted(packages):
        print(f"  - {package}")

    return packages


def calculate_hash(filepath):
    """Calculate sha256 hash of file.

    Args:
        filepath (str): Path to a file.

    Returns:
        str: File sha256 hashs.
    """

    checksum = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def prepare_package_data(
    venv_zip_path: str,
    bundle: Bundle,
    platform_name: str,
    runtime_dependencies: Dict[str, str],
):
    """Creates package data for server.

    All data in output are used to call 'create_dependency_package'.

    Args:
        venv_zip_path (str): Local path to zipped venv.
        bundle (Bundle): Bundle object with all data.
        platform_name (str): Platform name.
        runtime_dependencies (dict[str, str]): Runtime dependencies with
            requested versions.

    Returns:
        dict[str, Any]: Dependency package information.
    """

    venv_path = os.path.join(os.path.dirname(venv_zip_path), ".venv")
    python_modules = get_python_modules(venv_path)
    # Runtime dependencies do not have special key
    python_modules.update(runtime_dependencies)

    package_name = os.path.basename(venv_zip_path)
    checksum = calculate_hash(venv_zip_path)

    return {
        "filename": package_name,
        "python_modules": python_modules,
        "source_addons": bundle.addons,
        "installer_version": bundle.installer_version,
        "checksum": checksum,
        "checksum_algorithm": "sha256",
        "file_size": os.stat(venv_zip_path).st_size,
        "platform_name": platform_name,
    }


def stored_package_to_dir(output_dir, venv_zip_path, bundle, package_data):
    """Store dependency package to output directory.

    A json file with dependency package information is created and stored
    next to the dependency package file (replaced extension with .json).

    Bundle name is added to dependency package before saving.

    Args:
        output_dir (str): Path where dependency package will be stored.
        venv_zip_path (str): Local path to zipped venv.
        bundle (Bundle): Bundle object with all data.
        package_data (dict[str, Any]): Dependency package information.
    """

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    new_package_data = copy.deepcopy(package_data)
    # Change data to match server requirements
    new_package_data["platform"] = new_package_data.pop("platform_name")
    new_package_data["size"] = new_package_data.pop("file_size")
    # Add bundle name as information
    new_package_data["bundle_name"] = bundle.name

    filename = new_package_data["filename"]
    output_path = os.path.join(output_dir, filename)
    shutil.copy(venv_zip_path, output_path)
    metadata_path = output_path + ".json"
    with open(metadata_path, "w") as stream:
        json.dump(new_package_data, stream, indent=4)


def upload_to_server(con, venv_zip_path, package_data):
    """Creates and uploads package on the server

    Args:
        con (ayon_api.ServerAPI): Connection to server.
        venv_zip_path (str): Local path to zipped venv.
        package_data (dict[str, Any]): Package information.

    Returns:
        str: Package name.
    """

    con.create_dependency_package(**package_data)
    con.upload_dependency_package(
        venv_zip_path,
        package_data["filename"]
    )


def update_bundle_with_package(con, bundle, package_data):
    """Assign `package_name` to `bundle`

    Args:
        con (ayon_api.ServerAPI)
        bundle (Bundle)
        package_data (dict[str, Any])
    """

    package_name = package_data["filename"]
    print(f"Updating in {bundle.name} with {package_name}")
    platform_name = package_data["platform_name"]
    dependency_packages = copy.deepcopy(bundle.dependency_packages)
    dependency_packages[platform_name] = package_name
    con.update_bundle(bundle.name, dependency_packages)


def is_file_deletable(filepath):
    """Can be file deleted.

    Args:
        filepath (str): Path to a file.

    Returns:
        bool: File can be removed.
    """

    file_dirname = os.path.dirname(filepath)
    if os.access(file_dirname, os.W_OK | os.X_OK):
        try:
            with open(filepath, "w"):
                pass
            return True
        except OSError:
            pass

    return False


def get_runtime_dependencies(
    runtime_site_packages: str, addons_venv_path: str
) -> Dict[str, str]:
    python_executable = get_venv_executable(addons_venv_path, "python")
    script_path = os.path.join(PACKAGE_ROOT, "_runtime_deps.py")

    with tempfile.NamedTemporaryFile(
        prefix="ayon_dep_runtime", suffix=".json", delete=False
    ) as tmp:
        output_path = tmp.name

    with open(output_path, "w") as stream:
        json.dump(
            {"runtime_site_packages": runtime_site_packages},
            stream
        )

    try:
        subprocess.run([python_executable, script_path, output_path])
        with open(output_path) as stream:
            data = json.load(stream)
        return data["runtime_dependencies"]

    finally:
        os.remove(output_path)


def _remove_tmpdir(tmpdir):
    """Safer removement of temp directory.

    Notes:
        @iLLiCiTiT Function was created because I've hit issues with
            'shutil.rmtree' on tmpdir -> lead to many un-cleared temp dirs.

    Args:
        tmpdir (str): Path to temp directory.
    """

    failed = []
    if not os.path.exists(tmpdir):
        return failed

    filepaths = set()
    for root, dirnames, filenames in os.walk(tmpdir):
        for filename in filenames:
            filepaths.add(os.path.join(root, filename))

    remove_queue = collections.deque()
    for filepath in filepaths:
        remove_queue.append((filepath, 0))

    while remove_queue:
        (filepath, attempt) = remove_queue.popleft()
        try:
            os.remove(filepath)
        except OSError:
            if attempt > 3:
                failed.append(filepath)
            else:
                remove_queue.append((filepath, attempt + 1))

    if not failed:
        shutil.rmtree(tmpdir)
    return failed


def _create_package(
    bundle_name, con, skip_upload, output_root, destination_root=None
):
    bundles_by_name = get_bundles(con)

    bundle = bundles_by_name.get(bundle_name)
    if not bundle:
        raise ValueError(f"{bundle_name} not present on the server.")

    bundle_addons_toml = get_bundle_addons_tomls(con, bundle)

    # Installer is not set, dependency package cannot be created
    if bundle.installer_version is None:
        print(f"Bundle '{bundle.name}' does not have set installer.")
        return None

    platform_name = platform.system().lower()
    installer = find_installer_by_name(
        con, bundle_name, bundle.installer_version, platform_name
    )
    installer_toml_data = get_installer_toml(installer)
    full_toml_data = get_full_toml(
        installer_toml_data, bundle_addons_toml, platform_name
    )

    venv_info = prepare_new_venv(output_root, installer)

    solve_dependencies(full_toml_data, output_root, venv_info.venv_path)

    applicable_package = get_applicable_package(con, full_toml_data)
    if applicable_package:
        update_bundle_with_package(con, bundle, applicable_package)
        return applicable_package["filename"]

    (
        runtime_site_packages,
        installed_installer_runtime_deps
    ) = install_poetry(
        full_toml_data, installer, venv_info
    )

    # remove already distributed libraries from addons specific venv
    remove_existing_from_venv(
        venv_info.venv_path,
        installer,
        installed_installer_runtime_deps
    )
    runtime_dependencies = get_runtime_dependencies(
        runtime_site_packages, venv_info.venv_path
    )

    venv_zip_path = prepare_zip_venv(
        venv_info.venv_path,
        runtime_site_packages,
        output_root,
    )

    package_data = prepare_package_data(
        venv_zip_path, bundle, platform_name, runtime_dependencies
    )
    if destination_root:
        stored_package_to_dir(
            destination_root, venv_zip_path, bundle, package_data
        )

    if not skip_upload:
        upload_to_server(con, venv_zip_path, package_data)
        update_bundle_with_package(con, bundle, package_data)

    return package_data["filename"]


def create_package(bundle_name, con=None, output_dir=None, skip_upload=False):
    """Pulls all active addons info from server and create dependency package.

    1. Takes base (installer) pyproject.toml, and adds tomls from addons
        pyproject.toml (if available).
    2. Builds new venv with dependencies only for addons (dependencies already
        present in build are filtered out).
    3. Uploads zipped venv to server and set it to bundle.

    Args:
        bundle_name (str): Name of bundle for which is package created.
        con (Optional[ayon_api.ServerAPI]): Prepared server API object.
        output_dir (Optional[str]): Path to directory where package will be
            created.
        skip_upload (Optional[bool]): Skip upload to server. Default: False.
    """

    # create resolved venv based on distributed venv with Desktop + activated
    # addons
    tmpdir = os.path.realpath(tempfile.mkdtemp(prefix="ayon_dep-package"))
    print(">>> Creating processing directory {} for {}".format(
        tmpdir, bundle_name))

    try:
        if con is None:
            con = ayon_api.get_server_api_connection()
        return _create_package(
            bundle_name, con, skip_upload, tmpdir, output_dir
        )

    finally:
        print(">>> Cleaning up processing directory {}".format(tmpdir))
        failed_paths = _remove_tmpdir(tmpdir)
        if failed_paths:
            print("Failed to cleanup tempdir: {}".format(tmpdir))
            print("\n".join(sorted(failed_paths)))
