"""日志插件：提供按名称命名的标准库 logger。"""

from __future__ import annotations

import logging
from typing import Any

from cordis_py import Context

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-20s %(message)s"


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """注册 ``log`` 服务：``logger = ctx.get("log")`` 后按需取子 logger。"""
    level_name = str(config.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    def get_logger(name: str = "cordis") -> logging.Logger:
        logger = logging.getLogger(name)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(_FORMAT))
            logger.addHandler(handler)
            logger.propagate = False
        logger.setLevel(level)
        return logger

    ctx.provide("log", get_logger)
