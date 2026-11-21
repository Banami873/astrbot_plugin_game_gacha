"""把随包的命途 SVG 渲染成透明 PNG（出图用）。

SVG 原件来自 hsr.wishsimulator.app 现网 C9tu6MWG.js 里的 9 个内嵌组件，
已内联配色（fill:#fff + #ffffff59）并存进 resources/hsr/ui/path_icons/。
Pillow 没有 SVG 解码器，这里借本机 Chrome 光栅化——站点本身就是 Chrome 渲染的，
比 cairosvg 之类的第三方实现更贴近站点所见。

用法：python tools/build_hsr_path_icons.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RES = ROOT / "resources" / "hsr" / "ui" / "path_icons"
CHROME = pathlib.Path(
    r"C:/Program Files/Google/Chrome/Application/chrome.exe"
)


def main() -> int:
    if not CHROME.exists():
        print(f"找不到 Chrome：{CHROME}", file=__import__("sys").stderr)
        return 2
    manifest = json.loads((RES / "manifest.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for path, info in manifest.items():
            svg = (RES / info["file"]).read_text(encoding="utf-8")
            h = info["h"]
            page = tmp / f"{path}.html"
            page.write_text(
                "<!doctype html><html><head><meta charset='utf-8'><style>"
                "html,body{margin:0;padding:0;background:transparent}"
                f"body{{width:{info['w']}px;height:{h}px}}"
                "svg{display:block}</style></head>"
                f"<body>{svg}</body></html>",
                encoding="utf-8",
            )
            png = tmp / f"{path}.png"
            subprocess.run(
                [
                    str(CHROME), "--headless=new", "--disable-gpu",
                    "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={info['w']},{h}", "--virtual-time-budget=1500",
                    "--default-background-color=00000000",
                    f"--screenshot={png}", page.as_uri(),
                ],
                capture_output=True, text=True,
            )
            if not png.exists():
                print(f"  ! {path} 渲染失败", file=__import__("sys").stderr)
                return 1
            (RES / f"{path}.png").write_bytes(png.read_bytes())
            print(f"{path} -> {info['w']}x{h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
