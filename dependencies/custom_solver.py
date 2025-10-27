import os
import sys
import copy
import collections
from typing import Any
from pathlib import Path

import toml
from cleo.io.null_io import IO, NullIO
from cleo.io.outputs.stream_output import StreamOutput
from cleo.io.inputs.argv_input import ArgvInput
from cleo.formatters.style import Style

from packaging.utils import canonicalize_name

from poetry.utils.env import VirtualEnv
from poetry.repositories import RepositoryPool
from poetry.repositories import Repository
from poetry.installation.installer import Installer
from poetry.repositories.lockfile_repository import LockfileRepository

from poetry.factory import Factory


def create_io() -> IO:
    input = ArgvInput()
    input.set_stream(sys.stdin)

    output = StreamOutput(sys.stdout)

    error_output = StreamOutput(sys.stderr)

    io = IO(input, output, error_output)

    # Set our own CLI styles
    formatter = io.output.formatter
    formatter.set_style("c1", Style("cyan"))
    formatter.set_style("c2", Style("default", options=["bold"]))
    formatter.set_style("info", Style("blue"))
    formatter.set_style("comment", Style("green"))
    formatter.set_style("warning", Style("yellow"))
    formatter.set_style("debug", Style("default", options=["dark"]))
    formatter.set_style("success", Style("green"))

    # Dark variants
    formatter.set_style("c1_dark", Style("cyan", options=["dark"]))
    formatter.set_style("c2_dark", Style("default", options=["bold", "dark"]))
    formatter.set_style("success_dark", Style("green", options=["dark"]))

    io.output.set_formatter(formatter)
    io.error_output.set_formatter(formatter)

    return io


def solve_dependencies(
    full_toml_data: dict[str, Any],
    output_root: str,
    venv_path: str,
) -> None:
    output_root = Path(output_root)
    venv_path = Path(venv_path)

    all_dependencies = copy.deepcopy(full_toml_data)
    runtime_dependencies = all_dependencies["ayon"]["runtimeDependencies"]
    if not runtime_dependencies:
        return

    # Prepare all packages of all dependencies together
    all_main_dependencies = all_dependencies["tool"]["poetry"]["dependencies"]
    all_main_dependencies.update(runtime_dependencies)
    all_solved_packages = _solve_dependencies(
        all_dependencies, output_root, venv_path
    )
    packages_by_name = {
        package.name.lower(): package
        for package in all_solved_packages
    }

    # Fill up the main dependencies with resolved versions
    main_dependencies = {}
    main_dep_names = set()
    deps_queue = collections.deque(
        full_toml_data["tool"]["poetry"]["dependencies"]
    )
    while deps_queue:
        dep_name = deps_queue.popleft()
        dep_name_low = dep_name.lower()
        if dep_name_low == "python":
            continue

        if dep_name_low in main_dep_names:
            continue
        main_dep_names.add(dep_name_low)

        package = packages_by_name.get(dep_name_low)
        if package is None:
            dep_name_low_t = dep_name_low.replace("_", "-")
            package = packages_by_name.get(dep_name_low_t)
            if package is None:
                dep_name_low_t = dep_name_low.replace("-", "_")
                package = packages_by_name.get(dep_name_low_t)

        if package is None:
            raise RuntimeError(
                f"Failed to find dependency '{dep_name}'"
                " in resolved packages."
            )

        version = _package_to_version(package)
        main_dependencies[package.name] = version

        for dep in package.requires:
            deps_queue.append(dep.name)

    runtime_dependencies = {}
    for package_name, package in packages_by_name.items():
        if package_name in main_dep_names:
            continue
        runtime_dependencies[package.name] = _package_to_version(package)

    full_toml_data["ayon"]["runtimeDependencies"] = runtime_dependencies


def _package_to_version(package):
    if package.source_type not in ("directory", "file", "url", "git"):
        return package.version.text
    return None


def _solve_dependencies(
    toml_data: dict[str, Any],
    output_root: Path,
    venv_path: Path,
):
    pyproject_toml_path = output_root / "pyproject.toml"
    with open(pyproject_toml_path, "w") as stream:
        toml.dump(toml_data, stream)

    poetry = Factory().create_poetry(
        cwd=output_root,
        io=None,
        disable_plugins=False,
        disable_cache=False,
    )
    env = VirtualEnv(Path(venv_path))
    installer = CustomResolver(
        create_io(),
        env,
        poetry.package,
        poetry.locker,
        poetry.pool,
        poetry.config,
        disable_cache=poetry.disable_cache,
    )
    installer.run()
    os.remove(pyproject_toml_path)
    return [
        op.package
        for op in installer.ops
    ]


class CustomResolver(Installer):
    ops = []
    def _do_install(self) -> int:
        from poetry.puzzle.solver import Solver

        locked_repository = Repository("poetry-locked")
        if self._update:
            if not self._lock and self._locker.is_locked():
                locked_repository = self._locker.locked_repository()

                # If no packages have been whitelisted (The ones we want to update),
                # we whitelist every package in the lock file.
                if not self._whitelist:
                    for pkg in locked_repository.packages:
                        self._whitelist.append(pkg.name)

            # Checking extras
            for extra in self._extras:
                if extra not in self._package.extras:
                    raise ValueError(f"Extra [{extra}] is not specified.")

            self._io.write_line("<info>Updating dependencies</>")
            solver = Solver(
                self._package,
                self._pool,
                self._installed_repository.packages,
                locked_repository.packages,
                self._io,
            )

            with solver.provider.use_source_root(
                source_root=self._env.path.joinpath("src")
            ):
                ops = solver.solve(use_latest=self._whitelist).calculate_operations()
        else:
            self._io.write_line("<info>Installing dependencies from lock file</>")

            locked_repository = self._locker.locked_repository()

            if not self._locker.is_fresh():
                self._io.write_error_line(
                    "<warning>"
                    "Warning: poetry.lock is not consistent with pyproject.toml. "
                    "You may be getting improper dependencies. "
                    "Run `poetry lock [--no-update]` to fix it."
                    "</warning>"
                )

            locker_extras = {
                canonicalize_name(extra)
                for extra in self._locker.lock_data.get("extras", {})
            }
            for extra in self._extras:
                if extra not in locker_extras:
                    raise ValueError(f"Extra [{extra}] is not specified.")

            # If we are installing from lock
            # Filter the operations by comparing it with what is
            # currently installed
            ops = self._get_operations_from_lock(locked_repository)

        lockfile_repo = LockfileRepository()
        self._populate_lockfile_repo(lockfile_repo, ops)

        if not self.executor.enabled:
            # If we are only in lock mode, no need to go any further
            self._write_lock_file(lockfile_repo)
            return 0

        if self._groups is not None:
            root = self._package.with_dependency_groups(list(self._groups), only=True)
        else:
            root = self._package.without_optional_dependency_groups()

        if self._io.is_verbose():
            self._io.write_line("")
            self._io.write_line(
                "<info>Finding the necessary packages for the current system</>"
            )

        # We resolve again by only using the lock file
        pool = RepositoryPool(ignore_repository_names=True, config=self._config)

        # Making a new repo containing the packages
        # newly resolved and the ones from the current lock file
        repo = Repository("poetry-repo")
        for package in lockfile_repo.packages + locked_repository.packages:
            if not package.is_direct_origin() and not repo.has_package(package):
                repo.add_package(package)

        pool.add_repository(repo)

        solver = Solver(
            root,
            pool,
            self._installed_repository.packages,
            locked_repository.packages,
            NullIO(),
        )
        # Everything is resolved at this point, so we no longer need
        # to load deferred dependencies (i.e. VCS, URL and path dependencies)
        solver.provider.load_deferred(False)

        with solver.use_environment(self._env):
            ops = solver.solve(use_latest=self._whitelist).calculate_operations(
                with_uninstalls=self._requires_synchronization,
                synchronize=self._requires_synchronization,
                skip_directory=self._skip_directory,
            )
        self.ops = ops
