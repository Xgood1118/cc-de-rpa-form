# -*- coding: utf-8 -*-
"""命令行入口。

示例：
  python -m rpa.cli rows.csv --form f001
  python -m rpa.cli rows.csv --form f001 --concurrency 4 --rate-limit 10

退出码：0 全部成功；1 存在失败或被拒；2 配置/启动错误。
"""
import argparse
import os
import sqlite3
import sys

from .config import ConfigError, load_config
from .core import Recorder, load_rows, record_all
from .state import StateStore

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(os.path.dirname(HERE), "config.json")
DEFAULT_DB = os.path.join(os.getcwd(), "rpa_state.db")


def build_parser():
    ap = argparse.ArgumentParser(prog="rpa")
    ap.add_argument("rows")
    ap.add_argument("--form", default="f001")
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="校验规则 JSON 配置")
    ap.add_argument("--db", default=DEFAULT_DB, help="断点续跑用的 SQLite 路径")
    ap.add_argument("--concurrency", type=int, default=1, help="并发连接数，默认 1")
    ap.add_argument("--rate-limit", type=float, default=0,
                    help="每秒最多提交次数，0 或负数表示不限速")
    ap.add_argument("--max-403-retries", type=int, default=3,
                    help="单行 403 会话失效的最大重试次数")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.concurrency < 1:
        print("配置错误: --concurrency 必须 >= 1", file=sys.stderr)
        return 2
    if args.max_403_retries < 0:
        print("配置错误: --max-403-retries 必须 >= 0", file=sys.stderr)
        return 2

    try:
        rules = load_config(args.config)
        rows = load_rows(args.rows)
    except ConfigError as exc:
        print("配置错误: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("配置错误: 无法读取数据文件 %s: %s" % (args.rows, exc), file=sys.stderr)
        return 2

    try:
        store = StateStore(args.db)
    except sqlite3.Error as exc:
        print("配置错误: 无法打开状态库 %s: %s" % (args.db, exc), file=sys.stderr)
        return 2

    rec = Recorder(args.base)
    result = record_all(
        rec, args.form, rows, config=rules, store=store,
        concurrency=args.concurrency, rate_limit=args.rate_limit,
        max_403_retries=args.max_403_retries,
    )
    store.close()

    stats = result.as_dict()
    print("批量录入完成：")
    print("  total              = %d" % stats["total"])
    print("  submitted          = %d" % stats["submitted"])
    print("  duplicate_skipped  = %d" % stats["duplicate_skipped"])
    print("  rejected           = %d" % stats["rejected"])
    print("  failed             = %d" % stats["failed"])
    print("  retried            = %d" % stats["retried"])

    problems = [r for r in result.rows if r["status"] in ("failed", "rejected")]
    if problems:
        print("问题行明细：")
        for r in problems:
            print("  [%s] %s -> %s" % (r["status"], r["biz_id"], r["reason"]))

    return 0 if result.failed == 0 and result.rejected == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
