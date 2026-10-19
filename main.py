#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — 每日新闻推送主程序

用法:
    python3 main.py            # 立即抓取并发送一封邮件
    python3 main.py --preview  # 只生成 HTML 预览，不发邮件
    python3 main.py --test     # 同 --preview，但会校验配置完整性
    python3 main.py --daemon   # 常驻运行，每天定时自动推送
    python3 main.py --once-at 08:00   # 配合系统计划任务使用（等价于默认行为）

首次使用请先执行:
    cp config.example.yaml config.yaml
    然后编辑 config.yaml 填入邮箱信息
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("缺少依赖 PyYAML，请先执行: pip3 install -r requirements.txt")
    sys.exit(1)

from email_template import build_email_html, build_email_text
from mailer import MailSendError, detect_smtp_config, send_mail
from news_fetcher import (  # noqa: E402
    deduplicate,
    deduplicate_across_sections,
    fetch_all,
)
from news_body import fetch_bodies  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
CONFIG_EXAMPLE_PATH = BASE_DIR / "config.example.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("daily-news")


# ============================================================
#  配置加载
# ============================================================
def load_config(path: Path = CONFIG_PATH, require_email: bool = True) -> dict:
    """
    加载并校验配置文件。

    参数:
        require_email: 为 True 时严格校验邮箱配置（发送邮件前使用）；
                       为 False 时只做基本读取（生成预览时使用）。
    """
    if not path.exists():
        logger.error("配置文件不存在: %s", path)
        if CONFIG_EXAMPLE_PATH.exists():
            logger.error(
                "请先复制模板并填写你的邮箱信息：\n"
                "    cp %s %s",
                CONFIG_EXAMPLE_PATH.name, path.name,
            )
        sys.exit(1)

    try:
        with open(path, encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        logger.error("配置文件格式错误: %s", exc)
        sys.exit(1)

    email_cfg = config.setdefault("email", {})

    if require_email:
        # 基本校验
        missing = [
            key for key in ("sender", "password", "recipients")
            if not email_cfg.get(key)
        ]
        if missing:
            logger.error("配置缺少必要字段: email.%s", ", email.".join(missing))
            sys.exit(1)

        # 拦截未修改的占位符配置，避免用户困惑于认证失败
        placeholders = ("your_email@", "your_authorization_code", "example.com")
        sender_val = str(email_cfg.get("sender", ""))
        password_val = str(email_cfg.get("password", ""))
        if any(p in sender_val for p in placeholders) or \
           any(p in password_val for p in placeholders):
            logger.error(
                "检测到 config.yaml 仍是模板占位内容，尚未填入真实邮箱信息。\n"
                "  请编辑 config.yaml，填写以下三项：\n"
                "    email.sender     当前值: %s\n"
                "    email.password   请填入邮箱「授权码」（不是登录密码）\n"
                "    email.recipients 收件邮箱地址\n"
                "  各邮箱授权码获取方式见 使用指南.md。\n"
                "  提示：只想先看效果可以运行：python3 main.py --preview",
                sender_val,
            )
            sys.exit(1)

    # 自动补全 SMTP 服务器（若用户未填写或填了占位符）
    sender = str(email_cfg.get("sender", "")).strip()
    if "@" in sender:
        preset = detect_smtp_config(sender)
        if preset:
            server = str(email_cfg.get("smtp_server", "")).strip()
            if not server or "your_email" in server:
                email_cfg["smtp_server"] = preset["server"]
                email_cfg["smtp_port"] = preset["port"]
                email_cfg["use_ssl"] = preset["ssl"]
                logger.info("已根据邮箱后缀自动配置 SMTP: %s:%s",
                            preset["server"], preset["port"])

    # 收件人允许写成单个字符串
    recipients = email_cfg.get("recipients")
    if isinstance(recipients, str):
        email_cfg["recipients"] = [recipients]

    return config


# ============================================================
#  核心流程
# ============================================================
def collect_news(config: dict) -> dict:
    """抓取全部新闻并按数据源分组。"""
    news_cfg = config.get("news", {})
    sources = news_cfg.get("sources") or ["baidu", "thepaper", "chinanews"]
    per_section = int(news_cfg.get("max_items_per_section", 8))

    logger.info("开始抓取新闻（数据源: %s）...", ", ".join(sources))
    raw = fetch_all(
        sources=sources,
        timeout=int(news_cfg.get("timeout", 15)),
        retry=int(news_cfg.get("retry", 2)),
        per_source_limit=max(per_section * 4, 30),
    )

    # 每个板块内部去重
    sections = {key: deduplicate(items) for key, items in raw.items()}
    # 全局去重：同一条新闻只保留优先级最高的板块
    sections = deduplicate_across_sections(sections)
    total = sum(len(v) for v in sections.values())
    logger.info("抓取完成，共 %d 条（去重后）", total)

    if total == 0:
        logger.warning("未抓取到任何新闻，请检查网络连接")
        return sections

    # 抓取正文原文。搜索页类链接（如百度热搜）直接抓不到，
    # news_body 会按标题反查一篇报道再抓。
    detail_cfg = config.get("detail", {}) or {}
    want_quota = detail_cfg.get("quota") or {"baidu": 6, "thepaper": 5,
                                             "chinanews": 5, "thepaper_world": 4}
    # 多取几条备选，用来补足「抓不到原文」的空缺
    spare = int(detail_cfg.get("spare", 6))
    retry_search = bool(detail_cfg.get("retry_search", True))

    ok_total = 0
    pick_total = 0
    for key, items in sections.items():
        quota = int(want_quota.get(key, per_section))
        pool = items[:quota + spare]
        got = fetch_bodies(pool, key, retry_search=retry_search)
        pick_total += len(pool)
        ok_total += got
    logger.info("  原文抓取完成：%d/%d 条成功", ok_total, pick_total)

    # 只留下「有正文」的条目：没内容的新闻不往邮件里放
    keep_empty = bool(detail_cfg.get("keep_without_body", False))
    if not keep_empty:
        for key in list(sections.keys()):
            sections[key] = [it for it in sections[key] if it.get("body")]
    for key in list(sections.keys()):
        quota = int(want_quota.get(key, per_section))
        sections[key] = sections[key][:quota]
    kept = sum(len(v) for v in sections.values())
    logger.info("  最终保留 %d 条（无正文的已剔除）", kept)

    # AI 生成「一句话 + 完整说明」
    from ai_brief import AIConfig, enrich_items  # noqa: E402

    ai_cfg = AIConfig()
    if ai_cfg.enabled:
        logger.info("正在用 AI 生成说明（%s / %s）...", ai_cfg.provider, ai_cfg.model)
        n = 0
        for key, items in sections.items():
            stat = enrich_items(items, key, ai_cfg)
            n += len(items)
            logger.info("  %s: 成功 %d / 跳过 %d / 失败 %d",
                        key, stat["ok"], stat["skip"], stat["fail"])
        logger.info("  AI 说明生成完毕，共处理 %d 条", n)
    else:
        logger.info("未配置 AI 密钥，本次不生成 AI 说明（沿用原文）")

    return sections


def save_json_backup(sections: dict, config: dict) -> Path | None:
    """把当日新闻存档为 JSON。"""
    output_cfg = config.get("output", {})
    if not output_cfg.get("save_json", True):
        return None

    json_dir = Path(output_cfg.get("json_dir", "./data"))
    if not json_dir.is_absolute():
        json_dir = BASE_DIR / json_dir
    json_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    path = json_dir / f"news-{today}.json"

    payload = {
        "date": today,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": sum(len(v) for v in sections.values()),
        "sections": sections,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    logger.info("新闻数据已存档: %s", path)
    return path


def build_email(config: dict, sections: dict) -> tuple[str, str, str]:
    """生成邮件主题与正文。"""
    email_cfg = config.get("email", {})
    news_cfg = config.get("news", {})

    today = datetime.now()
    date_str = f"{today.year}年{today.month}月{today.day}日"

    subject_tpl = email_cfg.get("subject_template") or "【每日新闻】{date} 今日热点速览"
    subject = subject_tpl.format(date=date_str)

    max_items = int(news_cfg.get("max_items_per_section", 8))
    html_body = build_email_html(sections, max_items_per_section=max_items)
    text_body = build_email_text(sections, max_items_per_section=max_items)

    return subject, html_body, text_body


def run_once(config: dict, preview_only: bool = False) -> bool:
    """执行一次完整流程：抓取 → 生成 → 发送。"""
    started = time.time()

    # 1. 抓取
    sections = collect_news(config)
    if not sections or sum(len(v) for v in sections.values()) == 0:
        logger.error("没有获取到新闻内容，本次不发送邮件")
        return False

    # 2. 存档
    save_json_backup(sections, config)

    # 3. 生成邮件
    subject, html_body, text_body = build_email(config, sections)

    # 4. 预览或发送
    preview_path = BASE_DIR / "preview.html"
    preview_path.write_text(html_body, encoding="utf-8")

    if preview_only:
        logger.info("预览模式：已生成 %s（未发送邮件）", preview_path)
        logger.info("邮件主题: %s", subject)
        return True

    email_cfg = config["email"]
    try:
        send_mail(
            smtp_server=str(email_cfg["smtp_server"]),
            smtp_port=int(email_cfg.get("smtp_port", 465)),
            sender=str(email_cfg["sender"]),
            password=str(email_cfg["password"]),
            recipients=list(email_cfg["recipients"]),
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            use_ssl=bool(email_cfg.get("use_ssl", True)),
            sender_name=str(email_cfg.get("sender_name", "每日新闻")),
        )
    except MailSendError as exc:
        logger.error("邮件发送失败：\n%s", exc)
        return False

    elapsed = time.time() - started
    logger.info("本次推送完成，耗时 %.1f 秒", elapsed)
    return True


# ============================================================
#  定时守护进程
# ============================================================
def run_daemon(config: dict) -> None:
    """常驻运行，每天在指定时间推送。"""
    try:
        import schedule
    except ImportError:
        logger.error("缺少依赖 schedule，请执行: pip3 install schedule")
        sys.exit(1)

    schedule_cfg = config.get("schedule", {})
    push_time = str(schedule_cfg.get("time", "08:00"))
    timezone = str(schedule_cfg.get("timezone", "Asia/Shanghai"))

    # 设置时区（尽量，失败不影响运行）
    try:
        os.environ["TZ"] = timezone
        time.tzset()
    except (AttributeError, OSError):
        pass

    logger.info("=" * 56)
    logger.info("每日新闻推送服务已启动")
    logger.info("推送时间: 每天 %s (%s)", push_time, timezone)
    logger.info("按 Ctrl+C 退出")
    logger.info("=" * 56)

    schedule.every().day.at(push_time).do(run_once, config=config)

    # 启动时先跑一次，确认链路通畅
    logger.info("正在执行启动检查...")
    run_once(config, preview_only=False)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            logger.info("已停止推送服务")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("调度循环异常: %s", exc)
            time.sleep(60)


# ============================================================
#  命令行入口
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="每日新闻推送 - 每天定时把热点新闻发送到你的邮箱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 main.py              立即抓取并发送邮件
  python3 main.py --preview    只生成 HTML 预览（不发邮件）
  python3 main.py --daemon     常驻运行，每天定时自动推送
        """,
    )
    parser.add_argument("--preview", action="store_true",
                        help="只生成预览文件，不发送邮件")
    parser.add_argument("--no-search", action="store_true",
                        help="不按标题反查原文（加快速度，但会有更多条被剔除）")
    parser.add_argument("--test", action="store_true",
                        help="测试配置与抓取（不发邮件）")
    parser.add_argument("--daemon", action="store_true",
                        help="常驻运行，按配置时间每天推送")
    parser.add_argument("--config", type=str, default=str(CONFIG_PATH),
                        help="指定配置文件路径")
    args = parser.parse_args()

    # 预览 / 测试模式不需要邮箱配置，可先看效果再填邮箱
    preview_only = args.preview or args.test
    config = load_config(Path(args.config), require_email=not preview_only)
    if args.no_search:
        config.setdefault("detail", {})["retry_search"] = False

    if args.daemon:
        run_daemon(config)
        return 0

    ok = run_once(config, preview_only=preview_only)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
