import os
import shutil
import tempfile
import toml
import abc
import six
from packaging import version
import subprocess
import platform
import requests
import sys
import sysconfig
import hashlib
import logging
import json
import argparse

from common.openpype_common.distribution.file_handler import RemoteFileHandler


# This tool expects be deployed in a directory next to pype repo for time being
# It is using pyproject.lock and tools/create_env.* script
OPENPYPE_ROOT_FOLDER = (os.getenv("OPENPYPE_ROOT") or
                        os.path.join(os.path.dirname(__file__), "../../pype"))


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
            Returns list of dict containing toml information

        Some providers (http) are returning all tomls in one go.
        Returns:
            (list) of (dict)
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
        tomls = []

        for addon in get_production_addons(self.server_endpoint).values():
            if not addon.get("clientPyproject"):
                continue
            tomls.append(addon["clientPyproject"])

        return tomls


def get_production_addons(server_endpoint):
    """Returns dict of dicts with addon info.

    Dict keys are names of production addons (example_1.0.0)
    """
    response = requests.get(server_endpoint)

    production_addons = {}
    for addon in response.json()["addons"]:
        production_version = addon.get("productionVersion")
        if not production_version:
            continue
        addon_ver = addon["versions"][production_version]
        addon_name = f"{addon['name']}_{production_version}"
        production_addons[addon_name] = addon_ver

    return production_addons


def get_addon_tomls(server_url):
    """Provides list of dict containing addon tomls.

    Args:
        server_url (str): host name with port, without endpoint
    Returns:
        (list) of (dict)
    """
    server_endpoint = server_url + "/api/addons?details=1"

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


def merge_tomls(main_toml, addon_toml):
    """Add dependencies from 'addon_toml' to 'main_toml'.

    Looks for mininimal compatible version from both tomls.

    Handles sections:
        - ["tool"]["poetry"]["dependencies"]
        - ["tool"]["poetry"][""-dependencies"]
        - ["openpype"]["thirdparty"]

    Returns:
        (dict): updated 'main_toml' with additional/updated dependencies
    """
    dependency_keyes = ["dependencies", "dev-dependencies"]
    for key in dependency_keyes:
        main_poetry = main_toml["tool"]["poetry"].get(key) or {}
        addon_poetry = addon_toml["tool"]["poetry"].get(key) or {}
        for dependency, dep_version in addon_poetry.items():
            if main_poetry.get(dependency):
                main_version = main_poetry[dependency]
                # max ==  smaller from both versions
                dep_version = max(version.parse(dep_version),
                                  version.parse(main_version))

            if dep_version:
                main_poetry[dependency] = str(dep_version)

        main_toml["tool"]["poetry"][key] = main_poetry

    # handle thirdparty
    platform_name = platform.system().lower()

    addon_poetry = addon_toml.get("openpype", {}).get("thirdparty", {})
    main_poetry = main_toml["openpype"]["thirdparty"]  # reset level
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

            if version.parse(dep_version) > version.parse(main_version):
                dep_info = main_poetry[dependency]

        if dep_info:
            main_poetry[dependency] = dep_info

    main_toml["openpype"]["thirdparty"] = main_poetry

    return main_toml


def get_full_toml(base_toml_data, addon_tomls):
    """Loops through list of local addon folder paths to create full .toml

    Full toml is used to calculate set of python dependencies for all enabled
    addons.

    Args:
        base_toml_data (dict): content of pyproject.toml in the root
        addon_tomls (list): content of addon pyproject.toml
    Returns:
        (dict) updated base .toml
    """
    for addon_toml_data in addon_tomls:
        if isinstance(addon_toml_data, str):
            addon_toml_data = toml.loads(addon_toml_data)
        base_toml_data = merge_tomls(base_toml_data, addon_toml_data)

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

    pype_root = os.path.abspath(OPENPYPE_ROOT_FOLDER)
    create_env_script_path = os.path.join(pype_root, "tools",
                                          f"create_env.{ext}")
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
    RemoteFileHandler.zip(venv_folder, zip_destination_path)


def upload_zip_venv(zip_path, server_endpoint, session):
    """Uploads zipped venv to the server for distribution.

    Args:
        zip_path (str): local path to zipped venv
        server_endpoint (str)
        session (requests.Session)
    """
    if not os.path.exists(zip_path):
        raise RuntimeError(f"{zip_path} doesn't exist")

    with open(zip_path, "rb") as fp:
        r = session.post(server_endpoint, data=fp)


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
        line = proc.stdout.readline()
        sys.stdout.write(str(line)+"\n")
        sys.stdout.flush()

    if proc.returncode != 0:
        exc_msg = "Executing arguments was not successful: \"{}\"".format(args)
        if _stdout:
            exc_msg += "\n\nOutput:\n{}".format(_stdout)

        if _stderr:
            exc_msg += "Error:\n{}".format(_stderr)

        raise RuntimeError(exc_msg)

    return proc.returncode


def main(server_url, user, password):
    """Main endpoint to trigger full process.

    Pulls all active addons info from server, provides their pyproject.toml
    (if available), takes base (build) pyproject.toml, adds tomls from addons.
    Builds new venv with dependencies only for addons (dependencies already
    present in build are filtered out).
    Uploads zipped venv back to server.

    Args:
        server_url (string): hostname + port for v4 Server
            default value is http://localhost:5000
    """

    tmpdir = tempfile.mkdtemp()

    base_toml_data = FileTomlProvider(os.path.join(OPENPYPE_ROOT_FOLDER,
                                      "pyproject.toml")).get_toml()

    addon_tomls = get_addon_tomls(server_url)
    full_toml_data = get_full_toml(base_toml_data, addon_tomls)
    print(f"pPreparing new venv in {tmpdir}")
    return_code = prepare_new_venv(full_toml_data, tmpdir)

    if return_code != 0:
        raise RuntimeError("Preparation of venv failed!")

    base_venv_path = os.path.join(OPENPYPE_ROOT_FOLDER, ".venv")
    addon_venv_path = os.path.join(tmpdir, ".venv")

    remove_existing_from_venv(base_venv_path, addon_venv_path)
    zip_file_name = get_venv_zip_name(os.path.join(tmpdir, "poetry.lock"))
    venv_zip_path = os.path.join(tmpdir, zip_file_name)
    print(f"pZipping new venv to {venv_zip_path}")
    zip_venv(os.path.join(tmpdir, ".venv"),
             venv_zip_path)

    upload_to_server(server_url, venv_zip_path, user, password)

    shutil.rmtree(tmpdir)


def calculate_hash(file_url):
    with open(file_url, "rb") as f:
        checksum = hashlib.md5()
        chunk = f.read(8192)
        while chunk:
            checksum.update(chunk)
            chunk = f.read(8192)

    return checksum.hexdigest()


def upload_to_server(server_url, venv_zip_path, user, password):
    """Creates and uploads package on the server
    Args:
        server_url (str)
        venv_zip_path (str): local path to zipped venv
        user (str)
        password (str)
    Raises:
          (RuntimeError)
    """
    token = login(server_url, user, password)
    if not token:
        raise RuntimeError("Cannot login to server")

    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    })

    server_endpoint = f"{server_url}/api/addons?details=1"

    supported_addons = {}
    for name in get_production_addons(server_endpoint).keys():
        splitted = name.split("_")
        supported_addons[splitted[0]] = splitted[1]

    platform_name = platform.system().lower()
    package_name = os.path.splitext(os.path.basename(venv_zip_path))[0]
    checksum = calculate_hash(venv_zip_path)
    data = {"name": package_name,
            "platform": platform_name,
            "size": os.stat(venv_zip_path).st_size,
            "checksum": str(checksum),
            "supportedAddons": supported_addons}

    server_endpoint = f"{server_url}/api/dependencies"
    response = session.put(server_endpoint, data=json.dumps(data))
    if response.status_code != 201:
        raise RuntimeError("Cannot store package metadata on server")

    server_endpoint = f"{server_endpoint}/{package_name}/{platform_name}"
    session.headers.update({
        "Content-Type": "application/octet-stream"
    })
    upload_zip_venv(venv_zip_path, server_endpoint, session)


def login(url, username, password):
    """Use login to the server to receive token.

    Args:
        url (str): Server url.
        username (str): User's username.
        password (str): User's password.

    Returns:
        Union[str, None]: User's token if login was successful.
            Otherwise 'None'.
    """

    headers = {"Content-Type": "application/json"}
    response = requests.post(
        "{}/api/auth/login".format(url),
        headers=headers,
        json={
            "name": username,
            "password": password
        },
        timeout=5
    )
    token = None
    # 200 - success
    # 401 - invalid credentials
    # *   - other issues
    if response.status_code == 200:
        token = response.json()["token"]

    return token


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url",
                        help="Url of v4 server")
    parser.add_argument("--user",
                        help="User name to login")
    parser.add_argument("--password",
                        help="Password to login")

    kwargs = parser.parse_args(sys.argv[1:]).__dict__
    main(**kwargs)

