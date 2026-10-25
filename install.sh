#!/usr/bin/env bash
# Install Nacre system-wide (Ubuntu 24.04+, Pop!_OS 24.04+, Debian 13+ and derivatives).
#
#   ./install.sh            install dependencies + Nacre (asks for sudo)
#   ./install.sh --no-deps  skip apt, just copy Nacre's files
#
# DESTDIR=/some/dir ./install.sh --no-deps   stages the files (used by build-deb.sh)
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
SRC="$(pwd)"
DESTDIR="${DESTDIR:-}"
PREFIX="/usr"
NO_DEPS=0
[[ "${1:-}" == "--no-deps" ]] && NO_DEPS=1

if [[ -z "$DESTDIR" && $EUID -ne 0 ]]; then
  echo "Nacre installs system-wide, so it needs sudo."
  exec sudo --preserve-env=DESTDIR "$0" "$@"
fi

DEPS=(python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 clamav clamav-freshclam pkexec)
if [[ -z "$DESTDIR" && $NO_DEPS -eq 0 ]]; then
  echo "==> Installing dependencies: ${DEPS[*]}"
  apt-get update -qq || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${DEPS[@]}"
fi

PKGDIR="$PREFIX/share/nacre"
echo "==> Copying files"
install -d "$DESTDIR$PKGDIR/nacre/ui"
install -m 0644 "$SRC"/nacre/*.py "$SRC"/nacre/style.css "$DESTDIR$PKGDIR/nacre/"
install -m 0644 "$SRC"/nacre/ui/*.py "$DESTDIR$PKGDIR/nacre/ui/"

install -d "$DESTDIR$PREFIX/libexec/nacre"
install -m 0755 "$SRC/helper/nacre-helper" "$DESTDIR$PREFIX/libexec/nacre/nacre-helper"

install -d "$DESTDIR$PREFIX/bin"
sed "s|@PKGDIR@|$PKGDIR|" "$SRC/data/nacre.in" > "$DESTDIR$PREFIX/bin/nacre"
chmod 0755 "$DESTDIR$PREFIX/bin/nacre"

install -D -m 0644 "$SRC/data/io.github.cyb3rl00m.Nacre.desktop" \
  "$DESTDIR$PREFIX/share/applications/io.github.cyb3rl00m.Nacre.desktop"
install -D -m 0644 "$SRC/data/icons/hicolor/scalable/apps/io.github.cyb3rl00m.Nacre.svg" \
  "$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps/io.github.cyb3rl00m.Nacre.svg"
install -D -m 0644 "$SRC/data/io.github.cyb3rl00m.Nacre.policy" \
  "$DESTDIR$PREFIX/share/polkit-1/actions/io.github.cyb3rl00m.Nacre.policy"
install -D -m 0644 "$SRC/data/io.github.cyb3rl00m.Nacre.metainfo.xml" \
  "$DESTDIR$PREFIX/share/metainfo/io.github.cyb3rl00m.Nacre.metainfo.xml"

if [[ -n "$DESTDIR" ]]; then
  echo "==> Staged into $DESTDIR"
  exit 0
fi

python3 -m compileall -q "$PKGDIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$PREFIX/share/applications" || true

echo
echo "Nacre is installed. Open it from your app menu, or run: nacre"
if ! ls /var/lib/clamav/*.c[lv]d >/dev/null 2>&1; then
  echo "Tip: virus definitions aren't downloaded yet - click 'Download Definitions' the first time you open Nacre."
fi
