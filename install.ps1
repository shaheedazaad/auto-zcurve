$ErrorActionPreference = "Stop"

function Test-PathContainsDirectory {
    param(
        [string]$PathValue,
        [string]$Directory
    )

    $Separators = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $Directory = $Directory.TrimEnd($Separators)
    foreach ($Entry in ($PathValue -split ";")) {
        $ExpandedEntry = [Environment]::ExpandEnvironmentVariables($Entry.Trim().Trim([char]34))
        $ExpandedEntry = $ExpandedEntry.TrimEnd($Separators)
        if ($ExpandedEntry -ieq $Directory) { return $true }
    }
    return $false
}

function Add-DirectoryToUserPath {
    param([string]$Directory)

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Test-PathContainsDirectory $UserPath $Directory)) {
        $NewUserPath = if ([string]::IsNullOrWhiteSpace($UserPath)) {
            $Directory
        } else {
            "$Directory;$UserPath"
        }
        [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
        Write-Host "Added $Directory to your user PATH."
    }

    # The installer is evaluated in the current PowerShell process, so make the
    # command available there immediately as well as in future terminals.
    if (-not (Test-PathContainsDirectory $env:Path $Directory)) {
        $env:Path = "$Directory;$env:Path"
    }
}

$Repository = if ($env:AUTO_ZCURVE_REPOSITORY) { $env:AUTO_ZCURVE_REPOSITORY } else { "shaheedazaad/auto-zcurve" }
$Version = if ($env:AUTO_ZCURVE_VERSION) { $env:AUTO_ZCURVE_VERSION } else { "latest" }
$PixiHome = if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME ".pixi" }
$Pixi = Get-Command pixi -ErrorAction SilentlyContinue
if (-not $Pixi) {
    Invoke-RestMethod -UseBasicParsing https://pixi.sh/install.ps1 | Invoke-Expression
    $PixiPath = Join-Path $PixiHome "bin\pixi.exe"
} else {
    $PixiPath = $Pixi.Source
}

if ($Version -eq "latest") {
    $BundleUrl = "https://github.com/$Repository/releases/latest/download/auto-zcurve-bundle.zip"
} else {
    $BundleUrl = "https://github.com/$Repository/releases/download/$Version/auto-zcurve-bundle.zip"
}

$DataRoot = Join-Path $env:LOCALAPPDATA "Auto Z-Curve"
$AppDir = Join-Path $DataRoot "app"
$BackupDir = Join-Path $DataRoot "app.previous"
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("auto-zcurve-" + [guid]::NewGuid())

try {
    New-Item -ItemType Directory -Force -Path $TempDir, $DataRoot | Out-Null
    $Archive = Join-Path $TempDir "bundle.zip"
    Invoke-WebRequest -UseBasicParsing $BundleUrl -OutFile $Archive
    Expand-Archive -LiteralPath $Archive -DestinationPath $TempDir

    if (Test-Path $BackupDir) { Remove-Item -Recurse -Force $BackupDir }
    if (Test-Path $AppDir) { Move-Item $AppDir $BackupDir }
    Move-Item (Join-Path $TempDir "auto-zcurve") $AppDir

    & $PixiPath install --manifest-path (Join-Path $AppDir "pixi.toml") --frozen
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
        if (Test-Path $BackupDir) { Move-Item $BackupDir $AppDir }
        throw "Installation failed; the previous version was restored."
    }
    if (Test-Path $BackupDir) { Remove-Item -Recurse -Force $BackupDir }

    $BinDir = Join-Path $PixiHome "bin"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $Launcher = Join-Path $BinDir "auto-zcurve.cmd"
    "@echo off`r`n`"$PixiPath`" run --manifest-path `"$AppDir\pixi.toml`" --frozen auto-zcurve %*`r`n" |
        Set-Content -Encoding ASCII $Launcher

    Add-DirectoryToUserPath $BinDir

    Write-Host ""
    Write-Host "Auto Z-Curve is installed."
    Write-Host "Open a new PowerShell window and run: auto-zcurve"
} finally {
    if (Test-Path $TempDir) { Remove-Item -Recurse -Force $TempDir }
}
