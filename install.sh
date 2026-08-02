#!/bin/sh
set -eu

REPOSITORY="${AUTO_ZCURVE_REPOSITORY:-shaheedazaad/auto-zcurve}"
VERSION="${AUTO_ZCURVE_VERSION:-latest}"

add_path_line() {
  config_file="$1"
  escaped_bin_dir="$(printf '%s' "$BIN_DIR" | sed "s/'/'\\\\''/g")"
  path_line="AUTO_ZCURVE_BIN_DIR='$escaped_bin_dir'; case \":\$PATH:\" in *\":\$AUTO_ZCURVE_BIN_DIR:\"*) ;; *) export PATH=\"\$AUTO_ZCURVE_BIN_DIR:\$PATH\" ;; esac; unset AUTO_ZCURVE_BIN_DIR"

  if ! grep -Fqx "$path_line" "$config_file" 2>/dev/null; then
    printf '\n# Added by the Auto Z-Curve installer.\n%s\n' "$path_line" >> "$config_file"
  fi
}

ensure_launcher_on_path() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) return ;;
  esac

  shell_name="$(basename "${SHELL:-sh}")"
  case "$shell_name" in
    zsh)
      ZSH_CONFIG_DIR="${ZDOTDIR:-$HOME}"
      mkdir -p "$ZSH_CONFIG_DIR"
      add_path_line "$ZSH_CONFIG_DIR/.zshrc"
      PATH_CONFIG="$ZSH_CONFIG_DIR/.zshrc"
      ;;
    bash)
      # Bash uses .bashrc for interactive shells and .bash_profile for login
      # shells. Update the login file Bash already prefers so that creating a
      # new file does not prevent an existing .profile from being loaded.
      add_path_line "$HOME/.bashrc"
      if [ -f "$HOME/.bash_profile" ]; then
        BASH_LOGIN_CONFIG="$HOME/.bash_profile"
      elif [ -f "$HOME/.bash_login" ]; then
        BASH_LOGIN_CONFIG="$HOME/.bash_login"
      else
        BASH_LOGIN_CONFIG="$HOME/.profile"
      fi
      add_path_line "$BASH_LOGIN_CONFIG"
      PATH_CONFIG="$HOME/.bashrc and $BASH_LOGIN_CONFIG"
      ;;
    fish)
      if "$SHELL" -c 'fish_add_path -- $argv[1]' "$BIN_DIR"; then
        PATH_CONFIG="the fish user path"
      else
        echo "Could not add $BIN_DIR to the fish user path." >&2
        echo "Run: fish_add_path $BIN_DIR" >&2
        return
      fi
      ;;
    *)
      add_path_line "$HOME/.profile"
      PATH_CONFIG="$HOME/.profile"
      ;;
  esac

  echo "Added $BIN_DIR to PATH via $PATH_CONFIG."
}

if command -v pixi >/dev/null 2>&1; then
  PIXI_BIN="$(command -v pixi)"
else
  curl -fsSL https://pixi.sh/install.sh | sh
  PIXI_BIN="${PIXI_HOME:-$HOME/.pixi}/bin/pixi"
fi

case "$(uname -s)" in
  Darwin) DATA_ROOT="$HOME/Library/Application Support/Auto Z-Curve" ;;
  Linux) DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/auto-zcurve" ;;
  *) echo "Auto Z-Curve supports macOS and glibc-based Linux with this installer." >&2; exit 1 ;;
esac

if [ "$VERSION" = "latest" ]; then
  BUNDLE_URL="https://github.com/$REPOSITORY/releases/latest/download/auto-zcurve-bundle.tar.gz"
else
  BUNDLE_URL="https://github.com/$REPOSITORY/releases/download/$VERSION/auto-zcurve-bundle.tar.gz"
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT HUP INT TERM
curl -fL "$BUNDLE_URL" -o "$TEMP_DIR/bundle.tar.gz"
mkdir -p "$TEMP_DIR/unpacked"
tar -xzf "$TEMP_DIR/bundle.tar.gz" -C "$TEMP_DIR/unpacked"

APP_DIR="$DATA_ROOT/app"
BACKUP_DIR="$DATA_ROOT/app.previous"
mkdir -p "$DATA_ROOT"
rm -rf "$BACKUP_DIR"
if [ -d "$APP_DIR" ]; then
  mv "$APP_DIR" "$BACKUP_DIR"
fi
mv "$TEMP_DIR/unpacked/auto-zcurve" "$APP_DIR"

if ! "$PIXI_BIN" install --manifest-path "$APP_DIR/pixi.toml" --frozen; then
  rm -rf "$APP_DIR"
  if [ -d "$BACKUP_DIR" ]; then
    mv "$BACKUP_DIR" "$APP_DIR"
  fi
  echo "Installation failed; the previous version was restored." >&2
  exit 1
fi
rm -rf "$BACKUP_DIR"

BIN_DIR="${PIXI_HOME:-$HOME/.pixi}/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/auto-zcurve" <<EOF
#!/bin/sh
exec "$PIXI_BIN" run --manifest-path "$APP_DIR/pixi.toml" --frozen auto-zcurve "\$@"
EOF
chmod +x "$BIN_DIR/auto-zcurve"

ensure_launcher_on_path

echo
echo "Auto Z-Curve is installed."
echo "Open a new terminal and run: auto-zcurve"
