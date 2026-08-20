# Install

auto-zcurve ships as a single installer command. It sets up everything you
need — Python, R, Quarto, and all the required packages — in one self-contained
folder. You don't need to install any of those separately, and nothing is
added to your system outside that folder.

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

The installer downloads the latest release and the small AI models used to
read PDF layouts and tables. This can take a few minutes depending on your
connection — that's normal.

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

## Next step

Continue to [Getting an API key](api-key.md).
