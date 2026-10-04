# Install

auto-zcurve ships as a single installer command. It sets up everything you
need — Python, R, Quarto, and all the required packages — in one self-contained
managed app folder. You don't need to install those tools separately. The
installer also installs Pixi if needed and creates a launcher in Pixi's bin
directory, adding that directory to your user PATH when necessary.

Supported systems: Apple Silicon and Intel macOS 14+, Windows x64, and
mainstream 64-bit Linux.

## macOS or Linux

Open the **Terminal** app and paste this, then press Return:

```sh
curl -fsSL https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/install.sh | sh
```

## Windows

Open **PowerShell** (search for it in the Start menu) and paste this, then
press Enter:

```powershell
irm https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/install.ps1 | iex
```

The installer downloads the latest release and installs its locked runtime
dependencies. This can take a few minutes depending on your connection.

!!! note "First launch after installing"
    Close and reopen your terminal (or PowerShell) once after the very first
    install, so it picks up the new `auto-zcurve` command.

## Launching the app

From a terminal, type:

```sh
auto-zcurve
```

auto-zcurve picks a free port on your computer, opens your default web
browser to a private local address, and starts the app. Your terminal window
stays open and running in the background — leave it be while you work in the
browser tab. To stop the app, click back into that terminal window and press
<kbd>Ctrl</kbd>+<kbd>C</kbd>.

Every time you launch auto-zcurve, it generates a fresh, random web address.
Old links from a previous session won't work — just run `auto-zcurve` again
and use the new one.

## Updating

Run the same install command again whenever a new version is released. It
will download and swap in the update; your existing projects are untouched.

## Uninstalling

Stop the app with **Ctrl+C** in its terminal first. The uninstallers remove
only the managed application, any previous-version backup, and its launcher.
They keep projects, settings, saved API keys, Pixi, and Pixi's shared PATH
entry. To remove a saved key, use **Remove** in that provider's Settings
section before uninstalling.

### macOS and Linux

Download the script, preview its targets, then run it:

```sh
curl -fsSL https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/uninstall.sh -o uninstall.sh
sh uninstall.sh --dry-run
sh uninstall.sh
```

The final command asks for confirmation. Use `sh uninstall.sh --yes` for an
unattended uninstall. If you installed with `AUTO_ZCURVE_INSTALL_ROOT` or
`PIXI_HOME`, supply the same values when uninstalling.

### Windows

Download and run the PowerShell script:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/shaheedazaad/auto-zcurve/main/uninstall.ps1 -OutFile uninstall.ps1
.\uninstall.ps1 -WhatIf
.\uninstall.ps1
```

The final command asks for confirmation. `-Confirm:$false` skips that prompt.
If you installed with a custom `PIXI_HOME`, keep that value set. The script
uses the same `%LOCALAPPDATA%\Auto Z-Curve` location as the Windows installer.

Both uninstallers refuse to remove an unrecognised app directory or a
launcher belonging to another installation. Running them again after removal
is safe. They are also included in release bundles.

## Next step

Continue to [Getting an API key](api-key.md).
