<#
.SYNOPSIS
  Helper script create virtual environment using Poetry.

.DESCRIPTION
  This script will detect Python installation, create venv with Poetry
  and install all necessary packages from `poetry.lock` or `pyproject.toml`
  needed by OpenPype to be included during application freeze on Windows.

.EXAMPLE

PS> .\create_env.ps1

.EXAMPLE

Print verbose information from Poetry:
PS> .\create_env.ps1 -venv_path PTH_TO_NEW_VENV -verbose

#>

param (
    [String] $venv_path,
    [switch] $verbose
)
$poetry_verbosity=$null
if ($verbose){
    $poetry_verbosity="-vvv"
}

$current_dir = Get-Location
$script_dir = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$openpype_root = (Get-Item $script_dir).parent.FullName
$env:PSModulePath = $env:PSModulePath + ";$($openpype_root)\tools\modules\powershell"


function Exit-WithCode($exitcode) {
   # Only exit this host process if it's a child of another PowerShell parent process...
   $parentPID = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$PID" | Select-Object -Property ParentProcessId).ParentProcessId
   $parentProcName = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$parentPID" | Select-Object -Property Name).Name
   if ('powershell.exe' -eq $parentProcName) { $host.SetShouldExit($exitcode) }

   exit $exitcode
}


function Show-PSWarning() {
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        Write-Color -Text "!!! ", "You are using old version of PowerShell - ",  "$($PSVersionTable.PSVersion.Major).$($PSVersionTable.PSVersion.Minor)" -Color Red, Yellow, White
        Write-Color -Text "    Please update to at least 7.0 - ", "https://github.com/PowerShell/PowerShell/releases" -Color Yellow, White
        Exit-WithCode 1
    }
}

if (-not (Test-Path 'env:POETRY_HOME')) {
    $env:POETRY_HOME = "$openpype_root\.poetry"
}

Set-Location -Path $openpype_root

if ($venv_path -or
    -not (Test-Path -PathType Leaf -Path "$($openpype_root)\poetry.lock")) {
    Write-Color -Text ">>> ", "Installing virtual environment and creating lock." -Color Green, Gray
} else {
    Write-Color -Text ">>> ", "Installing virtual environment from lock." -Color Green, Gray
}

if ($venv_path){
   Write-Color -Text ">>> ", "Creating virtual environment at $($venv_path)." -Color Green, White
   & "$env:POETRY_HOME\bin\poetry" run python -m venv $venv_path
   $env:VIRTUAL_ENV = $venv_path
   & "$env:POETRY_HOME\bin\poetry" config virtualenvs.create false
   Set-Location -Path $venv_path
}

$startTime = [int][double]::Parse((Get-Date -UFormat %s))
Write-Color -Text ">>> ", "Installing dependencies at $($venv_path)." -Color Green, White
& "$env:POETRY_HOME\bin\poetry" install --no-root $poetry_verbosity --ansi
if ($LASTEXITCODE -ne 0) {
    Write-Color -Text "!!! ", "Poetry command failed." -Color Red, Yellow
    Set-Location -Path $current_dir
    Exit-WithCode 1
}

$endTime = [int][double]::Parse((Get-Date -UFormat %s))
Set-Location -Path $current_dir

Write-Color -Text ">>> ", "Virtual environment created." -Color Green, White
