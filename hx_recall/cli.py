"""命令行入口"""

import argparse
import asyncio

from hx_recall.bilibili.core import run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HX-Recall: 收藏夹回顾推送工具",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "-k",
        "--top-k",
        type=int,
        default=None,
        help="覆盖配置中的 top_k 值",
    )
    parser.add_argument(
        "-s",
        "--strategy",
        choices=["random", "latest", "oldest", "dusty"],
        default=None,
        help="覆盖配置中的选取策略",
    )
    args = parser.parse_args()

    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
