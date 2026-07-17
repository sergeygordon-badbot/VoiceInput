param(
    [switch]$RebuildApp,
    [string]$AppBuildDir = ""
)

$ErrorActionPreference = "Stop"

$innoVersion = "7.0.2"
$innoFileName = "innosetup-$innoVersion-x64.exe"
$downloadUrl = "https://github.com/jrsoftware/issrc/releases/download/is-7_0_2/$innoFileName"
$toolsRoot = Join-Path $PSScriptRoot ".tools"
$downloadsDir = Join-Path $toolsRoot "downloads"
$innoDir = Join-Path $toolsRoot "inno-setup-7"
$innoInstaller = Join-Path $downloadsDir $innoFileName
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "The .venv environment was not found."
}
$version = (& $python -c "from voice_input import __version__; print(__version__)").Trim()
if (-not $version) {
    throw "Application version was not detected."
}
$appDir = if ($AppBuildDir) {
    (Resolve-Path -LiteralPath $AppBuildDir).Path
} else {
    Join-Path $PSScriptRoot "dist\Rechka-$version\Rechka"
}
$appExe = Join-Path $appDir "Rechka.exe"
$scriptFile = Join-Path $PSScriptRoot "installer\Rechka.iss"

if ($RebuildApp) {
    & (Join-Path $PSScriptRoot "build.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Application build failed."
    }
}

if (-not (Test-Path -LiteralPath $appExe)) {
    throw "Portable application build not found. Run build.ps1 first."
}

$iscc = Get-ChildItem -LiteralPath $innoDir -Filter "ISCC.exe" -File -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($null -eq $iscc) {
    New-Item -ItemType Directory -Force -Path $downloadsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $innoDir | Out-Null

    if (-not (Test-Path -LiteralPath $innoInstaller)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host "Downloading official Inno Setup $innoVersion..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $innoInstaller -UseBasicParsing
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $innoInstaller
    $publisher = if ($signature.SignerCertificate) {
        $signature.SignerCertificate.Subject
    } else {
        ""
    }

    if ($signature.Status -ne "Valid" -or $publisher -notlike "*Pyrsys B.V.*") {
        throw "Inno Setup signature verification failed. Status: $($signature.Status), publisher: $publisher"
    }

    Write-Host "Preparing portable Inno Setup compiler..."
    $arguments = @(
        "/PORTABLE=1",
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CURRENTUSER",
        "/DIR=`"$innoDir`""
    )
    $process = Start-Process `
        -FilePath $innoInstaller `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -PassThru `
        -Wait

    if ($process.ExitCode -ne 0) {
        throw "Inno Setup bootstrap failed with exit code $($process.ExitCode)."
    }

    $iscc = Get-ChildItem -LiteralPath $innoDir -Filter "ISCC.exe" -File -Recurse |
        Select-Object -First 1
}

if ($null -eq $iscc) {
    throw "ISCC.exe was not found after preparing Inno Setup."
}

Write-Host "Building Rechka installer..."
& $iscc.FullName `
    "/DMyAppVersion=$version" `
    "/DMyAppSourceDir=$appDir" `
    $scriptFile
if ($LASTEXITCODE -ne 0) {
    throw "Installer compilation failed."
}

$output = Join-Path $PSScriptRoot "dist\installer\Rechka-Setup-$version.exe"
if (-not (Test-Path -LiteralPath $output)) {
    throw "Installer output was not created."
}

Write-Host "Built: $output"
