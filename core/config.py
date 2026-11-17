# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L0 内核 · 配置 Schema
=================================
对应架构方案 P0：插件用声明式 CONFIG_SCHEMA 描述配置，不靠读源码。

支持类型：str / int / float / bool / choice。
- ``validate``：填默认值、校验必填/类型/取值范围/枚举，返回干净配置，
  类型或取值不合法时抛 ConfigError，错误信息带字段名，前端可直接展示。
- ``form_spec``：输出前端可直接渲染表单的结构（label/类型/选项/默认值）。
"""

__all__ = ["ConfigError", "ConfigSchema", "field"]

_TYPES = ("str", "int", "float", "bool", "choice")


class ConfigError(Exception):
    """配置校验失败。"""


def field(type="str", default=None, required=False, choices=None,
          min=None, max=None, label=None, desc=None):
    """便捷构造一个字段描述（普通 dict，便于序列化）。"""
    if type not in _TYPES:
        raise ConfigError("不支持的字段类型 %r" % type)
    return {
        "type": type, "default": default, "required": required,
        "choices": list(choices) if choices else None,
        "min": min, "max": max, "label": label, "desc": desc,
    }


def _coerce(key, spec, value):
    t = spec["type"]
    # bool 优先（bool 是 int 的子类，需先判断）
    if t == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes", "on"):
                return True
            if value.lower() in ("false", "0", "no", "off"):
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        raise ConfigError("字段 %r 需要布尔值，收到 %r" % (key, value))

    if t == "int":
        try:
            num = int(value) if not isinstance(value, float) else int(value)
        except (ValueError, TypeError):
            raise ConfigError("字段 %r 需要整数，收到 %r" % (key, value))
    elif t == "float":
        try:
            num = float(value)
        except (ValueError, TypeError):
            raise ConfigError("字段 %r 需要数字，收到 %r" % (key, value))
    elif t == "choice":
        choices = spec.get("choices") or []
        if value not in choices:
            raise ConfigError("字段 %r 必须是 %r 之一，收到 %r"
                              % (key, choices, value))
        return value
    else:  # str
        if not isinstance(value, str):
            raise ConfigError("字段 %r 需要字符串，收到 %r" % (key, value))
        return value

    if spec.get("min") is not None and num < spec["min"]:
        raise ConfigError("字段 %r 不能小于 %s" % (key, spec["min"]))
    if spec.get("max") is not None and num > spec["max"]:
        raise ConfigError("字段 %r 不能大于 %s" % (key, spec["max"]))
    return num


class ConfigSchema(object):
    def __init__(self, fields):
        if not isinstance(fields, dict) or not fields:
            raise ConfigError("ConfigSchema 需要非空字段字典")
        for key, spec in fields.items():
            if not isinstance(spec, dict) or spec.get("type") not in _TYPES:
                raise ConfigError("字段 %r 描述非法" % key)
        self.fields = fields

    def validate(self, cfg=None):
        """校验并返回填好默认值的干净配置。"""
        cfg = cfg or {}
        if not isinstance(cfg, dict):
            raise ConfigError("配置必须是字典")
        out = {}
        for key, spec in self.fields.items():
            if key in cfg and cfg[key] is not None:
                out[key] = _coerce(key, spec, cfg[key])
            elif spec.get("default") is not None:
                out[key] = spec["default"]
            elif spec.get("required"):
                raise ConfigError("缺少必填字段 %r" % key)
            else:
                out[key] = spec.get("default")
        return out

    def form_spec(self):
        """前端表单描述。"""
        return {
            "fields": [
                {"key": key, "type": spec["type"],
                 "label": spec.get("label") or key,
                 "desc": spec.get("desc"),
                 "default": spec.get("default"),
                 "required": spec.get("required", False),
                 "choices": spec.get("choices"),
                 "min": spec.get("min"), "max": spec.get("max")}
                for key, spec in self.fields.items()
            ]
        }
