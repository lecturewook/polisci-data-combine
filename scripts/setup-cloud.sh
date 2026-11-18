#!/usr/bin/env bash
# Working directory can be anywhere inside or outside the checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
root_dir=$(pwd -P)
tools_dir="$(dirname "$root_dir")/.polihub-tools"
mkdir -p "$tools_dir/bin" "$tools_dir/cache"
python3 -m venv "$root_dir/.venv"
"$root_dir/.venv/bin/python" -m pip --disable-pip-version-check --no-cache-dir install -r requirements.txt
if [[ "$(uname -s)-$(uname -m)" != Linux-x86_64 ]]; then
  echo "Use the native R distribution for this platform; install readr and haven as documented."
  exit 0
fi
export XDG_CACHE_HOME="$tools_dir/cache"
export MAMBA_ROOT_PREFIX="$tools_dir/mamba"
export CONDA_PKGS_DIRS="$MAMBA_ROOT_PREFIX/pkgs"
mamba="$tools_dir/bin/micromamba-linux-64"
mamba_hash=701f39c06f8e6a670a0e186c8479b9e5c0502a2f0cda6cef65d83bdd2875e8bd
if ! printf '%s  %s\n' "$mamba_hash" "$mamba" | sha256sum --check --status; then
  temp_bin=$(mktemp "$tools_dir/bin/micromamba.XXXXXX")
  trap 'rm -f "$temp_bin"' EXIT
  curl --fail --location --silent --show-error --proto '=https' \
    https://github.com/mamba-org/micromamba-releases/releases/download/2.10.0-0/micromamba-linux-64 -o "$temp_bin"
  printf '%s  %s\n' "$mamba_hash" "$temp_bin" | sha256sum --check
  chmod 755 "$temp_bin"
  mv "$temp_bin" "$mamba"
fi
cert_options=()
if [[ -n "${SSL_CERT_FILE:-}" ]]; then cert_options=(--ssl-verify "$SSL_CERT_FILE"); fi
r_dir="$tools_dir/r"
if [[ ! -x "$r_dir/bin/Rscript" ]] || ! "$r_dir/bin/Rscript" --vanilla -e 'stopifnot(getRversion()=="4.5.3",packageVersion("readr")=="2.2.0",packageVersion("haven")=="2.5.5")'; then
  operation=create
  if [[ -d "$r_dir/conda-meta" ]]; then operation=install; fi
  "$mamba" "$operation" --yes --no-rc "${cert_options[@]}" --prefix "$r_dir" --file scripts/r-linux-64.lock
fi
"$root_dir/.venv/bin/python" -m polihub doctor --offline --context cloud
