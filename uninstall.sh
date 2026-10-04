#!/bin/sh
# Remove the managed application and its launcher; retain user data and Pixi.
set -eu

CONFIRMED=no
DRY_RUN=no
for option in "$@"; do
  case "$option" in
    --yes|-y) CONFIRMED=yes ;;
    --dry-run) DRY_RUN=yes ;;
    --help|-h)
      echo 'Usage: sh uninstall.sh [--yes] [--dry-run]'
      echo 'Removes the installed app and launcher. Keeps projects, settings, API keys, and Pixi.'
      echo 'Honours AUTO_ZCURVE_INSTALL_ROOT and PIXI_HOME, like install.sh.'
      exit 0 ;;
    *) echo "Unknown option: $option" >&2; exit 1 ;;
  esac
done

case "$(uname -s)" in
  Darwin) INSTALL_ROOT="${AUTO_ZCURVE_INSTALL_ROOT:-$HOME/.local/share/auto-zcurve}" ;;
  Linux) INSTALL_ROOT="${AUTO_ZCURVE_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/auto-zcurve}" ;;
  *) echo 'Use uninstall.ps1 on Windows.' >&2; exit 1 ;;
esac
case "$INSTALL_ROOT" in
  /*) ;;
  *) echo 'Installation root must be an absolute path.' >&2; exit 1 ;;
esac
INSTALL_ROOT="${INSTALL_ROOT%/}"
case "$INSTALL_ROOT" in
  ''|/|"$HOME") echo 'Refusing an unsafe installation root.' >&2; exit 1 ;;
esac
LAUNCHER="${PIXI_HOME:-$HOME/.pixi}/bin/auto-zcurve"

# Validate every target before deleting anything. Refuse unrelated directories
# and symbolic links instead of following a custom root into other data.
if [ -L "$INSTALL_ROOT" ]; then
  echo 'Refusing a symbolic-link installation root.' >&2; exit 1
fi
for target in "$INSTALL_ROOT/app" "$INSTALL_ROOT/app.previous"; do
  if [ -L "$target" ]; then
    echo "Refusing symbolic link: $target" >&2; exit 1
  fi
  if [ -e "$target" ]; then
    if [ ! -d "$target" ] || [ ! -f "$target/pixi.toml" ] ||
       ! grep -Eq '^name = "auto-zcurve"$' "$target/pyproject.toml" 2>/dev/null; then
      echo "Refusing to remove an unrecognised application directory: $target" >&2; exit 1
    fi
    printf 'Remove: %s\n' "$target"
  fi
done
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  if [ -L "$LAUNCHER" ] || ! grep -Fq -- "--manifest-path \"$INSTALL_ROOT/app/pixi.toml\" --frozen auto-zcurve" "$LAUNCHER"; then
    echo "Refusing to remove a launcher that does not belong to this installation: $LAUNCHER" >&2; exit 1
  fi
  printf 'Remove: %s\n' "$LAUNCHER"
fi

echo 'Projects, settings, saved API keys, Pixi, and the shared Pixi PATH entry will be kept.'
if [ "$DRY_RUN" = yes ]; then exit 0; fi
if [ "$CONFIRMED" != yes ]; then
  printf 'Close auto-zcurve before continuing. Uninstall? [y/N] '
  read -r answer || answer=no
  case "$answer" in y|Y|yes|YES) ;; *) echo 'Cancelled.'; exit 0 ;; esac
fi
for target in "$INSTALL_ROOT/app" "$INSTALL_ROOT/app.previous"; do
  if [ -d "$target" ]; then rm -rf -- "$target"; fi
done
if [ -f "$LAUNCHER" ]; then rm -f -- "$LAUNCHER"; fi
# Never recursively remove the root: it may also contain user projects.
if [ -d "$INSTALL_ROOT" ]; then rmdir -- "$INSTALL_ROOT" 2>/dev/null || true; fi
echo 'auto-zcurve has been uninstalled.'
