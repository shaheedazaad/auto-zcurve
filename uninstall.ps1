# Remove the managed application and its launcher; retain user data and Pixi.
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param()
$ErrorActionPreference = "Stop"

$DataRoot = Join-Path $env:LOCALAPPDATA "Auto Z-Curve"
$PixiHome = if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME ".pixi" }
$AppDir = Join-Path $DataRoot "app"
$Launcher = Join-Path $PixiHome "bin\auto-zcurve.cmd"
$Targets = @()

if ((Test-Path -LiteralPath $DataRoot) -and
    ((Get-Item -LiteralPath $DataRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Refusing a linked installation root: $DataRoot"
}
foreach ($Target in @($AppDir, (Join-Path $DataRoot "app.previous"))) {
    if (Test-Path -LiteralPath $Target) {
        $Item = Get-Item -LiteralPath $Target
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing a linked application directory: $Target"
        }
        $Metadata = Join-Path $Target "pyproject.toml"
        if (-not $Item.PSIsContainer -or -not (Test-Path -LiteralPath (Join-Path $Target "pixi.toml")) -or
            -not (Test-Path -LiteralPath $Metadata) -or
            -not (Select-String -LiteralPath $Metadata -Pattern '^name = "auto-zcurve"$' -Quiet)) {
            throw "Refusing to remove an unrecognised application directory: $Target"
        }
        $Targets += $Target
    }
}
if (Test-Path -LiteralPath $Launcher) {
    $Item = Get-Item -LiteralPath $Launcher
    $Expected = "--manifest-path `"$AppDir\pixi.toml`" --frozen auto-zcurve"
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $Item.PSIsContainer -or
        -not (Get-Content -LiteralPath $Launcher -Raw).Contains($Expected)) {
        throw "Refusing to remove a launcher that does not belong to this installation: $Launcher"
    }
    $Targets += $Launcher
}
Write-Host "Close auto-zcurve before uninstalling."
Write-Host "Projects, settings, saved API keys, Pixi, and the shared Pixi PATH entry will be kept."
if ($Targets.Count -eq 0) {
    Write-Host "No managed auto-zcurve installation found."
} elseif ($PSCmdlet.ShouldProcess(($Targets -join ", "), "Uninstall auto-zcurve")) {
    foreach ($Target in $Targets) { Remove-Item -LiteralPath $Target -Recurse -Force }
    # The data root can contain projects; remove it only when empty.
    if ((Test-Path -LiteralPath $DataRoot) -and
        @(Get-ChildItem -LiteralPath $DataRoot -Force).Count -eq 0) {
        Remove-Item -LiteralPath $DataRoot
    }
    Write-Host "auto-zcurve has been uninstalled."
}
