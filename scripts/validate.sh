#!/bin/bash
set -euo pipefail

plugin_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
omarchy_path="${OMARCHY_PATH:-/usr/share/omarchy}"

cd "$plugin_dir"
omarchy plugin validate .

for file in ./*.qml; do
  qmllint -I "$omarchy_path/shell" "$file"
done

python3 -m py_compile bin/omaplex
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/model.test.mjs

