#!/usr/bin/env bash
# Colors for terminal
RST='\033[0m'             # Text Reset

# Regular Colors
Black='\033[0;30m'        # Black
Red='\033[0;31m'          # Red
Green='\033[0;32m'        # Green
Yellow='\033[0;33m'       # Yellow
Blue='\033[0;34m'         # Blue
Purple='\033[0;35m'       # Purple
Cyan='\033[0;36m'         # Cyan
White='\033[0;37m'        # White

# Bold
BBlack='\033[1;30m'       # Black
BRed='\033[1;31m'         # Red
BGreen='\033[1;32m'       # Green
BYellow='\033[1;33m'      # Yellow
BBlue='\033[1;34m'        # Blue
BPurple='\033[1;35m'      # Purple
BCyan='\033[1;36m'        # Cyan
BWhite='\033[1;37m'       # White

# Bold High Intensity
BIBlack='\033[1;90m'      # Black
BIRed='\033[1;91m'        # Red
BIGreen='\033[1;92m'      # Green
BIYellow='\033[1;93m'     # Yellow
BIBlue='\033[1;94m'       # Blue
BIPurple='\033[1;95m'     # Purple
BICyan='\033[1;96m'       # Cyan
BIWhite='\033[1;97m'      # White

args=$@

poetry_version="2.2.1"
poetry_verbosity=""
while :; do
  case $1 in
    --verbose)
      poetry_verbosity="-vvv"
      ;;
    --)
      shift
      break
      ;;
    *)
      break
  esac
  shift
done

##############################################################################
# Return absolute path
# Globals:
#   None
# Arguments:
#   Path to resolve
# Returns:
#   None
###############################################################################
realpath () {
  echo $(cd $(dirname "$1") || return; pwd)/$(basename "$1")
}

repo_root=$(dirname "$(realpath ${BASH_SOURCE[0]})")
poetry_home_root="$repo_root/.poetry"

version_command="import os;exec(open(os.path.join('$repo_root', 'version.py')).read());print(__version__);"
tool_version="$(python <<< ${version_command})"

##############################################################################
# Detect required version of python
# Globals:
#   colors
#   PYTHON
# Arguments:
#   None
# Returns:
#   None
###############################################################################
detect_python () {
  echo -e "${BIGreen}>>>${RST} Using python \c"
  command -v python >/dev/null 2>&1 || { echo -e "${BIRed}- NOT FOUND${RST} ${BIYellow}You need Python 3.9 installed to continue.${RST}"; return 1; }
  local version_command
  version_command="import sys;print('{0}.{1}'.format(sys.version_info[0], sys.version_info[1]))"
  local python_version
  python_version="$(python <<< ${version_command})"
  oIFS="$IFS"
  IFS=.
  set -- $python_version
  IFS="$oIFS"
  if [ "$1" -ge "3" ] && [ "$2" -ge "9" ] ; then
    if [ "$2" -gt "9" ] ; then
      echo -e "${BIWhite}[${RST} ${BIRed}$1.$2 ${BIWhite}]${RST} - ${BIRed}FAILED${RST} ${BIYellow}Version is new and unsupported, use${RST} ${BIPurple}3.9.x${RST}"; return 1;
    else
      echo -e "${BIWhite}[${RST} ${BIGreen}$1.$2${RST} ${BIWhite}]${RST}"
    fi
  else
    command -v python >/dev/null 2>&1 || { echo -e "${BIRed}$1.$2$ - ${BIRed}FAILED${RST} ${BIYellow}Version is old and unsupported${RST}"; return 1; }
  fi
}

install_poetry () {
  echo -e "${BIGreen}>>>${RST} Installing Poetry ..."
  export POETRY_HOME="$poetry_home_root"
  export POETRY_VERSION="$poetry_version"
  command -v curl >/dev/null 2>&1 || { echo -e "${BIRed}!!!${RST}${BIYellow} Missing ${RST}${BIBlue}curl${BIYellow} command.${RST}"; return 1; }
  curl -sSL https://install.python-poetry.org/ | python -
}

##############################################################################
# Clean pyc files in specified directory
# Globals:
#   None
# Arguments:
#   Optional path to clean
# Returns:
#   None
###############################################################################
clean_pyc () {
  local path
  path=$repo_root
  echo -e "${BIGreen}>>>${RST} Cleaning pyc at [ ${BIWhite}$path${RST} ] ... \c"
  find "$path" -path ./build -o -regex '^.*\(__pycache__\|\.py[co]\)$' -delete

  echo -e "${BIGreen}DONE${RST}"
}

install () {
  # Directories
  pushd "$repo_root" > /dev/null || return > /dev/null

  echo -e "${BIGreen}>>>${RST} Reading Poetry ... \c"
  poetry_path="$poetry_home_root/bin/poetry"
  if [ -f $poetry_path ]; then
    installed_poetry_version="$({$poetry_path} --version)"
    if [[ $installed_poetry_version =~ $poetry_version ]]; then
       echo -e "${BIGreen}>>>${RST} Already installed Poetry has wrong version."
       echo -e "${BIGreen}>>>${RST} - Installed: $($installed_poetry_version)"
       echo -e "${BIGreen}>>>${RST} - Expected:  $($poetry_version)"
       echo -e "${BIGreen}>>>${RST} Reinstalling Poetry ..."
       rm -rf $poetry_home_root
    fi
  fi

  if [ -f $poetry_path ]; then
    echo -e "${BIGreen}OK${RST}"
  else
    echo -e "${BIYellow}NOT FOUND${RST}"
    install_poetry || { echo -e "${BIRed}!!!${RST} Poetry installation failed"; return 1; }
  fi

  if [ -f "$repo_root/poetry.lock" ]; then
    echo -e "${BIGreen}>>>${RST} Updating dependencies ..."
  else
    echo -e "${BIGreen}>>>${RST} Installing dependencies ..."
  fi

  $poetry_path install --no-root $poetry_verbosity || { echo -e "${BIRed}!!!${RST} Poetry environment installation failed"; return 1; }
  if [ $? -ne 0 ] ; then
    echo -e "${BIRed}!!!${RST} Virtual environment creation failed."
    return 1
  fi

  echo -e "${BIGreen}>>>${RST} Cleaning cache files ..."
  clean_pyc

  # reinstall these because of bug in poetry? or cx_freeze?
  # cx_freeze will crash on missing __pychache__ on these but
  # reinstalling them solves the problem.
  echo -e "${BIGreen}>>>${RST} Post-venv creation fixes ..."
  $poetry_path run python -m pip install --disable-pip-version-check --force-reinstall pip
}

set_env () {
  env_path="${repo_root}/.env"
  if [ -f $env_path ]; then
    # Set environments
    set -o allexport
    source "${env_path}"
    set +o allexport
  fi
}

listen () {
  pushd "$repo_root" > /dev/null || return > /dev/null
  "$poetry_home_root/bin/poetry" run python "$repo_root/service" "$@"
}

create_bundle() {
  pushd "$repo_root" > /dev/null || return > /dev/null
  set_env
  "$poetry_home_root/bin/poetry" run python "$repo_root/dependencies" create "$@"
}

list_bundles() {
  pushd "$repo_root" > /dev/null || return > /dev/null
  set_env
  "$poetry_home_root/bin/poetry" run python "$repo_root/dependencies" list-bundles "$@"
}

create_docker_image_private() {
  variant=$1
  if [ "$variant" == "ubuntu" ]; then
    dockerfile="$repo_root/Dockerfile"
  else
    dockerfile="$repo_root/Dockerfile.$variant"
  fi

  if [ ! -f $dockerfile ]; then
    echo -e "${BIRed}!!!${RST} Dockerfile for specifed platform ${BIWhite}$variant${RST} doesn't exist."
    exit 1
  fi
  docker build --pull --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') --build-arg VERSION=$tool_version -t ynput/ayon-dependencies-$variant:$tool_version -f $dockerfile .
}

create_docker_image() {
  variant=$1
  if [ -z "$variant" ]; then
    echo -e "${BIRed}!!!${RST} !!! Missing variant (available options are '${BIWhite}centos7${RST}', '${BIWhite}ubuntu${RST}', '${BIWhite}debian${RST}', '${BIWhite}rocky8${RST}', '${BIWhite}rocky8${RST}' or '${BIWhite}rocky9${RST}')."
    exit 1
  fi
  create_docker_image_private $variant
}

create_package_with_docker() {
  pushd "$repo_root" > /dev/null || return > /dev/null
  set_env
  bundle_name=$1
  variant=$2
  if [[ -z "$bundle_name" || -z "$variant" ]]; then
    echo -e "${BIRed}!!!${RST} Please use 'docker-create' command with [bundle name] [variant] arguments."
    return 1
  fi
  image_name="ynput/ayon-dependencies-$variant:$tool_version"
  if [ -z "$(docker images -q image_name 2> /dev/null)" ]; then
    create_docker_image_private $variant
  fi

  echo -e "${BIGreen}>>>${RST} Using Dockerfile for ${BIWhite}$variant${RST} ..."

  echo -e "${BIGreen}>>>${RST} Running docker build ..."
  container_name=$(date -u +'%Y-%m-%dT%H-%M-%SZ')
  docker run --name $container_name -it --entrypoint "/bin/bash" $image_name -l -c "/opt/ayon-dependencies-tool/start.sh create -b $bundle_name"
  docker container rm $container_name

  if [ $? -ne 0 ] ; then
    echo $?
    echo -e "${BIRed}!!!${RST} Docker build failed."
    return 1
  fi

  echo -e "${BIGreen}>>>${RST} All done!!!"
}

default_help() {
  echo ""
  echo "AYON dependency package tool $tool_version"
  echo ""
  echo "Usage: ./start.ps1 [target]"
  echo ""
  echo "Runtime targets:"
  echo "  install                          Install Poetry and update venv by lock file."
  echo "  set-env                          Set all env vars in .env file."
  echo "  listen                           Start listener on a server."
  echo "  create                           Create dependency package for single bundle."
  echo "  list-bundles                     List bundles available on server."
  echo "  docker-create [bundle] [variant] Create dependency package using docker. Variant can be 'centos7', 'ubuntu', 'debian', 'rocky8' or 'rocky9'"
  echo "  build-docker [variant]           Build docker image. Variant can be 'centos7', 'ubuntu', 'debian', 'rocky8' or 'rocky9'"
  echo ""
}

main() {
  return_code=0
  detect_python || return_code=$?
  if [ $return_code != 0 ]; then
    exit return_code
  fi

  # Use first argument, lower and keep only characters
  function_name="$(echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z]*//g')"

  case $function_name in
    "install")
      install || return_code=$?
      exit $return_code
      ;;
    "setenv")
      set_env || return_code=$?
      exit $return_code
      ;;
    "listen")
      listen "${@:2}" || return_code=$?
      exit $return_code
      ;;
    "create")
      create_bundle "${@:2}" || return_code=$?
      exit $return_code
      ;;
    "listbundles")
      list_bundles "${@:2}" || return_code=$?
      exit $return_code
      ;;
    "dockercreate")
      create_package_with_docker "${@:2}" || return_code=$?
      exit $return_code
      ;;
    "builddocker")
      create_docker_image "${@:2}" || return_code=$?
      exit $return_code
      ;;
  esac

  if [ "$function_name" != "" ]; then
    echo -e "${BIRed}!!!${RST} Unknown function name: $function_name"
  fi

  default_help
  exit $return_code
}

main $@
