Addon dependencies distribution tool
------------------------------------

Code in this folder is backend portion of Addon distribution of dependencies for Ayon server.

This should collect info about all enabled addons on the v4 server, reads its
pyproject.tomls, create one merged one (it tries to find common denominator for dependency version).

Then it uses Poetry to create new venv, zips it and provides this to Ayon server for distribution to 
all clients.

It is expected to be run on machine that has set reasonable development environment (cmake probably).

Should be standalone, not depending on Openpype libraries.

`./.env` must be created with filled env vars:
- AYON_API_KEY=api key for service account from Ayon Server
- AYON_SERVER_URL=Ayon server tool should communicate with

The tool should run automatically and listen for events on the Server OR could be run manually on machine(s).

Entry point for manual triggering is `dependencies/manage.ps1`.

Implemented commands:
- `install` - creates `dependencies/.venv` with requirements for this tool
- `create` - runs main process to create new dependency package and uploads it. Expects argument with name of Bundle (eg. `./manage.ps1 create MyBundle`)
- `listen` - starts service connecting to Ayon server and listening for events to trigger main process (TBD)

TODO:
- [ ] reuse python version from Installer (requirement for `pyenv`?)
- [ ] create dependency package to output folder
- [ ] handle runtime dependencies too
