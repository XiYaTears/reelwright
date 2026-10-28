"""工具层共用的错误类型。"""

from __future__ import annotations


class ToolArgumentError(ValueError):
    """工具入参不合法；编排层回填为 invalid_arguments 观察，不中断整批。"""
