#!/usr/bin/env python
"""测试专用：一个极小的 jq CLI，底层是真 libjq（PyPI `jq` 包）。

为什么需要它
------------
`tools/ci/retry_transient_failure.sh` 用 jq 解析 `gh api` 的输出，而 jq 在
GitHub 托管的 runner 上是预装的 —— 脚本对此的假设是正确的，不该为了迁就本机
环境而改写。但本机（Windows / Git Bash）通常没有 jq，于是这些测试在本地只能
全部跳过，等于没有本地验证。

本文件在**系统没有 jq 时**作为替身：它不是自己实现的解析器，而是把参数转交给
libjq（PyPI `jq` 包，jq 1.12 的原生绑定），所以语义与真 jq 一致，只少了 CLI 的
外围选项。仅支持脚本实际用到的那几种调用形态；遇到不认识的参数会显式报错，
而不是静默给出错误结果。

用法（由测试通过 PATH 注入，模拟成 `jq`）：
    jq -r '<program>' [file]      # 无 file 时读 stdin
"""

from __future__ import annotations

import json
import sys

try:
    import jq as _jq
except ImportError:  # pragma: no cover - 由测试提前判定
    sys.stderr.write("jq_cli.py: PyPI `jq` 包未安装\n")
    raise SystemExit(3)


_USAGE = "jq_cli.py: 仅支持 `[-r] <program> [file]`，读 stdin 或单个文件"


def _parse(argv: list[str]) -> tuple[bool, str, str | None]:
    raw = False
    program: str | None = None
    files: list[str] = []

    for arg in argv:
        if arg == "-r":
            raw = True
        elif arg in ("-c", "-n", "-e", "--raw-output"):
            # 脚本没有用到；显式拒绝好过静默忽略后给出错误结果
            raise SystemExit(f"jq_cli.py: 不支持的选项 {arg}。{_USAGE}")
        elif arg.startswith("-") and len(arg) > 1 and program is None:
            raise SystemExit(f"jq_cli.py: 不支持的选项 {arg}。{_USAGE}")
        elif program is None:
            program = arg
        else:
            files.append(arg)

    if program is None:
        raise SystemExit(_USAGE)
    if len(files) > 1:
        raise SystemExit(_USAGE)
    return raw, program, (files[0] if files else None)


def main(argv: list[str]) -> int:
    raw, program, path = _parse(argv)

    if path is not None:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    try:
        results = _jq.compile(program).input_text(text).all()
    except Exception as exc:  # libjq 的编译/运行错误
        sys.stderr.write(f"jq: error: {exc}\n")
        return 5

    lines: list[str] = []
    for value in results:
        if raw:
            if value is None:
                # jq -r 对 null 输出字面量 "null"
                lines.append("null")
            elif isinstance(value, str):
                lines.append(value)
            else:
                lines.append(json.dumps(value, ensure_ascii=False))
        else:
            lines.append(json.dumps(value, ensure_ascii=False))

    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
