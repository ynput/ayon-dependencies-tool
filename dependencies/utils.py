import os
import sys
import time
import re
import subprocess
import platform
import zipfile

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
PLATFORM_NAME = platform.system().lower()


class VenvInfo:
    def __init__(
        self,
        root: str,
        venv_path: str,
        python_version: str,
        executable_path: str,
    ):
        self.root: str = root
        self.venv_path: str = venv_path
        self.python_version: str = python_version
        self.executable_path: str = executable_path


def _get_venv_root_path(venv_root: str) -> str:
    """Return the actual virtual environment root path.

    The helper accepts either the virtual environment directory itself or the
    project root that contains the default `.venv` directory.
    """

    for candidate in (
        venv_root,
        os.path.join(venv_root, ".venv"),
    ):
        if os.path.isfile(os.path.join(candidate, "pyvenv.cfg")):
            return candidate

    raise FileNotFoundError(
        f"Could not find a virtual environment in: {venv_root}"
    )


def get_venv_executable(venv_root: str) -> str:
    """Get path to executable in virtual environment.

    Args:
        venv_root (str): Path to venv root or project root containing `.venv`.

    """
    # Do not use 'uv run' as it does project discovery in parent
    # directories (e.g. stray 'pyproject.toml' in temp dir) and respects
    # inherited 'VIRTUAL_ENV'.
    venv_path = _get_venv_root_path(venv_root)
    executable_parts = [venv_path]
    if PLATFORM_NAME == "windows":
        executable_parts.extend(["Scripts", "python.exe"])
    else:
        executable_parts.extend(["bin", "python"])

    executable_path = os.path.join(*executable_parts)
    if not os.path.exists(executable_path):
        raise FileNotFoundError(
            f"Could not find venv python executable: {executable_path}"
        )
    return executable_path


def get_venv_python_version(executable_path: str) -> str:
    """Get path to executable in virtual environment.

    Args:
        executable_path (str): Python executable path.

    """
    return subprocess.check_output(
        [
            executable_path,
            "-c", "import platform; print(platform.python_version())"
        ],
        text=True,
    ).strip()


def get_venv_site_packages(venv_root):
    """Path to site-packages folder in virtual environment.

    Todos:
        Find more elegant way to get site-packages paths.

    Args:
        venv_root (str): Path to venv root.

    Returns:
        list[str]: Normalized paths to site-packages dirs.
    """

    output = []
    for root, dirnames, _ in os.walk(venv_root):
        for dirname in dirnames:
            if dirname == "site-packages":
                output.append(os.path.join(root, dirname))
    return output


def run_subprocess(
    cmd_args, *args, venv_info: VenvInfo | None = None, **kwargs
):
    """Convenience method for getting output errors for subprocess.

    Output logged when process finish.

    Entered arguments and keyword arguments are passed to subprocess Popen.

    Args:
        cmd_args (Union[Iterable[str], str]): Command or list of arguments
            passed to Popen.
        *args: Variable length arument list passed to Popen.
        **kwargs : Arbitrary keyword arguments passed to Popen. Is possible to
            pass `logging.Logger` object under "logger" if want to use
            different than lib's logger.

    Returns:
        int: Returncode of process.

    Raises:
        RuntimeError: Exception is raised if process finished with nonzero
            return code.
    """

    # Get environents from kwarg or use current process environments if were
    # not passed.
    env = kwargs.get("env") or os.environ
    # Make sure environment contains only strings
    filtered_env = {str(k): str(v) for k, v in env.items()}

    if venv_info is not None:
        if "cwd" not in kwargs:
            kwargs["cwd"] = venv_info.root
        filtered_env["VIRTUAL_ENV"] = venv_info.venv_path
        filtered_env["PATH"] = os.pathsep.join([
            os.path.dirname(venv_info.executable_path),
            filtered_env["PATH"]
        ])

    # set overrides
    kwargs["env"] = filtered_env
    kwargs["stdin"] = subprocess.PIPE
    kwargs["stdout"] = sys.stdout
    kwargs["stderr"] = sys.stderr

    cmd = subprocess.list2cmdline(cmd_args)
    proc = subprocess.Popen(cmd_args, *args, **kwargs)
    while proc.poll() is None:
        time.sleep(0.1)

    if proc.returncode != 0:
        error_msg = f"Executing arguments was not successful: {cmd}"
        print(error_msg)
        raise RuntimeError(error_msg)
    return proc.returncode


class ZipFileLongPaths(zipfile.ZipFile):
    """Allows longer paths in zip files.

    Regular DOS paths are limited to MAX_PATH (260) characters, including
    the string's terminating NUL character.
    That limit can be exceeded by using an extended-length path that
    starts with the '\\?\' prefix.
    """
    _is_windows = platform.system().lower() == "windows"

    def _extract_member(self, member, tpath, pwd):
        if self._is_windows:
            tpath = os.path.abspath(tpath)
            if tpath.startswith("\\\\"):
                tpath = "\\\\?\\UNC\\" + tpath[2:]
            else:
                tpath = "\\\\?\\" + tpath

        return super(ZipFileLongPaths, self)._extract_member(
            member, tpath, pwd
        )
