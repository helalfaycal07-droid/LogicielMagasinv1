param(
    [switch]$CreateInstaller
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python) {
    throw "Python est introuvable. Installez Python 3.11+ puis relancez ce script."
}

Push-Location $projectRoot
try {
    # 1. Vérification de PyInstaller
    & $python.Source -m PyInstaller --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller manque. Installez-le avec : python -m pip install -r requirements-build.txt"
    }

    # 2. Compilation de l'application avec PyInstaller (avec inclusion du module de mise à jour)
    & $python.Source -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name LogicielMagasin `
        --hidden-import=version_checker `
        app.py

    if ($LASTEXITCODE -ne 0) {
        throw "La génération de l'application a échoué."
    }

    Write-Output "Application générée dans : $projectRoot\dist\LogicielMagasin"

    # 3. Création de l'installateur si demandé
    if ($CreateInstaller) {
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        $compilerPath = if ($compiler) { $compiler.Source }
        if (-not $compilerPath) {
            $candidate = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
            if (Test-Path $candidate) {
                $compilerPath = $candidate
            }
        }
        if (-not $compilerPath) {
            $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
            if ($programFilesX86) {
                $candidate = Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe"
                if (Test-Path $candidate) {
                    $compilerPath = $candidate
                }
            }
        }
        if (-not $compilerPath) {
            throw "Inno Setup 6 est requis pour créer l'installateur. Installez Inno Setup puis relancez avec -CreateInstaller."
        }

        & $compilerPath "LogicielMagasin.iss"
        if ($LASTEXITCODE -ne 0) {
            throw "La création de l'installateur a échoué."
        }
        Write-Output "Installateur créé dans : $projectRoot\dist\LogicielMagasin-Setup-0.3.3.exe"
    }
}
catch {
    Write-Host "Erreur critique : $_" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}