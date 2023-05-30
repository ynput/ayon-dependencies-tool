import os
import shutil
import tempfile
import toml
import abc
import six
from packaging import version
import subprocess
import platform
import sys
import sysconfig
import hashlib
import logging
import argparse
from poetry.core.constraints.version import parse_constraint
import zipfile

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

        for addon_name, addon in get_production_addons(self.server_endpoint).items():
            if not addon.get("clientPyproject"):
                continue
            tomls[addon_name] = addon["clientPyproject"]

        return tomls


def get_production_addons(server_endpoint):
    """Returns dict of dicts with addon info.

    Dict keys are names of production addons (example_1.0.0)
    """
    con = ayon_api.create_connection()

    response = con.get(server_endpoint)

    production_addons = {}
    for addon in response.data["addons"]:
        production_version = addon.get("productionVersion")
        if not production_version:
            continue
        addon_ver = addon["versions"][production_version]
        addon_name = f"{addon['name']}_{production_version}"
        production_addons[addon_name] = addon_ver

    return production_addons


def get_addon_tomls():
    """Provides list of dict containing addon tomls.

    Returns:
        (dict) of (dict)
    """
    server_endpoint = "addons?details=1"

    return ServerTomlProvider(server_endpoint).get_tomls()


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


def get_venv_zip_name(lock_file_path):
    """Creates zip file name for new venv.

    File name contains python version used when generating venv, platform and
    hash of installed libraries from .lock file

    Args:
        lock_file_path (str)
    Returns:
        (str):
        example 'openpype-win-amd64-python3.7.9-d64f07e555c5dd65034c9186192869e78b08390d.zip'  # noqa
        File name is far below max file name size limit so far, so no need to
        some clever trimming for now
    """
    ver = sys.version_info
    platform = sysconfig.get_platform()
    python_version = "python{}.{}.{}".format(ver.major, ver.minor, ver.micro)

    with open(lock_file_path) as fp:
        hash = hashlib.sha1(fp.read().encode('utf-8')).hexdigest()

    return "openpype-{}-{}-{}.zip".format(platform, python_version, hash)


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

    _stdout = proc.stdout
    _stderr = proc.stderr

    while proc.poll() is None:
        line = str(proc.stdout.readline())
        sys.stdout.write(line+"\n")
        sys.stdout.flush()
        if "version solving failed" in line:  # tempo, shouldnt be necessary
            proc.kill()
            proc.returncode = 1
            break

    if proc.returncode != 0:
        exc_msg = "Executing arguments was not successful: \"{}\"".format(args)
        if _stdout:
            exc_msg += "\n\nOutput:\n{}".format(_stdout)

        if _stderr:
            exc_msg += "Error:\n{}".format(_stderr)

        raise RuntimeError(exc_msg)

    return proc.returncode


def main(server_url, api_key, main_toml_path):
    """Main endpoint to trigger full process.

    Pulls all active addons info from server, provides their pyproject.toml
    (if available), takes base (build) pyproject.toml, adds tomls from addons.
    Builds new venv with dependencies only for addons (dependencies already
    present in build are filtered out).
    Uploads zipped venv back to server.

    Args:
        server_url (string): hostname + port for v4 Server
            default value is http://localhost:5000
        api_key (str): generated api key for service account
        main_toml_path (str): locally assessible path to `pyproject.toml`
            bundled with Ayon Desktop
    """
    os.environ["AYON_SERVER_URL"] = server_url
    os.environ["AYON_API_KEY"] = api_key

    base_toml_data = FileTomlProvider(main_toml_path).get_toml()

    addon_tomls = get_addon_tomls()
    full_toml_data = get_full_toml(base_toml_data, addon_tomls)

    # create resolved venv based on distributed venv with Desktop + activated
    # addons
    tmpdir = tempfile.mkdtemp()
    print(f"pPreparing new venv in {tmpdir}")
    return_code = prepare_new_venv(full_toml_data, tmpdir)

    if return_code != 0:
        raise RuntimeError(f"Preparation of {tmpdir} failed!")
    addons_venv_path = os.path.join(tmpdir, ".venv")

    # create base venv - distributed with Desktop
    base_venv_path = os.path.join(tmpdir, ".base_venv")
    os.makedirs(base_venv_path)
    shutil.copy(main_toml_path, base_venv_path)
    print(f"pPreparing new base venv in {base_venv_path}")
    return_code = prepare_new_venv(base_toml_data, base_venv_path)

    if return_code != 0:
        raise RuntimeError(f"Preparation of {base_venv_path} failed!")
    base_venv_path = os.path.join(base_venv_path, ".venv")

    # remove already distributed libraries from addons specific venv
    remove_existing_from_venv(base_venv_path,
                              addons_venv_path)

    zip_file_name = get_venv_zip_name(os.path.join(tmpdir, "poetry.lock"))
    venv_zip_path = os.path.join(tmpdir, zip_file_name)
    print(f"pZipping new venv to {venv_zip_path}")
    zip_venv(os.path.join(tmpdir, ".venv"),
             venv_zip_path)

    package_name = upload_to_server(venv_zip_path)

    shutil.rmtree(tmpdir)

    return package_name


def calculate_hash(file_url):
    with open(file_url, "rb") as f:
        checksum = hashlib.md5()
        chunk = f.read(8192)
        while chunk:
            checksum.update(chunk)
            chunk = f.read(8192)

    return checksum.hexdigest()


def upload_to_server(venv_zip_path):
    """Creates and uploads package on the server
    Args:
        venv_zip_path (str): local path to zipped venv
    Returns:
        (str): package name for logging
    Raises:
          (RuntimeError)
    """
    server_endpoint = "addons?details=1"
    supported_addons = {}
    for name in get_production_addons(server_endpoint).keys():
        splitted = name.split("_")
        supported_addons[splitted[0]] = splitted[1]

    platform_name = platform.system().lower()
    package_name = os.path.splitext(os.path.basename(venv_zip_path))[0]
    checksum = calculate_hash(venv_zip_path)
    ayon_api.update_dependency_info(package_name, platform_name,
                                    os.stat(venv_zip_path).st_size,
                                    str(checksum),
                                    supported_addons=supported_addons)

    ayon_api.upload_dependency_package(venv_zip_path, package_name,
                                       platform_name)

    return package_name


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url",
                        help="Url of v4 server")
    parser.add_argument("--api-key",
                        help="Api key")
    parser.add_argument("--main-toml-path",
                        help="Path to universal toml with basic dependencies")

    kwargs = parser.parse_args(sys.argv[1:]).__dict__

    toml_path = os.path.abspath("tests\\resources\\pyproject_clean.toml")
    kwargs = {
        "server_url": "https://ayon.dev",
        "api_key": "6605361c1e3e42db993a44146052847a",
        #'main_toml_path': 'C:\\Users\\petrk\\PycharmProjects\\Pype3.0\\pype\\pyproject.toml'
        "main_toml_path": toml_path
    }

    main(**kwargs)
