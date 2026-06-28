#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from xbotics_o20.desktop_console import main
except ImportError as exc:
    print(
        "Xbotics O20 控制台启动失败：缺少桌面依赖。请在当前目录执行 "
        "`pip install -r requirements.txt` 后重试。\n"
        f"原始错误：{exc}",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


if __name__ == "__main__":
    raise SystemExit(main(ROOT / "runtime" / "config.json"))
