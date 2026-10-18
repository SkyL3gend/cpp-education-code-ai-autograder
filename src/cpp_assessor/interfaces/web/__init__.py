# -*- coding: utf-8 -*-
"""本地 Web 界面（任务书任务 4）。

入口：``assessor web``，实现见 :mod:`cpp_assessor.interfaces.web.app`。
"""

from .app import WebSettings, create_app, serve

__all__ = ["WebSettings", "create_app", "serve"]
