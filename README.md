Addon dependencies distribution tool
------------------------------------

Code in this folder is backend portion of Addon distribution of dependencies for AYON launcher.

This should collect info about all enabled addons on the AYON server based on bundle name, reads its
pyproject.toml files, create one merged one (it tries to find common denominator for dependency version).

Then it uses Poetry to create new venv, zips it and provides this to AYON server for distribution.

It is expected to run on machine that has set reasonable development environment.

`./.env` must be created with filled env vars:
- AYON_API_KEY=api key for service account from AYON Server
- AYON_SERVER_URL=Ayon server tool should communicate with

The tool should run automatically and listen for events on the Server OR could be run manually on machine(s).

Entry point for manual triggering is `start.ps1` or `start.sh`.

Implemented commands:
- `install` - creates `./.venv` with requirements for this tool
- `create` - runs main process to create new dependency package and uploads it. Expects argument with name of Bundle (eg. `./start create -b MyBundle`). For more information `./start create --help`.
- `listen` - starts service connecting to Ayon server and listening for events to trigger main process (TBD)
- `list-bundles` - lists all bundles on Ayon server

TODO:
- [ ] reuse python version from Installer (requirement for `pyenv`?)
- [ ] handle runtime dependencies too
    - consider runtime dependencies from installer
    - give option to addons define their runtime dependencies
- [ ] skip dependency package creation if there are not any addons with dependencies