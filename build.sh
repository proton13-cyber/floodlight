#!/usr/bin/env bash
# Regenerate everything: campaign data -> interactive viz -> write-up (HTML + PDF).
# Requires: python3 + numpy. PDF step additionally needs node + playwright (optional).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/3  running the 12-round campaign"
python3 make_timelapse_data.py

echo "==> 2/3  building the interactive timelapse"
python3 - <<'PY'
import json, pathlib
tpl = pathlib.Path("viz/mdao_timelapse.template.html").read_text()
data = json.dumps(json.loads(pathlib.Path("timelapse_data.json").read_text()),
                  separators=(',', ':'))
out = pathlib.Path("docs/timelapse.html")
out.write_text(tpl.replace("__DATA__", data))
print(f"    docs/timelapse.html  {out.stat().st_size // 1024} KB")
PY

echo "==> 3/3  building the write-up"
if [ -d /tmp/mdao-figs ]; then
  python3 build_report.py
else
  echo "    skipped: build_report.py embeds figure snapshots captured from the"
  echo "    running viz. To regenerate them, screenshot docs/timelapse.html at"
  echo "    each round into /tmp as round_NN.jpg / grid_NN.jpg / trace_full.jpg,"
  echo "    then run: python3 build_report.py"
fi

echo "done."
