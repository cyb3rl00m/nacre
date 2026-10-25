#!/usr/bin/env bash
# Remove a Nacre installation made with install.sh.
# (If you installed the .deb, use:  sudo apt remove nacre  instead.)
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

PREFIX=/usr
echo "==> Removing Nacre"
rm -rf "$PREFIX/share/nacre" "$PREFIX/libexec/nacre"
rm -f "$PREFIX/bin/nacre" \
      "$PREFIX/share/applications/io.github.cyb3rl00m.Nacre.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/io.github.cyb3rl00m.Nacre.svg" \
      "$PREFIX/share/polkit-1/actions/io.github.cyb3rl00m.Nacre.policy" \
      "$PREFIX/share/metainfo/io.github.cyb3rl00m.Nacre.metainfo.xml"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$PREFIX/share/applications" || true

if [[ -d /var/lib/nacre ]]; then
  read -r -p "Also delete the quarantine (/var/lib/nacre)? Quarantined threats will be gone for good. [Y/n] " ans
  if [[ ! "$ans" =~ ^[Nn] ]]; then
    rm -rf /var/lib/nacre
    echo "    quarantine deleted"
  fi
fi

USER_HOME="$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)"
if [[ -n "$USER_HOME" && ( -d "$USER_HOME/.local/share/nacre" || -d "$USER_HOME/.config/nacre" ) ]]; then
  read -r -p "Also delete your settings and activity log? [y/N] " ans
  if [[ "$ans" =~ ^[Yy] ]]; then
    rm -rf "$USER_HOME/.local/share/nacre" "$USER_HOME/.config/nacre"
    echo "    settings and log deleted"
  fi
fi

echo "Nacre has been removed. ClamAV itself is still installed (sudo apt remove clamav clamav-freshclam to remove it)."
