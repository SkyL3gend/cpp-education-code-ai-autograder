#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仓库根目录的启动脚本。

等价于安装后的 ``assessor`` 命令；把 ``src`` 加入导入路径，便于直接运行：

    python assessor.py task list
    python assessor.py run --task t1_phone_validation --variant correct
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cpp_assessor.interfaces.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
