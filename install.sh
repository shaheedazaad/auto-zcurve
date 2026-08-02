#!/bin/sh
set -eu

REPOSITORY="${AUTO_ZCURVE_REPOSITORY:-shaheedazaad/auto-zcurve}"
VERSION="${AUTO_ZCURVE_VERSION:-latest}"

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

echo
echo "Auto Z-Curve is installed."
echo "Open a new terminal and run: auto-zcurve"
