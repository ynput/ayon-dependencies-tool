import os
import sys
import time
import re
import subprocess
import platform
import zipfile

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))


def get_venv_executable(venv_root, executable="python"):
    """Get path to executable in virtual environment.

    Args:
        venv_root (str): Path to venv root.
        executable (Optional[str]): Name of executable. Defaults to "python".
    """

    if platform.system().lower() == "windows":
        bin_folder = "Scripts"
    else:
        bin_folder = "bin"
    return os.path.join(venv_root, bin_folder, executable)


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
    cmd_args, *args, bound_output=True, **kwargs
):
    """Convenience method for getting output errors for subprocess.

    Output logged when process finish.

    Entered arguments and keyword arguments are passed to subprocess Popen.

    Args:
        cmd_args (Union[Iterable[str], str]): Command or list of arguments
            passed to Popen.
        *args: Variable length arument list passed to Popen.
        bound_output (bool): Output will be printed with bounded margins.
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
