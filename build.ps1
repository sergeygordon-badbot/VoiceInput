param(
    [string]$OutputName = ""
)

$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "The .venv environment was not found."
}

$version = (& $python -c "from voice_input import __version__; print(__version__)").Trim()
if (-not $version) {
    throw "Application version was not detected."
}

& $python (Join-Path $PSScriptRoot "tools\build_icon.py")
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed with exit code $LASTEXITCODE."
}

& $python (Join-Path $PSScriptRoot "tools\collect_runtime_licenses.py")
if ($LASTEXITCODE -ne 0) {
    throw "License collection failed with exit code $LASTEXITCODE."
}

& $python (Join-Path $PSScriptRoot "tools\write_windows_version_info.py")
if ($LASTEXITCODE -ne 0) {
    throw "Windows version metadata generation failed with exit code $LASTEXITCODE."
}

$distName = if ($OutputName) { $OutputName } else { "Rechka-$version" }
if ([IO.Path]::GetFileName($distName) -ne $distName) {
    throw "OutputName must be a single directory name."
}
$workPath = Join-Path $PSScriptRoot "build\$distName-work"
$distPath = Join-Path $PSScriptRoot "dist\$distName"
$iconPath = Join-Path $PSScriptRoot "assets\voiceinput.ico"
$versionFile = Join-Path $PSScriptRoot "build\Rechka.version.txt"

function Remove-PreviousBuildDirectory {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        return
    }

    $workspace = (Resolve-Path -LiteralPath $PSScriptRoot).Path
    $resolved = (Resolve-Path -LiteralPath $LiteralPath).Path
    $workspacePrefix = $workspace + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith(
        $workspacePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove a build directory outside the workspace: $resolved"
    }

    Get-ChildItem -LiteralPath $resolved -Recurse -Force -File |
        Where-Object { $_.IsReadOnly } |
        ForEach-Object { $_.IsReadOnly = $false }
    Get-ChildItem -LiteralPath $resolved -Recurse -Force -Directory |
        Where-Object {
            ($_.Attributes -band [IO.FileAttributes]::ReadOnly) -ne 0
        } |
        ForEach-Object {
            $_.Attributes = (
                $_.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
            )
        }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Remove-PreviousBuildDirectory -LiteralPath $workPath
Remove-PreviousBuildDirectory -LiteralPath $distPath

& $python -m PyInstaller `
    --noconfirm `
    --windowed `
    --onedir `
    --name Rechka `
    --icon $iconPath `
    --version-file $versionFile `
    --workpath $workPath `
    --distpath $distPath `
    --collect-all faster_whisper `
    --collect-all winrt `
    (Join-Path $PSScriptRoot "main.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

$target = Join-Path $distPath "Rechka"
foreach ($modelName in @("tiny", "base")) {
    $modelSource = Join-Path $PSScriptRoot "models\faster-whisper-$modelName"
    $modelTarget = Join-Path $target "models\faster-whisper-$modelName"
    if (-not (Test-Path -LiteralPath $modelSource)) {
        throw "Bundled model directory not found: $modelSource"
    }
    New-Item -ItemType Directory -Force -Path $modelTarget | Out-Null
    foreach ($modelFile in @("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")) {
        $sourceFile = Join-Path $modelSource $modelFile
        if (-not (Test-Path -LiteralPath $sourceFile)) {
            throw "Bundled model file not found: $sourceFile"
        }
        Copy-Item -LiteralPath $sourceFile -Destination $modelTarget -Force
    }
}

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "PRIVACY.md") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "SECURITY.md") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "CHANGELOG.md") -Destination $target -Force
Copy-Item `
    -LiteralPath (Join-Path $PSScriptRoot "build\runtime-licenses") `
    -Destination (Join-Path $target "licenses") `
    -Recurse `
    -Force

$releaseConfig = Join-Path $PSScriptRoot "release.json"
if (Test-Path -LiteralPath $releaseConfig) {
    Copy-Item -LiteralPath $releaseConfig -Destination $target -Force
}

Write-Host "Built: $target\Rechka.exe"
