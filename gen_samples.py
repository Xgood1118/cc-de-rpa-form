# -*- coding: utf-8 -*-
"""生成待录入数据。12 行，含 2 个重复业务主键、1 个超长字段、1 个非法枚举值。"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))

ROWS = [
    {"biz_id": "B001", "name": "张三", "city": "长春", "amount": "1200", "status": "normal"},
    {"biz_id": "B002", "name": "李四", "city": "吉林", "amount": "800", "status": "normal"},
    {"biz_id": "B003", "name": "王五", "city": "四平", "amount": "1500", "status": "normal"},
    {"biz_id": "B001", "name": "张三", "city": "长春", "amount": "1200", "status": "normal"},
    {"biz_id": "B004", "name": "赵六", "city": "长春", "amount": "2300", "status": "normal"},
    {"biz_id": "B005", "name": "", "city": "通化", "amount": "900", "status": "normal"},
    {"biz_id": "B006", "name": "孙七" * 30, "city": "白城", "amount": "1100", "status": "normal"},
    {"biz_id": "B007", "name": "周八", "city": "松原", "amount": "abc", "status": "normal"},
    {"biz_id": "B008", "name": "吴九", "city": "辽源", "amount": "700", "status": "unknown"},
    {"biz_id": "B009", "name": "郑十", "city": "白山", "amount": "1300", "status": "normal"},
    {"biz_id": "B010", "name": "王五", "city": "四平", "amount": "1500", "status": "normal"},
    {"biz_id": "B011", "name": "冯十一", "city": "长春", "amount": "1600", "status": "normal"},
]


def main():
    with open(os.path.join(HERE, "rows.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(ROWS[0].keys()))
        w.writeheader()
        w.writerows(ROWS)
    print("待录入 %d 行" % len(ROWS))


if __name__ == "__main__":
    main()
