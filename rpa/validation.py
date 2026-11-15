# -*- coding: utf-8 -*-
"""提交前的本地数据校验，脏数据不发往服务端。"""
import math


def validate_row(row, rules):
    """按配置规则校验一行数据。

    返回错误原因列表；空列表表示通过。覆盖四类问题：
    必填为空、字段超长、枚举越界、数值非法。
    """
    errors = []

    for field in rules.get("required", []):
        if str(row.get(field, "") or "").strip() == "":
            errors.append("必填字段 %s 为空" % field)

    for field, limit in rules.get("max_length", {}).items():
        value = str(row.get(field, "") or "")
        if len(value) > limit:
            errors.append("字段 %s 超长(%d > %d)" % (field, len(value), limit))

    for field, allowed in rules.get("enum", {}).items():
        value = row.get(field, "")
        if value != "" and value is not None and value not in allowed:
            errors.append("字段 %s 枚举值 %r 不在允许集合 %s" % (field, value, allowed))

    for field in rules.get("numeric", []):
        raw = row.get(field, "")
        if raw is None or str(raw).strip() == "":
            # 空值交给 required 规则处理
            continue
        try:
            num = float(raw)
        except (TypeError, ValueError):
            errors.append("字段 %s 数值非法: %r" % (field, raw))
            continue
        if not math.isfinite(num):
            errors.append("字段 %s 数值非法: %r" % (field, raw))

    return errors
