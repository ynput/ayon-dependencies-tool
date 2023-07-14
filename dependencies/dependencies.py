import os
import datetime
import shutil
import re
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


def get_installer_toml(bundle_name, installer_name):
    """Returns dict with format matching of .toml file for `installer_name`.

    Queries info from server for `bundle_name` and its `installer_name`,
    transforms its list of python dependencies into dictionary matching format
    of `.toml`

    Args:
        bundle_name (str)
        installer_name (str)
    Returns:
        (dict) {"tool": {"poetry": {"dependencies": {"aaa": ">=1.0.0"...}}}}
    """
    installers_by_name = {installer["version"]: installer
                          for installer in
                          ayon_api.get_installers()["installers"]}
    installer = installers_by_name.get(installer_name)
    if not installer:
        raise ValueError(f"{bundle_name} must have installer present.")
    poetry = {"dependencies": installer["pythonModules"]}
    return {"tool": {"poetry": poetry}, "openpype": {"thirdparty": {}}}


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

            resolved_vers = str(resolved_vers)
            if dependency == "python":
                resolved_vers = "3.9.*"  # TEMP TODO

            if resolved_vers == "<empty>":
                raise ValueError(f"Version {dep_version} cannot be resolved against {main_version} for {addon_name}")  # noqa

            main_poetry[dependency] = resolved_vers

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
    if isinstance(dep_version, dict):
        dep_version = dep_version["version"]
    if isinstance(main_version, dict):
        main_version = main_version["version"]
    if dep_version and _is_url_constraint(dep_version):
        # custom location for addon should take precedence
        return dep_version

    if main_version and _is_url_constraint(main_version):
        return main_version

    if not main_version:
        return parse_constraint(dep_version)
    if not dep_version:
        return parse_constraint(main_version)
    return parse_constraint(dep_version).intersect(
                parse_constraint(main_version))


def _is_url_constraint(version):
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

    tool_poetry = {}
    tool_poetry["name"] = "TestAddon"
    tool_poetry["version"] = "1.0.0"
    tool_poetry["description"] = "Test Openpype Addon"
    tool_poetry["authors"] = ["OpenPype Team <info@openpype.io>"]
    tool_poetry["license"] = "MIT License"
    full_toml_data["tool"]["poetry"].update(tool_poetry)

    _convert_url_constraints(full_toml_data)

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
    print(" ".join(cmd_args))
    return run_subprocess(cmd_args)


def _convert_url_constraints(full_toml_data):
    """Converts string occurences of "git+https" to dict required by Poetry"""
    dependency_keyes = ["dependencies", "dev-dependencies"]
    for key in dependency_keyes:
        dependencies = full_toml_data["tool"]["poetry"].get(key) or {}
        for dependency, dep_version in dependencies.items():
            revision = None
            dep_version = str(dep_version)
            if _is_url_constraint(dep_version):
                if "@" in dep_version:
                    dep_version, revision = dep_version.split("@")
                if dep_version.startswith("http"):
                    dependencies[dependency] = {"url": dep_version}
                if "git+" in dep_version:
                    dep_version = dep_version.replace("git+", "")
                    dependencies[dependency] = {"git": dep_version}
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
            if item.startswith("pip"):
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


def create_base_venv(base_toml_data, tmpdir):
    """ create base venv - distributed with Desktop

    Used to filter out already installed libraries later

    Args:
        base_toml_data (dict): content of toml for Desktop app
    """
    base_venv_path = os.path.join(tmpdir, ".base_venv")
    os.makedirs(base_venv_path)
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


def get_python_modules(venv_path):
    """Uses pip freeze to get installed libraries from `venv_path`.

    Args:
        venv_path (str): absolute path to created dependency package already
            with removed libraries from installer package
    Returns:
        (dict) {'acre': '1.0.0',...}
    """
    low_platform = platform.system().lower()
    if low_platform == "windows":
        bin_folder = "Scripts"
    else:
        bin_folder = "bin"

    pip_executable = os.path.join(venv_path, bin_folder, "pip")

    req_path = os.path.join(venv_path, "requirements.txt")
    cmd_args = [
        pip_executable,
        "freeze",
        "-v",
        venv_path,
        ">>",
        req_path
    ]
    print(" ".join(cmd_args))
    return_code = run_subprocess(cmd_args, shell=True)
    if return_code != 0:
        raise RuntimeError(f"Preparation of {req_path} failed!")

    with open(req_path, "r") as f:
        requirements = f.readlines()

    packages = {}
    for requirement in requirements:
        requirement = requirement.strip()
        requirement = requirement.replace("\x1b[0m", "")
        if not requirement or requirement.startswith("#"):
            continue

        match = re.match(r"^(.+?)(?:==|>=|<=|~=|!=|@)(.+)$", requirement)
        if match:
            package_name, version = match.groups()
            packages[package_name] = version
        else:
            packages[requirement] = None

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

    venv_path = os.path.join(os.path.dirname(venv_zip_path), ".venv")
    python_modules = get_python_modules(venv_path)

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


# TODO copy from openpype.lib.execute, could be imported directly??
def run_subprocess(*args, **kwargs):
    """Convenience method for getting output errors for subprocess.

    Output logged when process finish.

    Entered arguments and keyword arguments are passed to subprocess Popen.

    Args:
        *args: Variable length arument list passed to Popen.
        **kwargs : Arbitrary keyword arguments passed to Popen. Is possible to
            pass `logging.Logger` object under "logger" if want to use
            different than lib's logger.

    Returns:
        str: Full output of subprocess concatenated stdout and stderr.

    Raises:
        RuntimeError: Exception is raised if process finished with nonzero
            return code.
    """

    # Get environents from kwarg or use current process environments if were
    # not passed.
    env = kwargs.get("env") or os.environ
    # Make sure environment contains only strings
    filtered_env = {str(k): str(v) for k, v in env.items()}

    # Use lib's logger if was not passed with kwargs.
    logger = kwargs.pop("logger", None)
    if logger is None:
        logger = logging.getLogger("dependencies_tool")

    # set overrides
    kwargs['stdout'] = kwargs.get('stdout', subprocess.PIPE)
    kwargs['stderr'] = kwargs.get('stderr', subprocess.PIPE)
    kwargs['stdin'] = kwargs.get('stdin', subprocess.PIPE)
    kwargs['env'] = filtered_env

    proc = subprocess.Popen(*args, **kwargs)
    _stdout, _stderr = proc.communicate()

    if _stdout:
        print("\n\nOutput:\n{}".format(_clean_color_codes(str(_stdout))))

    if proc.returncode != 0:
        exc_msg = "Executing arguments was not successful: \"{}\"".format(args)
        if _stderr:
            exc_msg += "\n\nError:\n{}".format(
                _clean_color_codes(str(_stderr)))

        raise RuntimeError(exc_msg)

    return proc.returncode


def _clean_color_codes(text):
    """Completely incomplete clearing of color tags"""
    patterns = {
        '\\x1b[39m': "",
        '\\x1b[39': "",
        '\\x1b[36m': "",
        '\\x1b[34m': "",
        '\\x1b[32m': "",
        ';2m': '',
        ';22m': '',
        ';1m': '',
        '\\x1b[39325': '',
        '\\x1b[39;22m\\x1b[39\\xe2\\x94\\x82\\x1b[39;22m': '',
        ';22m\\xe2\\x94\\x82;22m': '',
        '\\x1b[34;1m\\xe2\\x80\\xa2': '',
        "\\r\\n": "\n"
    }

    for pattern, replacement in patterns.items():
        text = text.replace(pattern, replacement)

    return text


def create_package(bundle_name):
    """
        Pulls all active addons info from server, provides their pyproject.toml
    (if available), takes base (installer) pyproject.toml, adds tomls from
    addons.
    Builds new venv with dependencies only for addons (dependencies already
    present in build are filtered out).
    Uploads zipped venv back to server.

    Expects env vars:
        AYON_SERVER_URL
        AYON_API_KEY
    """
    bundles_by_name = get_bundles()

    bundle = bundles_by_name.get(bundle_name)
    if not bundle:
        raise ValueError(f"{bundle_name} not present on the server.")

    bundle_addons_toml = get_bundle_addons_tomls(bundle)

    installer_toml_data = get_installer_toml(bundle_name,
                                             bundle.installerVersion)
    full_toml_data = get_full_toml(installer_toml_data, bundle_addons_toml)

    applicable_package_name = get_applicable_package(full_toml_data)
    if applicable_package_name:
        update_bundle_with_package(bundle, applicable_package_name)
        return applicable_package_name

    # create resolved venv based on distributed venv with Desktop + activated
    # addons
    tmpdir = tempfile.mkdtemp()
    base_venv_path = create_base_venv(installer_toml_data, tmpdir)
    addons_venv_path = create_addons_venv(full_toml_data, tmpdir)

    # remove already distributed libraries from addons specific venv
    remove_existing_from_venv(base_venv_path, addons_venv_path)

    venv_zip_path = prepare_zip_venv(tmpdir)

    package_name = upload_to_server(venv_zip_path, bundle)

    update_bundle_with_package(bundle, package_name)

    shutil.rmtree(tmpdir)

    return package_name


def main(server_url, api_key, bundle_name):
    """Main endpoint to trigger full process.

    Sets env vars: (needed for ayon_api connection)
        AYON_SERVER_URL
        AYON_API_KEY

    Args:
        server_url (string): hostname + port for v4 Server
            default value is http://localhost:5000
        api_key (str): generated api key for service account
        bundle_name (str): from Ayon server
    Returns:
        (string) name of created package
    """
    os.environ["AYON_SERVER_URL"] = server_url
    os.environ["AYON_API_KEY"] = api_key

    return create_package(bundle_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url",
                        help="Url of v4 server")
    parser.add_argument("--api-key",
                        help="Api key")
    parser.add_argument("--bundle-name",
                        help="Bundle name for which dep package is created")

    kwargs = parser.parse_args(sys.argv[1:]).__dict__

    # << for development only
    # kwargs = {}
    # with open("../.env") as fp:
    #     for line in fp:
    #         if not line:
    #             continue
    #         key, value = line.split("=")
    #         os.environ[key] = value.strip()
    #         kwargs[key.replace("AYON_", "").strip().lower()] = value.strip().lower()
    # kwargs["bundle_name"] = "Everything"
    # for development only >>

    main(**kwargs)
