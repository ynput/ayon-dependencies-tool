import os
import sys
import click
import ayon_api
from ayon_api.constants import SERVER_URL_ENV_KEY, SERVER_API_ENV_KEY

from .core import create_package, get_bundles


@click.group()
def main_cli():
    pass


@main_cli.command(help="Create dependency package for release bundle")
@click.option(
    "-b",
    "--bundle-name",
    required=True,
    help="Bundle name for which dep package is created")
@click.option(
    "--output-dir",
    help="Directory where created package can be saved")
@click.option(
    "--skip-upload",
    is_flag=True,
    help="Skip upload of created package to AYON server")
@click.option(
    "--server",
    help="AYON server url",
    envvar=SERVER_URL_ENV_KEY)
@click.option(
    "--api-key",
    help="Api key",
    envvar=SERVER_API_ENV_KEY)
def create(bundle_name, skip_upload, output_dir, server, api_key):
    if server:
        os.environ[SERVER_URL_ENV_KEY] = server

    if api_key:
        os.environ[SERVER_API_ENV_KEY] = api_key

    if ayon_api.create_connection() is False:
        raise RuntimeError("Could not connect to server.")

    create_package(
        bundle_name,
        skip_upload=skip_upload,
        output_dir=output_dir
    )


@main_cli.command(help="List available bundles on AYON server")
@click.option(
    "--server",
    help="AYON server url",
    envvar=SERVER_URL_ENV_KEY)
@click.option(
    "--api-key",
    help="Api key",
    envvar=SERVER_API_ENV_KEY)
def list_bundles(server, api_key):
    if server:
        os.environ[SERVER_URL_ENV_KEY] = server

    if api_key:
        os.environ[SERVER_API_ENV_KEY] = api_key

    server = os.environ.get(SERVER_URL_ENV_KEY)
    api_key = os.environ.get(SERVER_API_ENV_KEY)
    if not server:
        print("AYON server url not set.")
        sys.exit(1)

    if not api_key:
        print("AYON api key not set.")
        sys.exit(1)

    print(f"Connecting to AYON server {server}...")
    if ayon_api.create_connection() is False:
        raise RuntimeError("Could not connect to server.")

    con = ayon_api.get_server_api_connection()
    print("--- Available bundles ---")
    for bundle_name in sorted(get_bundles(con)):
        print(bundle_name)
    print("-------------------------")


def main():
    main_cli()
