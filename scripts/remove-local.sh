#!/usr/bin/env bash
set -euo pipefail

plugin_id='godhiraj.local-test-pulse'
config_home=${XDG_CONFIG_HOME:-"${HOME:?HOME is required}/.config"}
omarchy_dir="$config_home/omarchy"
plugins_dir="$omarchy_dir/plugins"
target="$plugins_dir/$plugin_id"
marker="$target/.local-test-pulse-install"

for managed_dir in "$config_home" "$omarchy_dir" "$plugins_dir"; do
  if [[ -L "$managed_dir" ]]; then
    printf '%s\n' 'Refusing to remove through a symlinked configuration ancestor.' >&2
    exit 1
  fi
  if [[ -e "$managed_dir" && ! -d "$managed_dir" ]]; then
    printf '%s\n' 'Refusing a non-directory configuration ancestor.' >&2
    exit 1
  fi
done

if [[ ! -e "$target" && ! -L "$target" ]]; then
  printf '%s\n' "$plugin_id is not installed"
  exit 0
fi
if [[ -L "$target" || ! -d "$target" || ! -f "$marker" ]] || [[ $(<"$marker") != "$plugin_id" ]]; then
  printf '%s\n' 'Refusing to remove an unowned or unsafe path.' >&2
  exit 1
fi
rm -rf -- "$target"
printf '%s\n' "Removed $plugin_id from $target"
