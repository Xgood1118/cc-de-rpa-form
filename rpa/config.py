# -*- coding: utf-8 -*-
"""校验规则配置加载。

允许集合与长度限制等规则从 JSON 配置文件读取，例如：

{
  "required": ["biz_id", "name", "city", "amount", "status"],
  "max_length": {"name": 20, "city": 10, "biz_id": 32},
  "enum": {"status": ["normal", "vip"]},
  "numeric": ["amount"]
}
"""
import json


class ConfigError(Exception):
    """配置文件缺失或结构非法。"""


def _check_string_list(obj, key):
    if key in obj:
        if not isinstance(obj[key], list) or not all(isinstance(v, str) for v in obj[key]):
            raise ConfigError("%s 必须是字符串列表" % key)


def load_config(path):
    """读取并校验配置文件，返回规范化后的规则字典。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise ConfigError("配置文件不存在: %s" % path)
    except json.JSONDecodeError as exc:
        raise ConfigError("配置文件不是合法 JSON: %s" % exc)
    except OSError as exc:
        raise ConfigError("配置文件无法读取: %s" % exc)

    if not isinstance(cfg, dict):
        raise ConfigError("配置顶层必须是 JSON 对象")

    _check_string_list(cfg, "required")
    _check_string_list(cfg, "numeric")

    if "max_length" in cfg:
        ml = cfg["max_length"]
        if not isinstance(ml, dict):
            raise ConfigError("max_length 必须是对象")
        for field, limit in ml.items():
            if not isinstance(field, str) or not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                raise ConfigError("max_length.%s 的长度限制必须是非负整数" % field)

    if "enum" in cfg:
        en = cfg["enum"]
        if not isinstance(en, dict):
            raise ConfigError("enum 必须是对象")
        for field, allowed in en.items():
            if not isinstance(field, str) or not isinstance(allowed, list) \
                    or not all(isinstance(v, str) for v in allowed):
                raise ConfigError("enum.%s 必须是字符串列表" % field)

    return {
        "required": list(cfg.get("required", [])),
        "max_length": dict(cfg.get("max_length", {})),
        "enum": dict(cfg.get("enum", {})),
        "numeric": list(cfg.get("numeric", [])),
    }
