# -*- coding: utf-8 -*-
"""命令行入口：python -m rpa.cli rows.csv --form f001 --base http://127.0.0.1:8099"""
import argparse
import sys

from .core import Recorder, load_rows, record_all


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rpa")
    ap.add_argument("rows")
    ap.add_argument("--form", default="f001")
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    args = ap.parse_args(argv)
    rows = load_rows(args.rows)
    rec = Recorder(args.base)
    results = record_all(rec, args.form, rows)
    ok = sum(1 for r in results if r["status"] == 200)
    print("待录入 %d 行，成功 %d 行" % (len(rows), ok))
    for r in results:
        if r["status"] != 200:
            print("  失败 %s -> %s %s" % (r["row"].get("biz_id"), r["status"], r["body"][:40]))
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
