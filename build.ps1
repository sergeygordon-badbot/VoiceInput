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

$workPath = Join-Path $PSScriptRoot "build\VoiceInput-$version-work"
$distPath = Join-Path $PSScriptRoot "dist\VoiceInput-$version"
$iconPath = Join-Path $PSScriptRoot "assets\voiceinput.ico"

& $python -m PyInstaller `
    --noconfirm `
    --windowed `
    --onedir `
    --name Rechka `
    --icon $iconPath `
    --workpath $workPath `
    --distpath $distPath `
    --collect-all faster_whisper `
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
