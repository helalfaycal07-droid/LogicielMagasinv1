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
    & $python.Source -m PyInstaller --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller manque. Installez-le avec : python -m pip install pyinstaller"
    }

    # Nettoyer les anciens dossiers de compilation
    if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
    if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }
    
    # Compiler l'application
    & $python.Source -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name LogicielMagasin `
        app.py
    if ($LASTEXITCODE -ne 0) {
        throw "La génération de l'application a échoué."
    }

    Write-Output "✅ Application générée dans : $projectRoot\dist\LogicielMagasin"

    if ($CreateInstaller) {
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        $compilerPath = if ($compiler) { $compiler.Source }
        if (-not $compilerPath) {
            $candidate = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
            if (Test-Path $candidate) { $compilerPath = $candidate }
        }
        if (-not $compilerPath) {
            $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
            if ($programFilesX86) {
                $candidate = Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe"
                if (Test-Path $candidate) { $compilerPath = $candidate }
            }
        }
        if (-not $compilerPath) {
            throw "Inno Setup 6 est requis pour créer l'installateur. Installez Inno Setup puis relancez avec -CreateInstaller."
        }

        # Nettoyer les anciens installateurs
        if (Test-Path "dist\LogicielMagasin-Setup-*.exe") { 
            Remove-Item "dist\LogicielMagasin-Setup-*.exe" -Force 
        }
        
        & $compilerPath "LogicielMagasin.iss"
        if ($LASTEXITCODE -ne 0) {
            throw "La création de l'installateur a échoué."
        }
        Write-Output "✅ Installateur créé dans : $projectRoot\dist\"
    }
}
finally {
    Pop-Location
}