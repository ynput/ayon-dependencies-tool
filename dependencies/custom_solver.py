"""Custom dependency solver using uv pip compile.

Replaces the previous Poetry-based solver (poetry.factory.Factory,
poetry.installation.installer.Installer, poetry.puzzle.solver.Solver).

Strategy:
  1. Compile all deps (main + runtime) together → consistent resolved versions.
  2. Compile only main deps → their transitive closure.
  3. runtime_only = all_resolved - main_transitive_closure.
  4. Update full_toml_data["ayon"]["runtimeDependencies"] with exact pinned versions.
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def solve_dependencies(
    full_toml_data: dict[str, Any],
    output_root: str,
    venv_path: str,
) -> None:
    """Resolve all dependencies and pin runtimeDependencies to exact versions.

    Mutates full_toml_data["ayon"]["runtimeDependencies"] in place:
    packages that are part of the main dep transitive closure are removed;
    the rest are pinned to exact resolved versions.

    Args:
        full_toml_data: Combined TOML data (installer + addon tomls merged).
        output_root: Working directory for temp files.
        venv_path: Path to the target virtual environment (used for python
            version detection only).
    """
    runtime_deps = full_toml_data["ayon"]["runtimeDependencies"]
    if not runtime_deps:
        return

    main_deps = {
        k: v
        for k, v in full_toml_data["tool"]["poetry"]["dependencies"].items()
        if k.lower() != "python"
    }
    python_constraint = (
        full_toml_data["tool"]["poetry"]["dependencies"].get("python") or ">=3.9"
    )

    # Collect all deps for a single consistent resolution pass
    all_deps = dict(main_deps)
    for k, v in runtime_deps.items():
        all_deps.setdefault(k, v)

    print("Resolving all dependencies with uv ...")
    all_resolved = _uv_compile(all_deps, python_constraint)

    print("Resolving main dependency transitive closure with uv ...")
    main_resolved_names = set(_uv_compile(main_deps, python_constraint))

    # runtime-only = packages resolved that are NOT in the main transitive closure
    new_runtime: dict[str, str] = {}
    for name, version in all_resolved.items():
        if name.lower() not in main_resolved_names:
            new_runtime[name] = version

    full_toml_data["ayon"]["runtimeDependencies"] = new_runtime


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@dataclass
class _Package:
    """Minimal package descriptor (for future compatibility if needed)."""
    name: str
    version: str
    source_type: Optional[str] = None


def _uv_compile(
    deps: dict[str, Any],
    python_constraint: str,
) -> dict[str, str]:
    """Run `uv pip compile` and return a dict of {normalised_name: version}.

    Args:
        deps: Mapping of package name → constraint/version (Poetry or PEP 508
              format, or a dict for git/url deps).
        python_constraint: Python version constraint string (e.g. ">=3.9").

    Returns:
        Dict mapping lower-cased canonical package name to exact version string.
    """
    uv_bin = _find_uv()

    # Build a requirements.in file
    requirements_lines: list[str] = []
    for name, value in deps.items():
        req_line = _dep_to_requirement(name, value)
        if req_line:
            requirements_lines.append(req_line)

    if not requirements_lines:
        return {}

    # Extract a concrete python version for --python-version flag
    python_version = _extract_python_version(python_constraint)

    with tempfile.TemporaryDirectory(prefix="ayon_solver_") as tmp:
        req_in = os.path.join(tmp, "requirements.in")
        req_out = os.path.join(tmp, "requirements.txt")

        with open(req_in, "w") as f:
            f.write("\n".join(requirements_lines) + "\n")

        cmd = [
            uv_bin, "pip", "compile",
            req_in,
            "--output-file", req_out,
            "--no-header",
            "--quiet",
        ]
        if python_version:
            cmd += ["--python-version", python_version]

        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"uv pip compile failed (exit {result.returncode}). "
                "Check the error output above."
            )

        return _parse_requirements_txt(req_out)


def _find_uv() -> str:
    """Locate the uv executable."""
    uv = shutil.which("uv")
    if uv:
        return uv
    # Common install locations
    for candidate in [
        os.path.expanduser("~/.local/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv"),
        "/usr/local/bin/uv",
    ]:
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "uv executable not found. Install uv: https://docs.astral.sh/uv/getting-started/installation/"
    )


def _dep_to_requirement(name: str, value: Any) -> Optional[str]:
    """Convert a Poetry-style dep entry to a PEP 508 requirement line."""
    if value is None:
        return name

    if isinstance(value, dict):
        # Git dependency: {"git": "...", "rev": "..."}
        if "git" in value:
            url = value["git"]
            rev = value.get("rev") or value.get("branch") or value.get("tag")
            if rev:
                return f"{name} @ git+{url}@{rev}"
            return f"{name} @ git+{url}"
        # URL dependency: {"url": "..."}
        if "url" in value:
            return f"{name} @ {value['url']}"
        # Platform-conditional with version: {"version": "...", "markers": "..."}
        version_part = value.get("version", "")
        markers = value.get("markers", "")
        req = f"{name}{_poetry_constraint_to_pep508(version_part)}"
        if markers:
            req += f" ; {markers}"
        return req

    # String constraint
    return f"{name}{_poetry_constraint_to_pep508(str(value))}"


def _poetry_constraint_to_pep508(constraint: str) -> str:
    """Convert a Poetry version constraint to PEP 508 specifier string.

    Returns the specifier part only (e.g. ">=1.0.0,<2.0.0").
    Returns "" for wildcard/any constraints.
    Handles compound constraints (comma-separated, e.g. "^1.0,<2").
    """
    constraint = constraint.strip()
    if not constraint or constraint == "*":
        return ""

    # Handle compound constraints: split by comma, process each part, rejoin.
    # We only split if there are multiple distinct specifiers (e.g. "^1.0,<2").
    # PEP 508 already uses comma-and so we re-join with comma.
    parts = _split_compound_constraint(constraint)
    if len(parts) > 1:
        converted = [_convert_single_constraint(p.strip()) for p in parts]
        return ",".join(c for c in converted if c)

    return _convert_single_constraint(constraint)


def _split_compound_constraint(constraint: str) -> list:
    """Split a compound Poetry constraint into individual parts.

    Splits only on commas that separate top-level constraint parts,
    not commas within a single specifier.
    """
    # Simple case: if no caret/tilde, PEP 508 commas are fine as-is
    if "^" not in constraint and "~" not in constraint:
        return [constraint]
    # Split on comma followed by a specifier-starting character
    import re
    parts = re.split(r",\s*(?=[^,])", constraint)
    return [p.strip() for p in parts if p.strip()]


def _convert_single_constraint(constraint: str) -> str:
    """Convert a single (non-compound) Poetry constraint to a PEP 508 specifier."""
    constraint = constraint.strip()
    if not constraint or constraint == "*":
        return ""

    # Already PEP 508 or numeric
    if constraint.startswith((">=", "<=", "!=", "==", ">", "<")):
        return constraint

    # Caret: ^1.2.3
    if constraint.startswith("^"):
        from .version_utils import _parse_caret
        return _parse_caret(constraint)

    # Tilde: ~1.2.3
    if constraint.startswith("~"):
        from .version_utils import _parse_tilde
        return _parse_tilde(constraint)

    # Wildcard: 3.9.*
    if ".*" in constraint:
        base = constraint.replace(".*", "")
        parts = base.split(".")
        lower_parts = parts + ["0"] * (3 - len(parts))
        upper_parts = parts[:-1] + [str(int(parts[-1]) + 1)] + ["0"] * (3 - len(parts))
        return f">={'.'.join(lower_parts)},<{'.'.join(upper_parts)}"

    # Bare version number → exact
    try:
        from packaging.version import Version
        Version(constraint)  # validate
        return f"=={constraint}"
    except Exception:
        return constraint


def _extract_python_version(constraint: str) -> Optional[str]:
    """Extract a usable python version string from a constraint like '>=3.9'.

    Returns something like '3.9' or '3.11', or None if not determinable.
    """
    if not constraint:
        return None
    # Look for >= X.Y
    m = re.search(r">=\s*(\d+\.\d+)", constraint)
    if m:
        return m.group(1)
    # Look for bare X.Y.Z or X.Y
    m = re.match(r"^(\d+\.\d+)", constraint.strip())
    if m:
        return m.group(1)
    return None


def _parse_requirements_txt(path: str) -> dict[str, str]:
    """Parse a pip-compiled requirements.txt into {lower_name: version}."""
    result: dict[str, str] = {}
    if not os.path.exists(path):
        return result

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Handle VCS / URL deps: "name @ git+..."  → skip version pinning
            if " @ " in line:
                name = line.split(" @ ")[0].strip()
                result[name.lower()] = None
                continue
            # Normal: name==version (with possible extras)
            m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s;]+)", line)
            if m:
                name = re.sub(r"\[.*\]", "", m.group(1)).strip()
                result[name.lower()] = m.group(2)
    return result
