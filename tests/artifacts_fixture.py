import json, datetime as dt

now = dt.datetime.now(dt.timezone.utc)


def iso(hours_ago):
    return (now - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


arts = [
    # id, name, hours_ago, expired, run_id, size
    (101, "github-pages", 200, False, 1001, 5_000_000),   # 老 -> 删
    (102, "github-pages", 100, False, 1002, 5_000_000),   # 老 -> 删
    (103, "github-pages", 2,   False, 1003, 5_000_000),   # 新 -> 保留(KEEP_HOURS)
    (104, "github-pages", 50,  False, 9001, 5_000_000),   # 属于 in_progress run -> 保留
    (105, "pytest-report", 300, True, 1005, 1_000),       # 已过期 -> 跳过
    (106, "pytest-report", 150, False, 1006, 2_000_000),  # 同名最新未过期 -> rank0 保留
    (107, "pytest-report", 250, False, 1007, 2_000_000),  # rank1 -> 删
    (777, "orphan", 400, False, 1008, 900),               # 删除时 404 -> 容错
    (108, "orphan", 401, False, 9002, 900),               # queued run -> 保留
    (109, "self", 500, False, 424242, 100),               # 本次 run 自己 -> 保留
]

print(json.dumps({
    "total_count": len(arts),
    "artifacts": [
        {
            "id": i, "name": n, "size_in_bytes": s, "created_at": iso(h),
            "expired": e, "workflow_run": {"id": r},
        }
        for (i, n, h, e, r, s) in arts
    ],
}, indent=1))
