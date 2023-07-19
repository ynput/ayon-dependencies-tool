# Receive first positional argument
$FunctionName=$ARGS[0]
$arguments=@()
if ($ARGS.Length -gt 1) {
    $arguments = $ARGS[1..($ARGS.Length - 1)]
}
$image = "ynput/ayon-dependencies-tool:0.0.1"
$current_dir = Get-Location
$script_dir_rel = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$script_dir = (Get-Item $script_dir_rel).FullName

function defaultfunc {
  Write-Host ""
  Write-Host "*************************"
  Write-Host "AYON dependency packages creator"
  Write-Host "   Run listening service"
  Write-Host "*************************"
  Write-Host ""
  Write-Host "Usage: manage [target]"
  Write-Host ""
  Write-Host "Runtime targets:"
  Write-Host "  build    Build docker image"
  Write-Host "  clean    Remove docker image"
  Write-Host "  dev      Run docker (for development purposes)"
}

function build {
  & cp -r "$current_dir/../dependencies" .
  & cp -r "$current_dir/../pyproject.toml" .
  & docker build -t $image .
  & Remove-Item -Recurse -Force "$current_dir/dependencies"
  & Remove-Item -Force "$current_dir/pyproject.toml"
}

function clean {
  & docker rmi $(image)
}

function dev {
  & cp -r "$current_dir/../dependencies" .
  & cp -r "$current_dir/../pyproject.toml" .
  & cp -r "$current_dir/../.env" .
  & docker run --rm -ti `
    -v "$($current_dir):/service" `
  	--hostname dependpack `
    --env-file .env `
  	"$($image)" python /service/listener.py
  & Remove-Item -Recurse -Force "$current_dir/dependencies"
  & Remove-Item -Force "$current_dir/pyproject.toml"
  & Remove-Item -Force "$current_dir/.env"
}

function main {
  if ($FunctionName -eq "build") {
    build
  } elseif ($FunctionName -eq "clean") {
    clean
  } elseif ($FunctionName -eq "dev") {
    dev
  } elseif ($FunctionName -eq $null) {
    defaultfunc
  } else {
    Write-Host "Unknown function ""$FunctionName"""
  }
}

main