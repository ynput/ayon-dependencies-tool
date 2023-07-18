import os
import os.path
import tempfile
import pytest
import shutil
import platform

from poetry.core.constraints.version import parse_constraint
from poetry.core.constraints.generic import EmptyConstraint

from ..dependencies import (
    FileTomlProvider,
    ServerTomlProvider,
    is_valid_toml,
    merge_tomls,
    get_full_toml,
    prepare_new_venv,
    zip_venv,
    lock_to_toml_data,
    remove_existing_from_venv,
    _get_correct_version,
    _convert_url_constraints,
    _create_dependency_package_basename
)

ROOT_FOLDER = os.getenv("OPENPYPE_ROOT") or \
    os.path.join(os.path.dirname(__file__), "../../../pype")
TEST_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "resources")
TEST_OP_TOML = os.path.join(TEST_RESOURCES_DIR, "openpype_pyproject.toml")
PURGE_TMP = True


@pytest.fixture
def openpype_toml_data():
    provider = FileTomlProvider(TEST_OP_TOML)
    return provider.get_toml()


@pytest.fixture
def addon_toml_to_compare_data():
    """Test file contains dummy data to test version compare"""
    provider = FileTomlProvider(os.path.join(TEST_RESOURCES_DIR,
                                             "pyproject.toml"))
    return provider.get_toml()


@pytest.fixture
def addon_toml_to_venv_data():
    """Test file contains 'close to live' toml for single addon."""
    provider = FileTomlProvider(os.path.join(TEST_RESOURCES_DIR,
                                             "pyproject_clean.toml"))
    return provider.get_toml()


@pytest.fixture(scope="module")
def tmpdir():
    tmpdir = tempfile.mkdtemp(prefix="openpype_test_")

    yield tmpdir

    if PURGE_TMP:
        try:
            shutil.rmtree(tmpdir)
        except PermissionError:
            print(f"Couldn't delete {tmpdir}")


@pytest.mark.parametrize(
    ("constraint1", "constraint2", "expected"),
    [
        (
            "3.6.1",
            "^3.9",
            "<empty>",
        ),
        (
            "3.9.*",
            "^3.9",
            ">=3.9,<3.10.dev0",
        ),
        (
                "3.9.5",
                "3.9.8",
                "<empty>",
        ),
        (
                None,
                "3.9.8",
                "3.9.8",
        )
    ]
)
def test_get_correct_version(constraint1, constraint2, expected):
    assert str(_get_correct_version(constraint1, constraint2)) == str(expected)


def test_existing_file():
    provider = FileTomlProvider(TEST_OP_TOML)
    _ = provider.get_toml()


def test_not_existing_file():
    dir_name = os.path.dirname(__file__)
    provider = FileTomlProvider(os.path.join(dir_name, "pyproject.toml"))
    with pytest.raises(ValueError):
        _ = provider.get_toml()

#
def test_is_valid_toml(openpype_toml_data):

    assert is_valid_toml(openpype_toml_data), "Must contain all required keys"


def test_is_valid_toml_invalid(openpype_toml_data):
    openpype_toml_data.pop("tool")

    with pytest.raises(KeyError):
        is_valid_toml(openpype_toml_data)


def test_merge_tomls(openpype_toml_data, addon_toml_to_compare_data):
    result_toml = merge_tomls(openpype_toml_data, addon_toml_to_compare_data,
                              "dummy_addon_0.0.1")
    _compare_resolved_tomp(result_toml)


def test_get_full_toml(openpype_toml_data):
    addon_tomls = {}
    with open(os.path.join(TEST_RESOURCES_DIR, "pyproject.toml")) as fp:
        addon_tomls["dummy_addon_0.0.1"] = fp.read()

    result_toml = get_full_toml(openpype_toml_data, addon_tomls)
    _compare_resolved_tomp(result_toml)


def _compare_resolved_tomp(result_toml):
    res_dependencies = result_toml["tool"]["poetry"]["dependencies"]
    dep_version = res_dependencies["aiohttp"]
    assert dep_version == ">=3.7,<3.8.dev0"

    dep_version = res_dependencies["new_dependency"]
    assert dep_version == "^1.0.0"

    res_dependencies = result_toml["tool"]["poetry"]["dev-dependencies"]
    dep_version = res_dependencies["new_dependency"]
    assert dep_version == "^2.0.0"

    platform_name = platform.system().lower()
    third_party = result_toml["openpype"]["thirdparty"]
    assert str(third_party["ffmpeg"][platform_name]["version"]) == "4.4"

    res_dependencies = (third_party["oiio"][platform_name])
    dep_version = res_dependencies["version"]
    assert dep_version == "2.3.10"

    res_dependencies = (third_party["ocioconfig"])
    dep_version = res_dependencies["version"]
    assert dep_version == "1.0.2"


def test_create_dependency_package_basename():
    test_file_1_path = os.path.join(TEST_RESOURCES_DIR, "pyproject.toml")

    test_file_1_name = _create_dependency_package_basename(test_file_1_path)
    test_file_2_name = _create_dependency_package_basename(test_file_1_path)

    assert test_file_1_name == test_file_2_name, \
        "Same file must result in same name"

    test_file_2_path = os.path.join(TEST_RESOURCES_DIR, "pyproject_clean.toml")
    test_file_2_name = _create_dependency_package_basename(test_file_2_path)

    assert test_file_1_name != test_file_2_name, \
        "Different file must result in different name"

    with pytest.raises(FileNotFoundError):
        _create_dependency_package_basename(test_file_1_path + ".ntext")


def test_lock_to_toml_data():
    lock_file_path = os.path.join(TEST_RESOURCES_DIR, "poetry.lock")

    toml_data = lock_to_toml_data(lock_file_path)

    assert (toml_data["tool"]["poetry"]["dependencies"]["acre"] == "1.0.0",
            "Wrong version, must be '1.0.0'")

    assert is_valid_toml(toml_data), "Must contain all required keys"


def test_prepare_new_venv(addon_toml_to_venv_data, tmpdir):
    """Creates zip of simple venv from mock addon pyproject data"""
    print(f"Creating new venv in {tmpdir}")
    return_code = prepare_new_venv(addon_toml_to_venv_data, tmpdir)

    assert return_code != 1, "Prepare of new venv failed"

    inst_lib = os.path.join(tmpdir, '.venv', 'Lib', 'site-packages', 'aiohttp')
    assert os.path.exists(inst_lib), "aiohttp should be installed"


# @pytest.mark.long_running
# def test_remove_existing_from_venv(openpype_toml_data, tmpdir):
#     """New venv shouldn't contain libraries already in build venv.
#
#     test_prepare_new_venv must be enabled
#     """
#     base_venv_path = os.path.join(tmpdir, ".base_venv")
#     os.makedirs(base_venv_path)
#     shutil.copy(TEST_OP_TOML, os.path.join(base_venv_path, "pyproject.toml"))
#     return_code = prepare_new_venv(openpype_toml_data, base_venv_path)
#
#     assert return_code != 1, "Prepare of base venv failed"
#
#     base_venv_path = os.path.join(base_venv_path, ".venv")
#     addon_venv_path = os.path.join(tmpdir, ".venv")
#
#     assert os.path.exists(base_venv_path), f"Base {base_venv_path} must exist"
#     assert os.path.exists(addon_venv_path), f"Addon {addon_venv_path} must exist"  # noqa
#
#     removed = remove_existing_from_venv(base_venv_path, addon_venv_path)
#
#     assert "aiohttp" in removed, "aiohttp is in base, should be removed"
#

# def test_ServerTomlProvider():
#     # TODO switch to mocks without test server
#     server_endpoint = "https://34e99f0f-f987-4715-95e6-d2d88caa7586.mock.pstmn.io/get_addons_tomls"  # noqa
#     tomls = ServerTomlProvider(server_endpoint).get_tomls()
#
#     assert len(tomls) == 1, "One addon should have dependencies"
#
#     assert (tomls[0]["tool"]["poetry"]["dependencies"]["python"] == "^3.10",
#             "Missing dependency")


def test_convert_url_constraints_http_dependency(full_toml_data):
    """Tests that the `_convert_url_constraints()` method correctly converts
    an HTTP dependency."""
    full_toml_data["tool"]["poetry"]["dependencies"] = {
        "requests": "http://github.com/kennethreitz/requests@2.27.1"
    }
    _convert_url_constraints(full_toml_data)
    assert full_toml_data["tool"]["poetry"]["dependencies"]["requests"] == {
        "url": "http://github.com/kennethreitz/requests@2.27.1"
    }

def test_convert_url_constraints_git_dependency(full_toml_data):
    """Tests that the `_convert_url_constraints()` method correctly converts
    a Git dependency."""
    full_toml_data["tool"]["poetry"]["dependencies"] = {
        "mypackage": "git+https://github.com/myusername/mypackage@v1.2.3"
    }
    _convert_url_constraints(full_toml_data)
    assert full_toml_data["tool"]["poetry"]["dependencies"]["mypackage"] == {
        "git": "https://github.com/myusername/mypackage",
        "rev": "v1.2.3"
    }
