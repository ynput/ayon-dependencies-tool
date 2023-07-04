import os
import datetime
import shutil
import tempfile
import toml
import abc
import six
from packaging import version
import subprocess
import platform
import sys
import hashlib
import logging
import argparse
from poetry.core.constraints.version import parse_constraint
import zipfile
import attr
from typing import Dict, List

import ayon_api


@six.add_metaclass(abc.ABCMeta)
class AbstractTomlProvider:
    """Interface class to base real toml data providers."""
    @abc.abstractmethod
    def get_toml(self):
        """
            Returns dict containing toml information


        Returns:
            (dict)
        """
        pass

    @abc.abstractmethod
    def get_tomls(self):
        """
            Returns dict of dict containing toml information

        Some providers (http) are returning all tomls in one go.
        Returns:
            (dict) of (dict)
            { "example": {"poetry":{}..}}
        """
        pass


class FileTomlProvider(AbstractTomlProvider):
    """Class that parses toml from 'source_url' into dictionary."""
    def __init__(self, source_url):
        self.source_url = os.path.abspath(source_url)

    def get_toml(self):
        if not os.path.exists(self.source_url):
            raise ValueError(f"{self.source_url} doesn't exist. "
                             "Provide path to real toml.")

        with open(self.source_url) as fp:
            return toml.load(fp)

    def get_tomls(self):
        raise NotImplementedError


class ServerTomlProvider(AbstractTomlProvider):
    """Class that parses tomls from 'server_endpoint' into dictionary."""
    def __init__(self, server_endpoint):
        self.server_endpoint = server_endpoint

    def get_toml(self):
        raise NotImplementedError

    def get_tomls(self):
        tomls = {}

        con = ayon_api.create_connection()

        response = con.get(self.server_endpoint)

        for addon_dict in response.data["addons"]:
            addon = Addon(**addon_dict)
            for version_name, addon_version_dict in addon.versions.items():
                addon_version = AddonVersion(**addon_version_dict)
                if not addon_version.clientPyproject:
                    continue
                addon_version.name = version_name
                addon_version.full_name = f"{addon.name}_{version_name}"
                tomls[addon_version.full_name] = addon_version.clientPyproject

        return tomls


@attr.s
class Bundle:
    name: str = attr.ib()
    createdAt: str = attr.ib()
    isProduction: bool = attr.ib()
    isStaging: bool = attr.ib()
    installerVersion: str = attr.ib(default=None)
    addons: Dict[str, str] = attr.ib(default={})
    dependencyPackages: Dict[str, str] = attr.ib(default={})


@attr.s
class Addon:
    name: str = attr.ib()
    title: str = attr.ib()
    description: str = attr.ib()
    productionVersion: bool = attr.ib(default=None)  # TODO
    stagingVersion: bool = attr.ib(default=None)
    versions: Dict[str, str] = attr.ib(default={})


@attr.s
class AddonVersion:
    hasSettings: bool = attr.ib()
    hasSiteSettings: bool = attr.ib()
    frontendScopes: Dict[str, str] = attr.ib()
    name: str = attr.ib(default=None)
    full_name: str = attr.ib(default=None)
    clientPyproject: Dict[str, str] = attr.ib(default={})
    clientSourceInfo: List[str] = attr.ib(default=[])
    services: List[str] = attr.ib(default=[])


def get_bundles():
    """Provides dictionary with available bundles

    Returns:
        (dict) of (Bundle) {"BUNDLE_NAME": Bundle}
    """
    bundles_by_name = {}
    for bundle_dict in ayon_api.get_bundles()["bundles"]:
        bundle = Bundle(**bundle_dict)
        bundles_by_name[bundle.name] = bundle
    return bundles_by_name


def get_all_addon_tomls():
    """Provides list of dict containing addon tomls.

    Returns:
        (dict) of (dict)
    """
    server_endpoint = "addons?details=1"

    return ServerTomlProvider(server_endpoint).get_tomls()


def get_bundle_addons_tomls(bundle):
    """Query addons for `bundle` to get their python dependencies.

    Returns:
        (dict) of (dict)  {'core_1.0.0': {toml}}
    """
    bundle_addons = [f"{key}_{value}"
                     for key, value in bundle.addons.items()
                     if value is not None]
    addon_tomls = get_all_addon_tomls()

    bundle_addons_toml = {}
    for addon_full_name, toml in addon_tomls.items():
        if addon_full_name in bundle_addons:
            bundle_addons_toml[addon_full_name] = toml
    return bundle_addons_toml


def is_valid_toml(toml):
    """Validates that 'toml' contains all required fields.

    Args:
        toml (dict)
    Returns:
        True if all required keys present
    Raises:
        KeyError
    """
    required_fields = ["tool.poetry"]

    for field in required_fields:
        fields = field.split('.')
        value = toml
        while fields:
            key = fields.pop(0)
            value = value.get(key)

            if not value:
                raise KeyError(f"Toml content must contain {field}")

    return True


def merge_tomls(main_toml, addon_toml, addon_name):
    """Add dependencies from 'addon_toml' to 'main_toml'.

    Looks for mininimal compatible version from both tomls.

    Handles sections:
        - ["tool"]["poetry"]["dependencies"]
        - ["tool"]["poetry"][""-dependencies"]
        - ["openpype"]["thirdparty"]

    Returns:
        (dict): updated 'main_toml' with additional/updated dependencies

    Raises:
        ValueError if any tuple of main and addon dependency cannot be resolved
    """
    dependency_keyes = ["dependencies", "dev-dependencies"]
    for key in dependency_keyes:
        main_poetry = main_toml["tool"]["poetry"].get(key) or {}
        addon_poetry = addon_toml["tool"]["poetry"].get(key) or {}
        for dependency, dep_version in addon_poetry.items():
            if main_poetry.get(dependency):
                main_version = main_poetry[dependency]
                resolved_vers = _get_correct_version(main_version, dep_version)
            else:
                resolved_vers = dep_version

            if dependency == "python":
                resolved_vers = "3.9.*"

            if str(resolved_vers) == "<empty>":
                raise ValueError(f"Version {dep_version} cannot be resolved against {main_version} for {addon_name}")  # noqa

            main_poetry[dependency] = str(resolved_vers)

        main_toml["tool"]["poetry"][key] = main_poetry

    # handle thirdparty
    platform_name = platform.system().lower()

    addon_poetry = addon_toml.get("openpype", {}).get("thirdparty", {})
    main_poetry = main_toml["openpype"].get("thirdparty", {})  # reset level
    for dependency, dep_info in addon_poetry.items():
        if main_poetry.get(dependency):
            if dep_info.get(platform_name):
                dep_version = dep_info[platform_name]["version"]
                main_version = (main_poetry[dependency]
                                           [platform_name]
                                           ["version"])
            else:
                dep_version = dep_info["version"]
                main_version = main_poetry[dependency]["version"]

            result_range = _get_correct_version(main_version, dep_version)
            if (str(result_range) != "<empty>" and
                    parse_constraint(dep_version).allows(result_range)):
                dep_info = main_poetry[dependency]
            else:
                raise ValueError(f"Cannot result {dependency} with {dep_info} for {addon_name}")  # noqa

        if dep_info:
            main_poetry[dependency] = dep_info

    main_toml["openpype"]["thirdparty"] = main_poetry

    return main_toml


def _get_correct_version(main_version, dep_version):
    """Return resolved version from two version (constraint).

    Arg:
        main_version (str): version or constraint ("3.6.1", "^3.7")
        dep_version (str): dtto
    Returns:
        (VersionRange| EmptyConstraint if cannot be resolved)
    """
    if not main_version:
        return parse_constraint(dep_version)
    if not dep_version:
        return parse_constraint(main_version)
    return parse_constraint(dep_version).intersect(
                parse_constraint(main_version))


def _version_parse(version_value):
    """Handles different formats of versions

    Parses:
        "^2.0.0"
        { version = "301", markers = "sys_platform == 'win32'" }
    """
    if isinstance(version_value, dict):
        return version_value.get("version")
    return version.parse(version_value)


def get_full_toml(base_toml_data, addon_tomls):
    """Loops through list of local addon folder paths to create full .toml

    Full toml is used to calculate set of python dependencies for all enabled
    addons.

    Args:
        base_toml_data (dict): content of pyproject.toml in the root
        addon_tomls (dict): content of addon pyproject.toml
    Returns:
        (dict) updated base .toml
    """
    for addon_name, addon_toml_data in addon_tomls.items():
        if isinstance(addon_toml_data, str):
            addon_toml_data = toml.loads(addon_toml_data)
        base_toml_data = merge_tomls(base_toml_data, addon_toml_data,
                                     addon_name)

    return base_toml_data


def prepare_new_venv(full_toml_data, venv_folder):
    """Let Poetry create new venv in 'venv_folder' from 'full_toml_data'.

    Args:
        full_toml_data (dict): toml representation calculated based on basic
            .toml + all addon tomls
        venv_folder (str): path where venv should be created
    Raises:
        RuntimeError: Exception is raised if process finished with nonzero
            return code.
    """
    toml_path = os.path.join(venv_folder, "pyproject.toml")

    with open(toml_path, 'w') as fp:
        fp.write(toml.dumps(full_toml_data))

    low_platform = platform.system().lower()

    if low_platform == "windows":
        ext = "ps1"
        executable = "powershell"
    else:
        ext = "sh"
        executable = "bash"

    create_env_script_path = os.path.abspath(os.path.join(
                                                os.path.dirname(__file__),
                                                "tools",
                                                f"create_env.{ext}"))
    if not os.path.exists(create_env_script_path):
        raise RuntimeError(
            f"Expected create_env script here {create_env_script_path}")

    cmd_args = [
        executable,
        create_env_script_path,
        "-venv_path", os.path.join(venv_folder, ".venv"),
        "-verbose"
    ]
    return run_subprocess(cmd_args)


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
    parsed = FileTomlProvider(lock_path).get_toml()

    dependencies = {}
    for package_info in parsed["package"]:
        dependencies[package_info["name"]] = package_info["version"]

    toml = {"tool": {"poetry": {"dependencies": {}}}}
    toml["tool"]["poetry"]["dependencies"] = dependencies

    return toml


def remove_existing_from_venv(base_venv_path, addons_venv_path):
    """Loop through calculated addon venv and remove already installed libs.

    Args:
        base_venv_path (str): path to base venv of build
        addons_venv_path (str): path to newly created merged venv for active
            addons
    Returns:
        (set) of folder/file paths that were removed from addon venv, used only
            for testing
    """
    checked_subfolders = os.path.join("Lib", "site-packages")
    base_content = set(os.listdir(os.path.join(base_venv_path,
                                               checked_subfolders)))

    removed = set()
    installed_path = os.path.join(addons_venv_path, checked_subfolders)
    for item in os.listdir(installed_path):
        if item in base_content:
            if item.startswith("_"):
                print(f"Keep internal {item}")
                continue
            path = os.path.join(installed_path, item)
            removed.add(item)
            print(f"Removing {path}")
            if os.path.isdir(path):
                shutil.rmtree(path)
            if os.path.isfile(path):
                os.remove(path)

    return removed


def zip_venv(venv_folder, zip_destination_path):
    """Zips newly created venv to single .zip file."""
    temp_dir_to_zip_s = venv_folder.replace("\\", "/")
    with zipfile.ZipFile(zip_destination_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirnames, filenames in os.walk(venv_folder):
            root_s = root.replace("\\", "/")
            zip_root = root_s.replace(temp_dir_to_zip_s, "").strip("/")
            for name in sorted(dirnames):
                path = os.path.normpath(os.path.join(root, name))
                zip_path = name
                if zip_root:
                    zip_path = "/".join((zip_root, name))
                zipf.write(path, zip_path)

            for name in filenames:
                path = os.path.normpath(os.path.join(root, name))
                zip_path = name
                if zip_root:
                    zip_path = "/".join((zip_root, name))
                if os.path.isfile(path):
                    zipf.write(path, zip_path)


def prepare_zip_venv(tmpdir):
    """Handles creation of zipped venv.

    Args:
        tmpdir (str): temp folder path

    Returns:
        (str) path to zipped venv
    """
    # create_dependency_package_basename not yet part of public API
    zip_file_name = f"{_create_dependency_package_basename()}.zip"
    venv_zip_path = os.path.join(tmpdir, zip_file_name)
    print(f"pZipping new venv to {venv_zip_path}")
    zip_venv(os.path.join(tmpdir, ".venv"), venv_zip_path)

    return venv_zip_path


def create_base_venv(base_toml_data, main_toml_path, tmpdir):
    """ create base venv - distributed with Desktop

    Used to filter out already installed libraries later

    Args:
        base_toml_data (dict): content of toml for Desktop app
    """
    base_venv_path = os.path.join(tmpdir, ".base_venv")
    os.makedirs(base_venv_path)
    shutil.copy(main_toml_path, base_venv_path)  # ???
    print(f"pPreparing new base venv in {base_venv_path}")
    return_code = prepare_new_venv(base_toml_data, base_venv_path)
    if return_code != 0:
        raise RuntimeError(f"Preparation of {base_venv_path} failed!")
    base_venv_path = os.path.join(base_venv_path, ".venv")
    return base_venv_path


def create_addons_venv(full_toml_data, tmpdir):
    print(f"Preparing new venv in {tmpdir}")
    return_code = prepare_new_venv(full_toml_data, tmpdir)
    if return_code != 0:
        raise RuntimeError(f"Preparation of {tmpdir} failed!")
    addons_venv_path = os.path.join(tmpdir, ".venv")
    return addons_venv_path


def get_applicable_package(new_toml):
    """Compares existing dependency packages to find matching.

    One dep package could contain same versions of python dependencies for
    different versions of addons (eg. no change in dependency, but change in
    functionality)

    Args:
        new_toml (dict): in a format of regular toml file
    Returns:
        (str) name of matching package
    """
    toml_python_packages = dict(
        sorted(new_toml["tool"]["poetry"]["dependencies"].items())
    )
    for package in ayon_api.get_dependency_packages()["packages"]:
        package_python_packages = dict(sorted(
            package["pythonModules"].items())
        )
        if toml_python_packages == package_python_packages:
            return package["filename"]


def get_python_modules(py_project_path):
    with open(py_project_path, 'r') as f:
        py_project = toml.loads(f.read())

    packages = {}
    dependencies = py_project["tool"]["poetry"]["dependencies"]
    for package_name, package_version in dependencies.items():
        packages[package_name] = package_version

    return packages


def calculate_hash(file_url):
    with open(file_url, "rb") as f:
        checksum = hashlib.md5()
        chunk = f.read(8192)
        while chunk:
            checksum.update(chunk)
            chunk = f.read(8192)

    return checksum.hexdigest()


def upload_to_server(venv_zip_path, bundle):
    """Creates and uploads package on the server
    Args:
        venv_zip_path (str): local path to zipped venv
    Returns:
        (str): package name for logging
        (Bundle): bundle object
    Raises:
          (RuntimeError)
    """
    supported_addons = {}
    for addon_name, addon_version in bundle.addons.items():
        if addon_version is None:
            continue
        supported_addons[addon_name] = addon_version

    py_project_path = os.path.join(os.path.dirname(venv_zip_path),
                                   "pyproject.toml")
    python_modules = get_python_modules(py_project_path)

    platform_name = platform.system().lower()
    package_name = os.path.splitext(os.path.basename(venv_zip_path))[0]
    checksum = calculate_hash(venv_zip_path)

    ayon_api.create_dependency_package(
        filename=package_name,
        python_modules=python_modules,
        source_addons=bundle.addons,
        installer_version=bundle.installerVersion,
        checksum=str(checksum),
        checksum_algorithm="md5",
        file_size=os.stat(venv_zip_path).st_size,
        sources=None,
        platform_name=platform_name,
    )

    ayon_api.upload_dependency_package(venv_zip_path, package_name,
                                       platform_name)

    return package_name


def update_bundle_with_package(bundle, package_name):
    """Assign `package_name` to `bundle`

    Args:
        bundle (Bundle)
        package_name (str)
    """
    print(f"Updating in {bundle.name} with {package_name}")
    platform_str = platform.system().lower()
    bundle.dependencyPackages[platform_str] = package_name
    ayon_api.update_bundle(bundle.name, bundle.dependencyPackages)


def _create_dependency_package_basename(platform_name=None):
    if platform_name is None:
        platform_name = platform.system().lower()

    now_date = datetime.datetime.now()
    time_stamp = now_date.strftime("%y%m%d%H%M")
    return "ayon_{}_{}".format(time_stamp, platform_name)



if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url",
                        help="Url of v4 server")
    parser.add_argument("--api-key",
                        help="Api key")
    parser.add_argument("--main-toml-path",
                        help="Path to universal toml with basic dependencies")
    parser.add_argument("--bundle-name",
                        help="Bundle name for which dep package is created")

    kwargs = parser.parse_args(sys.argv[1:]).__dict__

    # << for development only
    toml_path = os.path.abspath("tests\\resources\\pyproject_clean.toml")
    kwargs = {
        #'main_toml_path': 'C:\\Users\\petrk\\PycharmProjects\\Pype3.0\\pype\\pyproject.toml'
        "main_toml_path": toml_path
    }
    with open(".env") as fp:
        for line in fp:
            if not line:
                continue
            key, value = line.split("=")
            kwargs[key.replace("AYON_", "").strip().lower()] = value.strip().lower()
    kwargs["bundle_name"] = "Core"
    # for development only >>

    main(**kwargs)
