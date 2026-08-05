#!/usr/bin/env python3
"""临时注册压测设备到智控台 DB（绑定已有豆包智能体）。"""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime

import pymysql


def mac_for(i: int) -> str:
    return (
        f"CA:P0:{(i >> 16) & 0xFF:02X}:"
        f"{(i >> 8) & 0xFF:02X}:"
        f"{i & 0xFF:02X}:"
        f"{(i * 7) & 0xFF:02X}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--start-index", type=int, default=10_000, help="与 capacity_bench chat 索引一致")
    p.add_argument(
        "--agent-id",
        default="3b28030117704463b8bdd542bd12897a",
        help="已配置 DoubaoLLM 的智能体",
    )
    p.add_argument("--user-id", type=int, default=1)
    p.add_argument("--cleanup", action="store_true", help="删除 alias=capacity-bench 的设备")
    args = p.parse_args()

    conn = pymysql.connect(
        host="127.0.0.1",
        user="root",
        password="123456",
        database="xiaozhi_esp32_server",
        autocommit=True,
    )
    cur = conn.cursor()
    if args.cleanup:
        cur.execute("DELETE FROM ai_device WHERE alias=%s", ("capacity-bench",))
        print(f"deleted {cur.rowcount} capacity-bench devices")
        conn.close()
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    for n in range(args.count):
        idx = args.start_index + n
        mac = mac_for(idx)
        cur.execute("SELECT id FROM ai_device WHERE mac_address=%s", (mac,))
        if cur.fetchone():
            continue
        did = uuid.uuid4().hex
        cur.execute(
            """
            INSERT INTO ai_device
              (id, user_id, mac_address, auto_update, board, alias, agent_id,
               app_version, sort, creator, create_date, updater, update_date)
            VALUES
              (%s,%s,%s,0,%s,%s,%s,%s,0,%s,%s,%s,%s)
            """,
            (
                did,
                args.user_id,
                mac,
                "capacity-bench",
                "capacity-bench",
                args.agent_id,
                "bench",
                args.user_id,
                now,
                args.user_id,
                now,
            ),
        )
        inserted += 1
    print(f"inserted={inserted} agent={args.agent_id} mac[{args.start_index}..{args.start_index+args.count-1}]")
    # verify one
    mac0 = mac_for(args.start_index)
    cur.execute("SELECT mac_address, agent_id, alias FROM ai_device WHERE mac_address=%s", (mac0,))
    print("sample", cur.fetchone())
    conn.close()


if __name__ == "__main__":
    main()
