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
  export POETRY_HOME="$repo_root/.poetry"
  export POETRY_VERSION="1.3.2"
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
  if [ -f "$POETRY_HOME/bin/poetry" ]; then
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

  "$POETRY_HOME/bin/poetry" install --no-root $poetry_verbosity || { echo -e "${BIRed}!!!${RST} Poetry environment installation failed"; return 1; }
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
  "$POETRY_HOME/bin/poetry" run python -m pip install --disable-pip-version-check --force-reinstall pip
}

set_env () {
  # Set environments
  set -o allexport
  source "${repo_root}/.env"
  set +o allexport
  # Print out values
  cat file.txt | xargs echo
}

listen () {
  pushd "$repo_root" > /dev/null || return > /dev/null
  "$POETRY_HOME/bin/poetry" run python "$repo_root/service" "$@"
}

create_bundle() {
  pushd "$repo_root" > /dev/null || return > /dev/null
  set_env
  "$POETRY_HOME/bin/poetry" run python "$repo_root/dependencies" create "$@"
}

list_bundles() {
  pushd "$repo_root" > /dev/null || return > /dev/null
  set_env
  "$POETRY_HOME/bin/poetry" run python "$repo_root/dependencies" list-bundles "$@"
}

default_help() {
  echo ""
  echo "Ayon dependency package tool"
  echo ""
  echo "Usage: ./start.ps1 [target]"
  echo ""
  echo "Runtime targets:"
  echo "  install                       Install Poetry and update venv by lock file."
  echo "  set_env                       Set all env vars in .env file."
  echo "  listen                        Start listener on a server."
  echo "  create                        Create dependency package for single bundle."
  echo "  list-bundles                  List bundles available on server."
  echo ""
}

main() {
  return_code=0
  detect_python || return_code=$?
  if [ $return_code != 0 ]; then
    exit return_code
  fi

  if [[ -z $POETRY_HOME ]]; then
    export POETRY_HOME="$repo_root/.poetry"
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
  esac

  if [ "$function_name" != "" ]; then
    echo -e "${BIRed}!!!${RST} Unknown function name: $function_name"
  fi

  default_help
  exit $return_code
}

main $@
