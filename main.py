"""SAgent 程序入口。

将 src 目录加入 sys.path 后调用 CLI 主入口。
运行示例:
    python main.py --mode react
    python main.py --config config.yaml --mode plan
"""

from __future__ import annotations

import os
import sys

# 将 src 目录加入模块搜索路径，便于直接以脚本方式运行
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from sagent.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
