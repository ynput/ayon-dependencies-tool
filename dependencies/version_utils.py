"""Version constraint utilities replacing poetry.core.constraints.version.

Provides a VersionRange class with Poetry-compatible API backed by
the standard `packaging` library. Also provides URL/git helpers that
replace poetry.core.packages.utils and poetry.core.vcs.git.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from packaging.version import Version


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_poetry_constraint(value: str) -> tuple:
    """Parse a Poetry constraint string into (min, max, include_min, include_max, exact).

    Returns a tuple: (min_ver, max_ver, include_min, include_max, exact_ver)
    where all unset fields are None / True.
    """
    value = value.strip()

    if not value or value == "*":
        return (None, None, True, False, None)

    # Wildcard: "3.9.*"
    if ".*" in value:
        base = value.replace(".*", "")
        parts = base.split(".")
        lower_parts = parts + ["0"] * (3 - len(parts))
        upper_parts = parts[:-1] + [str(int(parts[-1]) + 1)] + ["0"] * (3 - len(parts))
        lower = ".".join(lower_parts)
        upper = ".".join(upper_parts)
        return (_V(lower), _V(upper), True, False, None)

    # Caret: "^1.2.3"
    if value.startswith("^"):
        spec = _parse_caret(value)
        return _parse_range_spec(spec)

    # Tilde: "~1.2.3"
    if value.startswith("~"):
        spec = _parse_tilde(value)
        return _parse_range_spec(spec)

    # Range with comma: ">=1.0,<2.0"
    if "," in value:
        return _parse_range_spec(value)

    # Single comparator: ">=1.0", "<2.0", "==1.0", "!=1.0", ">1.0", "<=2.0"
    op_match = re.match(r"^(>=|<=|!=|==|>|<)(.+)$", value)
    if op_match:
        op, ver = op_match.group(1), op_match.group(2).strip()
        if op == "==":
            return (None, None, True, True, _V(ver))
        if op == ">=":
            return (_V(ver), None, True, False, None)
        if op == ">":
            return (_V(ver), None, False, False, None)
        if op == "<=":
            return (None, _V(ver), True, True, None)
        if op == "<":
            return (None, _V(ver), True, False, None)
        # != is complex; treat as any for simplicity
        return (None, None, True, False, None)

    # Bare version number: "3.9.5" → treat as exact
    try:
        return (None, None, True, True, _V(value))
    except Exception:
        return (None, None, True, False, None)


def _V(s: str) -> Version:
    return Version(str(s))


def _parse_caret(value: str) -> str:
    """Convert ^X.Y.Z to >=X.Y.Z,<N.0.0 where N is first-non-zero + 1."""
    ver_str = value[1:]
    parts = [int(p) for p in ver_str.split(".")]

    # Find leftmost non-zero digit index
    idx = 0
    while idx < len(parts) and parts[idx] == 0:
        idx += 1
    if idx >= len(parts):
        idx = len(parts) - 1

    while len(parts) < 3:
        parts.append(0)

    upper_parts = list(parts)
    upper_parts[idx] += 1
    for i in range(idx + 1, len(upper_parts)):
        upper_parts[i] = 0

    lower = ".".join(str(p) for p in parts)
    upper = ".".join(str(p) for p in upper_parts)
    return f">={lower},<{upper}"


def _parse_tilde(value: str) -> str:
    """Convert ~X.Y.Z to >=X.Y.Z,<X.Y+1.0 (patch compat)."""
    ver_str = value[1:]
    parts = [int(p) for p in ver_str.split(".")]
    n = len(parts)

    while len(parts) < 3:
        parts.append(0)
    lower = ".".join(str(p) for p in parts)

    if n >= 3:
        # ~1.2.3 → >=1.2.3,<1.3.0
        upper = f"{parts[0]}.{parts[1] + 1}.0"
    elif n == 2:
        # ~1.2 → >=1.2.0,<1.3.0
        upper = f"{parts[0]}.{parts[1] + 1}.0"
    else:
        # ~1 → >=1.0.0,<2.0.0
        upper = f"{parts[0] + 1}.0.0"

    return f">={lower},<{upper}"


def _parse_range_spec(spec: str) -> tuple:
    """Parse a PEP 508-style range like '>=1.0,<2.0'."""
    min_ver = None
    max_ver = None
    include_min = True
    include_max = False

    for part in spec.split(","):
        part = part.strip()
        m = re.match(r"^(>=|<=|>|<|==)(.+)$", part)
        if not m:
            continue
        op, ver = m.group(1), m.group(2).strip()
        if op == ">=":
            min_ver = _V(ver)
            include_min = True
        elif op == ">":
            min_ver = _V(ver)
            include_min = False
        elif op == "<=":
            max_ver = _V(ver)
            include_max = True
        elif op == "<":
            max_ver = _V(ver)
            include_max = False
        elif op == "==":
            return (None, None, True, True, _V(ver))

    return (min_ver, max_ver, include_min, include_max, None)


def _version_in_range(ver: Version, r: "VersionRange") -> bool:
    if r._empty:
        return False
    if r._any:
        return True
    if r._exact is not None:
        return ver == r._exact
    if r._min is not None:
        if r._include_min:
            if ver < r._min:
                return False
        else:
            if ver <= r._min:
                return False
    if r._max is not None:
        if r._include_max:
            if ver > r._max:
                return False
        else:
            if ver >= r._max:
                return False
    return True


# ---------------------------------------------------------------------------
# VersionRange class
# ---------------------------------------------------------------------------

class VersionRange:
    """A version constraint with Poetry-compatible API.

    Replaces poetry.core.constraints.version classes:
        EmptyConstraint, VersionConstraint, VersionRangeConstraint
    """

    def __init__(
        self,
        min_ver: Optional[Version] = None,
        max_ver: Optional[Version] = None,
        include_min: bool = True,
        include_max: bool = False,
        *,
        exact: Optional[Version] = None,
        empty: bool = False,
        any: bool = False,
    ):
        self._min = min_ver
        self._max = max_ver
        self._include_min = include_min
        self._include_max = include_max
        self._exact = exact
        self._empty = empty
        self._any = any

    # -- Factory methods -------------------------------------------------

    @classmethod
    def from_empty(cls) -> "VersionRange":
        return cls(empty=True)

    @classmethod
    def from_any(cls) -> "VersionRange":
        return cls(any=True)

    @classmethod
    def from_exact(cls, ver: Version) -> "VersionRange":
        return cls(exact=ver)

    # -- Predicates ------------------------------------------------------

    def is_empty(self) -> bool:
        return self._empty

    def is_any(self) -> bool:
        return self._any

    def is_simple(self) -> bool:
        """True if this is an exact version constraint (==X.Y.Z)."""
        return self._exact is not None

    # -- Properties ------------------------------------------------------

    @property
    def min(self) -> Optional[Version]:
        if self._exact is not None:
            return self._exact
        return self._min

    @property
    def max(self) -> Optional[Version]:
        if self._exact is not None:
            return self._exact
        return self._max

    @property
    def include_min(self) -> bool:
        if self._exact is not None:
            return True
        return self._include_min

    @property
    def include_max(self) -> bool:
        if self._exact is not None:
            return True
        return self._include_max

    # -- Operations -------------------------------------------------------

    def intersect(self, other: "VersionRange") -> "VersionRange":
        if self._empty or other._empty:
            return VersionRange.from_empty()
        if self._any:
            return other
        if other._any:
            return self

        # Both exact
        if self._exact is not None and other._exact is not None:
            if self._exact == other._exact:
                return VersionRange.from_exact(self._exact)
            return VersionRange.from_empty()

        # One exact, one range
        if self._exact is not None:
            return self if _version_in_range(self._exact, other) else VersionRange.from_empty()
        if other._exact is not None:
            return other if _version_in_range(other._exact, self) else VersionRange.from_empty()

        # Both ranges: take tighter bounds
        new_min = self._min
        new_include_min = self._include_min
        new_max = self._max
        new_include_max = self._include_max

        if other._min is not None:
            if new_min is None or other._min > new_min:
                new_min, new_include_min = other._min, other._include_min
            elif other._min == new_min:
                new_include_min = self._include_min and other._include_min

        if other._max is not None:
            if new_max is None or other._max < new_max:
                new_max, new_include_max = other._max, other._include_max
            elif other._max == new_max:
                new_include_max = self._include_max and other._include_max

        # Validate
        if new_min is not None and new_max is not None:
            if new_min > new_max:
                return VersionRange.from_empty()
            if new_min == new_max and not (new_include_min and new_include_max):
                return VersionRange.from_empty()

        return VersionRange(new_min, new_max, new_include_min, new_include_max)

    def allows_all(self, other: "VersionRange") -> bool:
        """Return True if self is a superset of other (other ⊆ self)."""
        if other._empty:
            return True
        if self._any:
            return True
        if other._any:
            return self._any

        # Use intersection equality: A allows_all B iff A ∩ B == B
        intersection = self.intersect(other)
        if intersection._empty:
            return False
        return _ranges_equal(intersection, other)

    # -- String representation -------------------------------------------

    def __str__(self) -> str:
        if self._empty:
            return "<empty>"
        if self._any:
            return "*"
        if self._exact is not None:
            return str(self._exact)
        parts = []
        if self._min is not None:
            op = ">=" if self._include_min else ">"
            parts.append(f"{op}{self._min}")
        if self._max is not None:
            op = "<=" if self._include_max else "<"
            parts.append(f"{op}{self._max}")
        return ",".join(parts) if parts else "*"

    def __repr__(self) -> str:
        return f"VersionRange({self!s})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionRange):
            return NotImplemented
        return (
            self._empty == other._empty
            and self._any == other._any
            and self._exact == other._exact
            and self._min == other._min
            and self._max == other._max
            and self._include_min == other._include_min
            and self._include_max == other._include_max
        )


def _ranges_equal(a: VersionRange, b: VersionRange) -> bool:
    return (
        a._empty == b._empty
        and a._any == b._any
        and a._exact == b._exact
        and a._min == b._min
        and a._max == b._max
        and a._include_min == b._include_min
        and a._include_max == b._include_max
    )


# Compatibility aliases
EmptyConstraint = VersionRange
VersionConstraint = VersionRange
VersionRangeConstraint = VersionRange


def parse_constraint(value) -> VersionRange:
    """Parse a version constraint string into a VersionRange.

    Handles Poetry-specific syntax (^, ~, .*) as well as PEP 508 ranges.
    """
    if value is None:
        return VersionRange.from_any()
    if isinstance(value, VersionRange):
        return value

    value = str(value).strip()
    if not value or value == "*":
        return VersionRange.from_any()

    try:
        min_ver, max_ver, include_min, include_max, exact = _parse_poetry_constraint(value)
    except Exception:
        return VersionRange.from_any()

    if exact is not None:
        return VersionRange.from_exact(exact)
    if min_ver is None and max_ver is None:
        return VersionRange.from_any()
    return VersionRange(min_ver, max_ver, include_min, include_max)


# ---------------------------------------------------------------------------
# URL / Git utilities  (replace poetry.core.packages.utils and vcs.git)
# ---------------------------------------------------------------------------

def is_url(value: str) -> bool:
    """Return True if the value looks like a URL."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    return value.startswith(
        ("http://", "https://", "git://", "git+http://", "git+https://")
    )


class Link:
    """Minimal replacement for poetry.core.packages.utils.link.Link."""

    def __init__(self, url: str):
        self._url = url.strip()
        parsed = urlparse(self._url)
        self._scheme = parsed.scheme  # e.g. "git+https", "https"

    @property
    def url(self) -> str:
        return self._url

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def url_without_fragment(self) -> str:
        return self._url.split("#")[0]


class ParsedUrl:
    """Minimal replacement for poetry.core.vcs.git.ParsedUrl."""

    def __init__(self, url: str, rev: Optional[str] = None):
        self.url = url
        self.rev = rev

    @classmethod
    def parse(cls, url: str) -> "ParsedUrl":
        """Parse a git URL, optionally with @revision suffix."""
        rev = None
        # Remove git+ prefix if present
        clean = url
        if clean.startswith("git+"):
            clean = clean[4:]

        # Extract revision from @ref (ensure it's in the path, not user@host)
        scheme_end = clean.find("://")
        at_idx = clean.rfind("@")
        if at_idx != -1 and (scheme_end == -1 or at_idx > scheme_end + 3):
            rev = clean[at_idx + 1:]
            clean = clean[:at_idx]

        return cls(url=clean, rev=rev or None)
