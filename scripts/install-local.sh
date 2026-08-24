#!/usr/bin/env bash
set -euo pipefail

plugin_id='godhiraj.local-test-pulse'
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
source_dir=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
config_home=${XDG_CONFIG_HOME:-"${HOME:?HOME is required}/.config"}
omarchy_dir="$config_home/omarchy"
plugins_dir="$omarchy_dir/plugins"
target="$plugins_dir/$plugin_id"

for managed_dir in "$config_home" "$omarchy_dir" "$plugins_dir"; do
  if [[ -L "$managed_dir" ]]; then
    printf '%s\n' 'Refusing to install through a symlinked configuration ancestor.' >&2
    exit 1
  fi
  if [[ -e "$managed_dir" && ! -d "$managed_dir" ]]; then
    printf '%s\n' 'Refusing a non-directory configuration ancestor.' >&2
    exit 1
  fi
done
if [[ -L "$target" ]]; then
  printf '%s\n' 'Refusing to install through a symlink.' >&2
  exit 1
fi
if [[ -e "$target" ]]; then
  printf '%s\n' "Refusing to overwrite existing plugin: $target" >&2
  exit 1
fi
mkdir -p -- "$plugins_dir"
staging=$(mktemp -d -- "$plugins_dir/.local-test-pulse.XXXXXX")
cleanup() { [[ ! -e "$staging" ]] || rm -rf -- "$staging"; }
trap cleanup EXIT INT TERM
cp -R -- "$source_dir/." "$staging/"
rm -rf -- "$staging/.git" "$staging/__pycache__" "$staging/tests/__pycache__" "$staging/scripts/__pycache__"
printf '%s\n' "$plugin_id" > "$staging/.local-test-pulse-install"
chmod 0755 -- "$staging/scripts/report_status.py" "$staging/scripts/install-local.sh" "$staging/scripts/remove-local.sh"
mv -- "$staging" "$target"
trap - EXIT INT TERM
printf '%s\n' "Installed $plugin_id at $target"
