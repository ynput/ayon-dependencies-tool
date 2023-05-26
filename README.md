Addon dependencies distribution tool
------------------------------------

Code in this folder is backend portion of Addon distribution of dependencies for v4 server.

This should collect info about all enabled addons on the v4 server, reads its
pyproject.tomls, create one merged one (it tries to find common denominator for dependency version).

Then it uses Poetry to create new venv, zips it and provides this to v4 server for distribution to 
all clients.

It is expected to be run on machine that has set reasonable development environment (cmake probably).

Should be standalone, not depending on Openpype libraries.

`dependencies/.env` must be created with filled env vars:
- AYON_API_KEY=api key for service account from Ayon Server
- AYON_SERVER_URL=Ayon server tool should communicate with
- AY_ADDON_NAME=dependencies_tool
- AY_ADDON_VERSION=0.0.1

Entry point for manual triggering is `dependencies/start.ps1`.

Implemented commands:
- `install` - creates `dependencies/.venv` with requirements for this tool
- `create` - runs main process to create new dependency package and uploads it. Expects `--main-toml-path` argument pointing
    to base `pyproject.toml` that should contain all requirements bundled with Ayon Desktop.
- `listen` - starts service connecting to Ayon server and listening for events to trigger main process (TBD)