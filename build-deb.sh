#!/usr/bin/env bash
# Build a .deb package:  ./build-deb.sh   ->  dist/nacre_<version>_all.deb
# Install it with:       sudo apt install ./dist/nacre_<version>_all.deb
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
VERSION="$(python3 -c 'import re;print(re.search(r"VERSION = \"(.+?)\"", open("nacre/__init__.py").read()).group(1))')"
STAGE="$(mktemp -d)"
chmod 0755 "$STAGE"
trap 'rm -rf "$STAGE"' EXIT

DESTDIR="$STAGE" ./install.sh --no-deps >/dev/null

mkdir -p "$STAGE/DEBIAN"
SIZE="$(du -sk "$STAGE" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: nacre
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), clamav, clamav-freshclam, pkexec | policykit-1
Maintainer: cyb3rl00m <https://github.com/cyb3rl00m>
Homepage: https://github.com/cyb3rl00m/nacre
Description: Modern antivirus app for Linux, powered by ClamAV
 Nacre is a GTK4/libadwaita front end for the ClamAV antivirus engine.
 Run quick, full system or custom scans, update virus definitions with one
 click, quarantine or delete threats, and review a 30-day activity log.
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "purge" ]; then
  rm -rf /var/lib/nacre
fi
EOF
chmod 0755 "$STAGE/DEBIAN/postrm"

mkdir -p dist
OUT="dist/nacre_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "Built $OUT"
