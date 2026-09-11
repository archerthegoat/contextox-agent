#!/bin/sh
# Install a pinned ContextOx release; no Python, Node, UV, sudo, or shell-profile edits.
set -eu
version=1.0.0
release_url="https://github.com/archerthegoat/contextox-agent/releases/download/v${version}"
archive_name="contextox-${version}-macos-arm64.tar.gz"
archive_path=
archive_sha=
install_dir="${HOME}/.local/share/contextox"
start_after_install=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --archive) archive_path=$2; shift 2 ;;
    --sha256) archive_sha=$2; shift 2 ;;
    --install-dir) install_dir=$2; shift 2 ;;
    --no-start) start_after_install=0; shift ;;
    *) printf '%s\n' "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  printf '%s\n' '当前运行包仅支持 macOS Apple 芯片。' >&2
  exit 2
fi
staging=$(mktemp -d "${TMPDIR:-/tmp}/contextox-download.XXXXXX")
trap 'rm -rf "$staging"' EXIT HUP INT TERM
if [ -z "$archive_path" ]; then
  printf '%s\n' "下载数契 ${version}…"
  archive_path="$staging/$archive_name"
  curl --fail --location --proto '=https' --tlsv1.2 "$release_url/$archive_name" --output "$archive_path"
  curl --fail --location --proto '=https' --tlsv1.2 "$release_url/SHA256SUMS" --output "$staging/SHA256SUMS"
  archive_sha=$(awk -v name="$archive_name" '$2 == name {print $1}' "$staging/SHA256SUMS")
fi
if ! printf '%s\n' "$archive_sha" | /usr/bin/grep -Eq '^[0-9a-f]{64}$'; then
  printf '%s\n' '需要有效的 SHA256 校验值。' >&2; exit 2
fi
actual_sha=$(shasum -a 256 "$archive_path" | awk '{print $1}')
if [ "$actual_sha" != "$archive_sha" ]; then
  printf '%s\n' '下载校验失败，原有安装未改变。' >&2; exit 1
fi
mkdir "$staging/extract"
tar -xzf "$archive_path" -C "$staging/extract"
bundle="$staging/extract/contextox-${version}-macos-arm64"
launcher=$("$bundle/python/bin/python3" -I -B "$bundle/install_bundle.py" --install-dir "$install_dir")
printf '%s\n' "安装完成。启动命令：\"$launcher\" start"
if [ "$start_after_install" -eq 1 ]; then
  "$launcher" start
fi
