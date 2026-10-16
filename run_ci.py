#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_ci.py — 云端自动运行入口

与 main.py 的区别：配置不从 config.yaml 读取，而是从环境变量读取。
这样邮箱密码可以存放在 GitHub Secrets 里，不会暴露在代码中。

需要在环境变量中提供：
    MAIL_SENDER      发件邮箱
    MAIL_PASSWORD    邮箱授权码
    MAIL_RECIPIENTS  收件邮箱（多个用英文逗号分隔）
    SMTP_SERVER      可选，默认 smtp.qq.com
    SMTP_PORT        可选，默认 465
    USE_SSL          可选，默认 true
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("daily-news")


def build_config() -> dict:
    """从环境变量组装配置。"""
    sender = os.environ.get("MAIL_SENDER", "").strip()
    password = os.environ.get("MAIL_PASSWORD", "").strip()
    recipients_raw = os.environ.get("MAIL_RECIPIENTS", "").strip()

    # 配置检查
    missing = []
    if not sender:
        missing.append("MAIL_SENDER")
    if not password:
        missing.append("MAIL_PASSWORD")
    if not recipients_raw:
        missing.append("MAIL_RECIPIENTS")
    if missing:
        logger.error(
            "缺少必要的环境变量: %s\n"
            "请在 GitHub 仓库的 Settings → Secrets and variables → Actions "
            "中添加这些密钥。",
            ", ".join(missing),
        )
        sys.exit(1)

    # 收件人支持逗号 / 分号 / 换行分隔
    recipients = [
        r.strip()
        for r in recipients_raw.replace(";", ",").replace("\n", ",").split(",")
        if r.strip()
    ]

    return {
        "email": {
            "smtp_server": os.environ.get("SMTP_SERVER", "smtp.qq.com").strip(),
            "smtp_port": int(os.environ.get("SMTP_PORT", "465")),
            "use_ssl": os.environ.get("USE_SSL", "true").strip().lower() != "false",
            "sender": sender,
            "password": password,
            "recipients": recipients,
            "subject_template": "【每日新闻】{date} 今日热点速览",
            "sender_name": "每日新闻",
        },
        "news": {
            "max_items_per_section": 8,
            "sources": ["baidu", "thepaper", "chinanews", "thepaper_world"],
            "timeout": 15,
            "retry": 2,
        },
        "output": {
            "save_json": True,
            "json_dir": "./data",
        },
    }


def main() -> int:
    import main as app

    config = build_config()
    logger.info("配置加载成功，收件人: %s", ", ".join(config["email"]["recipients"]))

    ok = app.run_once(config, preview_only=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
