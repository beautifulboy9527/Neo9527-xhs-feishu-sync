#!/usr/bin/env python3
"""在飞书多维表格中创建评论数据表 tblComments"""
import json, subprocess, sys

APP_TOKEN = "Ro3EbZ5vLaXCljs651kc8j8Lndh"
TABLE_NAME = "tblComments"

# 字段定义: (name, type, property)
# type: 1=Text, 2=Number, 3=SingleSelect, 4=MultiSelect, 5=DateTime, 7=Checkbox, 15=URL
FIELDS = [
    ("评论ID", 1, None),
    ("笔记ID", 1, None),
    ("笔记标题", 1, None),
    ("评论内容", 1, None),
    ("评论者昵称", 1, None),
    ("评论者ID", 1, None),
    ("是否贴主", 7, None),
    ("是否主评论", 7, None),
    ("父评论ID", 1, None),
    ("回复谁", 1, None),
    ("评论点赞数", 2, {"formatter": "0"}),
    ("子评论数", 2, {"formatter": "0"}),
    ("IP属地", 1, None),
    ("评论时间", 1, None),
    ("采集时间", 5, {"auto_fill": True, "date_formatter": "yyyy/MM/dd HH:mm"}),
    ("原始JSON", 1, None),
]

def main():
    # Step 1: Create table
    cols = len(FIELDS) + 1  # +1 for row_id auto column
    cmd = [
        "openclaw", "bitable", "create-table",
        "--app-token", APP_TOKEN,
        "--name", TABLE_NAME,
        "--row-size", "1",
        "--column-size", str(cols),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print(f"Create table: {r.stdout.strip()}")
    if r.returncode != 0:
        print(f"STDERR: {r.stderr.strip()}")
        # If table exists, try to find it
        sys.exit(1)

    # Extract table_id from output (or use a known approach)
    # Actually we need to get the table_id - let's use feishu API directly via openclaw
    # For now, print the result and let user check
    print(f"\nTable '{TABLE_NAME}' created/exists in {APP_TOKEN}")
    print("\nNow creating fields...")

    # We'll need the table_id. Let's parse it from create-table output
    # or use a known pattern. For safety, let's list tables first.
    print("Done with table creation. Fields need to be added separately.")
    print("\nFields to add:")
    for name, ftype, prop in FIELDS:
        print(f"  {name} (type={ftype})")

if __name__ == "__main__":
    main()
