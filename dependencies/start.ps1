# Receive first positional argument
param (
    [Parameter(Mandatory=$true)]
    [string]$FunctionName,
    [Parameter(Mandatory=$false)]
    [string]$maintomlpath
)

$poetry_verbosity="-vv"

$current_dir = Get-Location
$script_dir_rel = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$script_dir = (Get-Item $script_dir_rel).FullName

function Exit-WithCode($exitcode) {
   # Only exit this host process if it's a child of another PowerShell parent process...
   $parentPID = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$PID" | Select-Object -Property ParentProcessId).ParentProcessId
   $parentProcName = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$parentPID" | Select-Object -Property Name).Name
   if ('powershell.exe' -eq $parentProcName) { $host.SetShouldExit($exitcode) }

   exit $exitcode
}

function Install-Poetry() {
    Write-Color -Text ">>> ", "Installing Poetry ... " -Color Green, Gray
    $python = "python"

    $env:POETRY_HOME="$script_dir\.poetry"
    $env:POETRY_VERSION="1.3.2"
    (Invoke-WebRequest -Uri https://install.python-poetry.org/ -UseBasicParsing).Content | & $($python) -
}

function install {
    # install dependencies for tool
    Install-Poetry
    Set-Location "$($script_dir)"
    Write-Color -Text ">>> ", "Creating venv ... ", "$($script_dir)" -Color Green, Gray
    & "$($script_dir)\.poetry\bin\poetry" config virtualenvs.in-project true --local
    & "$($script_dir)\.poetry\bin\poetry" config virtualenvs.create true --local
    Write-Color -Text ">>> ", "Poetry config ... "  -Color Green, Gray
    & "$($script_dir)\.poetry\bin\poetry" config --list
    & "$($script_dir)\.poetry\bin\poetry" install --no-interaction --no-ansi $poetry_verbosity
}

function run_listener {
  & "$($script_dir)\.venv\Scripts\python" "$($script_dir)\services\listener.py"
}

function set_env {
  # set all env vars in .env file
  if (-not (Test-Path "$($script_dir)\.env")) {
    Write-Host "!!! .env file must be prepared!" -ForegroundColor red
    Exit-WithCode 1
  }else{
    $content = Get-Content -Path "$($script_dir)\.env" -Encoding UTF8 -Raw
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

  if (Test-Path "$($script_dir)\venv\Scripts\activate.ps1"){
      & "$($script_dir)\venv\Scripts\activate.ps1"
  }

  if ($FunctionName -eq "install") {
    install
  } elseif ($FunctionName -eq "listen") {
    set_env
    run_listener
  } elseif ($FunctionName -eq "set_env") {
    set_env
  } elseif ($FunctionName -eq "create") {
    set_env
    & "$($script_dir)\.venv\Scripts\python" "$($script_dir)\dependencies.py" --server-url $($env:AYON_SERVER_URL) --api-key $($env:AYON_API_KEY)
  } else {
    Write-Host "Unknown function ""$FunctionName"""
  }
#   & deactivate
}

main