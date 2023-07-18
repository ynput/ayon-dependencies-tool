import os
import re
import subprocess
import platform
import zipfile

ANSI_REGEX = re.compile(
    (
        r"\x1b("
        r"(\[\??\d+[hl])|"
        r"([=<>a-kzNM78])|"
        r"([\(\)][a-b0-2])|"
        r"(\[\d{0,2}[ma-dgkjqi])|"
        r"(\[\d+;\d+[hfy]?)|"
        r"(\[;?[hf])|"
        r"(#[3-68])|"
        r"([01356]n)|"
        r"(O[mlnp-z]?)|"
        r"(/Z)|"
        r"(\d+)|"
        r"(\[\?\d;\d0c)|"
        r"(\d;\dR))"
    ),
    flags=re.IGNORECASE
)


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


def _clean_color_codes(text):
    """Completely incomplete clearing of color tags"""

    return ANSI_REGEX.sub("", text)


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
    kwargs["stdout"] = kwargs.get("stdout", subprocess.PIPE)
    kwargs["stderr"] = kwargs.get("stderr", subprocess.PIPE)
    kwargs["stdin"] = kwargs.get("stdin", subprocess.PIPE)
    kwargs["env"] = filtered_env

    cmd = subprocess.list2cmdline(cmd_args)
    proc = subprocess.Popen(cmd_args, *args, **kwargs)
    _stdout, _stderr = proc.communicate()
    if _stdout:
        try:
            _stdout = _stdout.decode("utf-8")
        except Exception:
            _stdout = str(_stdout)
        _stdout = _clean_color_codes(_stdout)
        if bound_output:
            print((
                f"{cmd}\n"
                "*** Output ***"
                f"\n{_stdout}"
                "**************"
            ))
        else:
            print(_stdout)

    if proc.returncode != 0:
        error_msg = f"Executing arguments was not successful: {cmd}"
        print(error_msg)
        if _stderr:
            try:
                _stderr = _stderr.decode("utf-8")
            except Exception:
                _stderr = str(_stderr)
            _stderr = _clean_color_codes(_stderr)
            if bound_output:
                print(
                    "--- StdErr ---"
                    f"\n{_stderr}"
                    "--------------"
                )
            else:
                print(_stderr)

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
