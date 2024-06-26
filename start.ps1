# Receive first positional argument
$FunctionName=$ARGS[0]
$arguments=@()
if ($ARGS.Length -gt 1) {
    $arguments = $ARGS[1..($ARGS.Length - 1)]
}

$poetry_verbosity="-vv"

$current_dir = Get-Location
$repo_root_rel = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$repo_root = (Get-Item $repo_root_rel).FullName

$TOOL_VERSION = Invoke-Expression -Command "python -c ""import os;import sys;content={};f=open(r'$($current_dir)/version.py');exec(f.read(),content);f.close();print(content['__version__'])"""


function Default-Func {
    Write-Host ""
    Write-Host "AYON dependency package tool $TOOL_VERSION"
    Write-Host ""
    Write-Host "Usage: ./start.ps1 [target]"
    Write-Host ""
    Write-Host "Runtime targets:"
    Write-Host "  install                         Install Poetry and update venv by lock file."
    Write-Host "  set-env                         Set all env vars in .env file."
    Write-Host "  listen                          Start listener on a server."
    Write-Host "  create                          Create dependency package for single bundle."
    Write-Host "  list-bundles                    List bundles available on server."
    Write-Host "  docker-build [bundle] [variant] Build dependency package using docker. Variant can be 'centos7', 'ubuntu', 'debian' or 'rocky9'"
    Write-Host ""
}

function Exit-WithCode($exitcode) {
   # Only exit this host process if it's a child of another PowerShell parent process...
   $parentPID = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$PID" | Select-Object -Property ParentProcessId).ParentProcessId
   $parentProcName = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$parentPID" | Select-Object -Property Name).Name
   if ('powershell.exe' -eq $parentProcName) { $host.SetShouldExit($exitcode) }
   Restore-Cwd
   exit $exitcode
}

function Install-Poetry() {
    Write-Host ">>> Installing Poetry ... "
    $python = "python"
    if (Get-Command "pyenv" -ErrorAction SilentlyContinue) {
        if (-not (Test-Path -PathType Leaf -Path "$($repo_root)\.python-version")) {
            $result = & pyenv global
            if ($result -eq "no global version configured") {
                Write-Host "!!! ", "Using pyenv but having no local or global version of Python set."
                Exit-WithCode 1
            }
        }
        $python = & pyenv which python
    }

    (Invoke-WebRequest -Uri https://install.python-poetry.org/ -UseBasicParsing).Content | & $($python) -
}

function CreatePackageWithDocker {
    $startTime = [int][double]::Parse((Get-Date -UFormat %s))
    Write-Host ">>> Building AYON using Docker ..."
    $bundleName = $args[0]
    $variant = $args[1]
    $imageIdPath = "$($repo_root)/docker-image.id"

    if ($null -eq $variant) {
        $variant = "ubuntu"
    }

    if ($variant -eq "ubuntu") {
        $dockerfile = "$($repo_root)\Dockerfile"
    } else {
        $dockerfile = "$($repo_root)\Dockerfile.$variant"
    }
    if (-not (Test-Path -PathType Leaf -Path $dockerfile)) {
        Write-Host "!!! Dockerfile for specifed platform [$variant] doesn't exist."
        Restore-Cwd
        Exit-WithCode 1
    }

    Write-Host ">>> Running Docker build ..."

    if (Test-Path $imageIdPath) {
        Remove-Item $imageIdPath
    }
    docker build --pull --iidfile $imageIdPath --build-arg BUILD_DATE=$(Get-Date -UFormat %Y-%m-%dT%H:%M:%SZ) --build-arg VERSION=$TOOL_VERSION --build-arg BUNDLE_NAME=$bundleName -t ynput/ayon-dependencies:$TOOL_VERSION-$variant -f $dockerfile .
    if (Test-Path $imageIdPath) {
        $cid = Get-Content $imageIdPath
        Remove-Item $imageIdPath
        docker image rm $cid
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "!!! Docker command failed. $LASTEXITCODE"
        Restore-Cwd
        Exit-WithCode 1
    }

    $endTime = [int][double]::Parse((Get-Date -UFormat %s))

    Write-Host "*** All done in $($endTime - $startTime) secs."
}

function Change-Cwd() {
    Set-Location -Path $repo_root
}

function Restore-Cwd() {
    Set-Location -Path $current_dir
}

function install {
    # install dependencies for tool
     if (-not (Test-Path -PathType Container -Path "$($env:POETRY_HOME)\bin")) {
        Install-Poetry
    }

    Change-Cwd

    Write-Host ">>> ", "Poetry config ... "
    & "$env:POETRY_HOME\bin\poetry" install --no-interaction --no-root --ansi  $poetry_verbosity
}

function set_env {
    # set all env vars in .env file
    if (-not (Test-Path "$($repo_root)\.env")) {
        Write-Host "!!! ", ".env file must be prepared!" -ForegroundColor red
        Exit-WithCode 1
    } else {
        $content = Get-Content -Path "$($repo_root)\.env" -Encoding UTF8 -Raw
        foreach($line in $content.split()) {
            if ($line){
                $parts = $line.split("=")
                $varName = $parts[0]
                $value = $parts[1]
                Write-Host "Setting $varName with $value"
                Set-Item "env:$varName" $value
            }
        }
    }
}

function main {
    if ($null -eq $FunctionName) {
        Default-Func
        return
    }
    $FunctionName = $FunctionName.ToLower() -replace "\W"
    if ($FunctionName -eq "install") {
        Change-Cwd
        install
    } elseif ($FunctionName -eq "listen") {
        Change-Cwd
        set_env
        & "$env:POETRY_HOME\bin\poetry" run python "$($repo_root)\service" @arguments
    } elseif ($FunctionName -eq "setenv") {
        Change-Cwd
        set_env
    } elseif ($FunctionName -eq "create") {
        Change-Cwd
        set_env
        & "$env:POETRY_HOME\bin\poetry" run python "$($repo_root)\dependencies" create @arguments
    } elseif ($FunctionName -eq "listbundles") {
        Change-Cwd
        set_env
        & "$env:POETRY_HOME\bin\poetry" run python "$($repo_root)\dependencies" list-bundles @arguments
    } elseif ($FunctionName -eq "dockerbuild") {
        Change-Cwd
        CreatePackageWithDocker @arguments
    } else {
        Write-Host "Unknown function \"$FunctionName\""
        Default-Func
    }
    Restore-Cwd
}

# Force POETRY_HOME to this directory
$env:POETRY_HOME="$repo_root\.poetry"
$env:POETRY_VERSION="1.8.1"

main