#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
email_template.py — HTML 邮件模板生成（详解版）

把抓取到的新闻渲染成一份兼容主流邮箱客户端（QQ邮箱 / 网易 / Gmail / Outlook）
的 HTML 邮件。全部使用内联样式 + 表格布局，不使用外部 CSS 与 JavaScript。

内容格式：每天 20 条，每条含三段详解（发生了什么 / 为什么重要 / 小知识）+ 生词
"""

from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news_detail import DETAIL, FALLBACK  # noqa: E402
from news_digest import brief_title, find_digest  # noqa: E402

# 板块配色与图标：数据源 key -> (显示名, 副标题, 主色, emoji)
SECTION_STYLE: Dict[str, tuple] = {
    "baidu":          ("热搜榜",   "大家都在看",   "#E8443A", "🔥"),
    "thepaper":       ("时政要闻", "国家大事",     "#C62828", "📰"),
    "chinanews":      ("综合资讯", "身边的新鲜事", "#1565C0", "📡"),
    "thepaper_world": ("国际视野", "国外发生了啥", "#2E7D32", "🌍"),
    "default":        ("更多资讯", "", "#546E7A", "📌"),
}

# 每天条数与板块分配（合计 20）
SECTION_QUOTA: Dict[str, int] = {
    "baidu": 6,
    "thepaper": 5,
    "chinanews": 5,
    "thepaper_world": 4,
}
DAILY_LIMIT = 20

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 三段讲解的配色
TAG_STYLE = {
    "what": ("发生了什么", "#E3F2FD", "#1565C0"),
    "why":  ("为什么重要", "#FFF3E0", "#E65100"),
    "know": ("小知识",     "#E8F5E9", "#2E7D32"),
}


def _esc(text: str) -> str:
    """HTML 转义，防止标题中的特殊字符破坏排版。"""
    return html.escape(str(text or ""), quote=True)


def _one_line(item: Dict[str, str], section_key: str = "") -> str:
    """速览用的一句话：AI 生成优先，其次内置要点库，最后用标题兜底。"""
    dg = (item.get("ai_one_line") or "").strip()
    if dg:
        return dg
    dg = find_digest(item.get("title", ""), section_key)
    if dg and dg != item.get("title"):
        return dg
    return brief_title(item.get("title", ""))


def _has_content(item: Dict[str, str]) -> bool:
    """这条新闻有没有值得展示的内容（AI 说明或原文）。"""
    return bool((item.get("ai_explain") or "").strip()
                or (item.get("body") or "").strip())


def find_detail(title: str) -> dict:
    """按关键词匹配详解，命中多条时取关键词最长者。"""
    best, best_len = None, -1
    for key, val in DETAIL.items():
        if key in title and len(key) > best_len:
            best, best_len = val, len(key)
    if best:
        return best
    return {"what": FALLBACK["what"], "why": FALLBACK["why"],
            "know": FALLBACK["know"], "words": FALLBACK["words"]}


def _render_card(idx: int, item: Dict[str, str], color: str,
                 section_key: str = "") -> str:
    """渲染单条新闻卡片（含一句话要点 + 三段详解）。"""
    title = _esc(item.get("title", ""))
    url = _esc(item.get("url", ""))
    source = _esc(item.get("source", ""))
    time_text = _esc(item.get("time", ""))

    d = find_detail(item.get("title", ""))
    # 优先用 AI 生成的一句话；没有才回落到内置要点库
    digest = (item.get("ai_one_line") or "").strip() \
        or find_digest(item.get("title", ""), section_key)

    # 序号徽章
    if idx <= 3:
        badge_bg, badge_color = color, "#FFFFFF"
    else:
        badge_bg, badge_color = "#ECEFF1", "#607D8B"

    # 正文区：优先用 AI 写的完整说明；其次用抓来的新闻原文；都没有才说明情况
    ai_explain = (item.get("ai_explain") or "").strip()
    body = (item.get("body") or "").strip()

    if ai_explain:
        # AI 说明用 || 分段
        parts = [p.strip() for p in ai_explain.split("||") if p.strip()]
        if len(parts) == 1:
            parts = [p.strip() for p in ai_explain.split("\n") if p.strip()]
        paras = "".join(
            f'<p style="margin:0 0 10px 0;font-size:13.5px;line-height:1.85;color:#2b3140;'
            f"font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;\">"
            f"{_esc(p)}</p>"
            for p in parts
        )
        blocks_html = (
            '<div style="margin-bottom:11px;font-size:11px;font-weight:700;'
            'color:#8b93a5;letter-spacing:.5px;">📖 新闻说明</div>' + paras
        )
        if body:
            blocks_html += (
                '<div style="margin-top:14px;padding-top:12px;border-top:1px dashed #E3E9F0;'
                'font-size:12.5px;line-height:1.8;color:#8b93a5;">'
                f'原文出处：{_esc(source)}　'
                f'<a href="{url}" target="_blank" style="color:#3949AB;text-decoration:none;">'
                '阅读完整报道 →</a></div>'
            )
    elif body:
        paras = "".join(
            f'<p style="margin:0 0 10px 0;font-size:13.5px;line-height:1.85;color:#2b3140;'
            f"font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;\">"
            f"{_esc(p)}</p>"
            for p in body.split("\n") if p.strip()
        )
        blocks_html = (
            '<div style="margin-bottom:11px;font-size:11px;font-weight:700;'
            'color:#8b93a5;letter-spacing:.5px;">📄 新闻原文</div>'
            + paras
        )
    else:
        blocks_html = (
            '<div style="margin-bottom:11px;font-size:11px;font-weight:700;'
            'color:#8b93a5;letter-spacing:.5px;">📖 新闻说明</div>'
            f'<p style="margin:0 0 8px 0;font-size:13.5px;line-height:1.85;color:#2b3140;">'
            f'{_esc(d["what"])}</p>'
        )

    # 生词行
    words = d.get("words") or []
    if words:
        chips = "".join(
            f'<span style="display:inline-block;margin:0 6px 6px 0;padding:3px 10px;'
            f'background:#EEF2FA;color:#2E3A78;border-radius:12px;font-size:11.5px;'
            f'font-family:-apple-system,\'Segoe UI\',\'PingFang SC\',Arial,sans-serif;">'
            f'<b>{_esc(term)}</b>：{_esc(desc)}</span>'
            for term, desc in words
        )
        words_html = f'''
              <tr>
                <td style="padding:2px 0 0 0;">
                  <div style="font-size:11px;color:#8b93a5;font-weight:700;
                              margin-bottom:6px;font-family:-apple-system,'Segoe UI','PingFang SC',Arial,sans-serif;">
                    💡 生词解释
                  </div>
                  <div>{chips}</div>
                </td>
              </tr>'''
    else:
        words_html = ""

    meta = " · ".join(p for p in (source, time_text) if p)

    return f'''
    <tr>
      <td style="padding:0 0 12px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background:#FFFFFF;border-radius:14px;">
          <tr>
            <td style="padding:17px 18px 15px 18px;">

              <!-- 标题行 -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="margin-bottom:13px;">
                <tr>
                  <td width="30" valign="top" style="padding-top:2px;">
                    <div style="width:23px;height:23px;line-height:23px;border-radius:12px;
                                background:{badge_bg};color:{badge_color};font-size:11.5px;
                                font-weight:700;text-align:center;
                                font-family:-apple-system,'Segoe UI',Arial,sans-serif;">{idx}</div>
                  </td>
                  <td valign="top">
                    <a href="{url}" target="_blank"
                       style="font-size:15.5px;line-height:1.55;color:#1f2430;font-weight:700;
                              text-decoration:none;display:block;
                              font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">{title}</a>
                  </td>
                </tr>
              </table>

              <!-- 一句话要点 -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="margin-bottom:13px;">
                <tr>
                  <td style="background:#EEF2FA;border-radius:9px;padding:9px 12px;
                             font-size:13px;line-height:1.7;color:#283593;
                             font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">
                    <span style="display:inline-block;background:#283593;color:#ffffff;
                                 font-size:10.5px;font-weight:700;border-radius:5px;
                                 padding:1px 7px;margin-right:7px;">要点</span>{_esc(digest)}
                  </td>
                </tr>
              </table>

              <!-- 新闻原文 -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                {blocks_html}
                {words_html}
              </table>

              <!-- 来源 -->
              <div style="margin-top:11px;padding-top:10px;border-top:1px solid #edf0f6;
                          font-size:11.5px;color:#8b93a5;
                          font-family:-apple-system,'Segoe UI','PingFang SC',Arial,sans-serif;">
                {meta}
              </div>

            </td>
          </tr>
        </table>
      </td>
    </tr>'''


def build_section_html(section_key: str, items: List[Dict[str, str]]) -> str:
    """渲染单个新闻板块。"""
    if not items:
        return ""

    name, sub, color, icon = SECTION_STYLE.get(section_key, SECTION_STYLE["default"])

    cards = "".join(
        _render_card(idx, item, color, section_key)
        for idx, item in enumerate(items, 1)
    )

    return f'''
    <tr>
      <td style="padding:22px 0 10px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="border-left:4px solid {color};padding-left:10px;">
              <span style="font-size:16px;font-weight:700;color:#1A237E;
                           font-family:-apple-system,'Segoe UI','PingFang SC',Arial,sans-serif;">
                {icon} {name}
              </span>
              <span style="font-size:11.5px;color:#8b93a5;margin-left:7px;
                           font-family:-apple-system,'Segoe UI','PingFang SC',Arial,sans-serif;">
                {sub}
              </span>
            </td>
            <td align="right" style="font-size:11.5px;color:#8b93a5;
                                     font-family:-apple-system,'Segoe UI','PingFang SC',Arial,sans-serif;">
              {len(items)} 条
            </td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          {cards}
        </table>
      </td>
    </tr>'''


def build_email_html(
    sections: Dict[str, List[Dict[str, str]]],
    max_items_per_section: int = 0,
) -> str:
    """
    生成完整邮件 HTML（详解版：每天 20 条，每条三段讲解）。
    """
    today = datetime.now()
    date_cn = f"{today.year}年{today.month}月{today.day}日"
    weekday = WEEKDAYS[today.weekday()]

    # 按配额挑选：配额是上限，抓不到就少发，不硬凑
    picked = {}
    for key, items in sections.items():
        quota = SECTION_QUOTA.get(key, 5)
        picked[key] = items[:quota]

    # 去掉空板块，避免出现空壳标题
    picked = {k: v for k, v in picked.items() if v}
    # 没有内容（既没 AI 说明也没原文）的条目一律不报
    picked = {k: [it for it in v if _has_content(it)] for k, v in picked.items()}
    picked = {k: v for k, v in picked.items() if v}

    total = sum(len(v) for v in picked.values())

    body_sections = "".join(
        build_section_html(key, items) for key, items in picked.items()
    )

    # 要点速览：每条一句话（邮件用表格行，兼容性最好）
    brief_rows = []
    for key, items in picked.items():
        for item in items:
            dg = _one_line(item, key)
            bt = brief_title(item.get("title", ""))
            brief_rows.append(f'''
                    <tr>
                      <td valign="top" width="18" style="font-size:12.5px;line-height:1.75;
                                 color:#8b93a5;font-weight:700;padding:3px 0;">&bull;</td>
                      <td style="font-size:12.5px;line-height:1.75;color:#1f2430;padding:3px 0;">
                        <b>{_esc(bt)}</b><br>
                        <span style="color:#5c6580;font-size:12px;">{_esc(dg)}</span>
                      </td>
                    </tr>''')
    brief_rows = "".join(brief_rows)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日新闻 · {date_cn}</title>
</head>
<body style="margin:0;padding:0;background:#EEF2F6;
             font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;">

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#EEF2F6;padding:22px 12px;">
  <tr>
    <td align="center">

      <table role="presentation" width="660" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:660px;">

        <!-- 报头 -->
        <tr>
          <td style="border-radius:14px 14px 0 0;
                     background:linear-gradient(135deg,#1A237E 0%,#283593 55%,#3949AB 100%);
                     padding:30px 26px 24px 26px;">
            <div style="font-size:11px;letter-spacing:3px;color:#9FA8DA;font-weight:600;">
              DAILY NEWS
            </div>
            <div style="font-size:26px;font-weight:800;color:#FFFFFF;margin-top:9px;">
              每日新闻
            </div>
            <div style="font-size:13.5px;color:#C5CAE9;margin-top:9px;">
              {date_cn} &nbsp;·&nbsp; {weekday}
            </div>
            <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                   style="margin-top:17px;">
              <tr>
                <td style="background:rgba(255,255,255,0.16);border-radius:20px;
                           padding:6px 15px;font-size:12.5px;color:#FFFFFF;font-weight:600;">
                  今日 {total} 条 · 一句话速览 + 完整说明
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- 要点速览 -->
        <tr>
          <td style="background:#F7F9FC;padding:16px 15px 6px 15px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#FFFFFF;border-radius:14px;">
              <tr>
                <td style="padding:17px 18px 14px 18px;">
                  <div style="font-size:15px;font-weight:700;color:#283593;margin-bottom:10px;">
                    📌 要点速览（{total} 条）
                  </div>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    {brief_rows}
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- 正文卡片 -->
        <tr>
          <td style="background:#F7F9FC;padding:10px 15px 14px 15px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              {body_sections}
            </table>
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>

</body>
</html>'''


def build_email_text(
    sections: Dict[str, List[Dict[str, str]]],
    max_items_per_section: int = 0,
) -> str:
    """生成纯文本版本（供不支持 HTML 的邮箱客户端使用）。"""
    today = datetime.now()
    lines = [
        "每日新闻",
        f"{today.year}年{today.month}月{today.day}日",
        "=" * 56,
        "",
    ]

    # 要点速览
    lines.append("📌 要点速览")
    lines.append("-" * 56)
    n = 0
    for key, items in sections.items():
        for item in items[:SECTION_QUOTA.get(key, 5)]:
            n += 1
            bt = brief_title(item.get("title", ""))
            dg = _one_line(item, key)
            lines.append(f"{n}. {bt}")
            lines.append(f"   {dg}")
    lines.append("")

    for key, items in sections.items():
        name, sub, _color, icon = SECTION_STYLE.get(key, SECTION_STYLE["default"])
        items = [it for it in items[:SECTION_QUOTA.get(key, 5)] if _has_content(it)]
        if not items:
            continue
        lines.append(f"{icon} {name}（{sub}）")
        lines.append("-" * 56)
        for idx, item in enumerate(items, 1):
            d = find_detail(item.get("title", ""))
            lines.append(f"{idx}. {item.get('title', '')}")

            ai_explain = (item.get("ai_explain") or "").strip()
            body = (item.get("body") or "").strip()

            if ai_explain:
                lines.append("   【新闻说明】")
                for p in ai_explain.replace("\n", "||").split("||"):
                    if p.strip():
                        lines.append(f"     {p.strip()}")
                if body:
                    lines.append(f"   原文出处：{item.get('source', '')}")
            elif body:
                lines.append("   【新闻原文】")
                for p in body.split("\n"):
                    if p.strip():
                        lines.append(f"     {p}")
            else:
                lines.append(f"   【新闻说明】{d['what']}")

            lines.append(f"   链接：{item.get('url', '')}")
            lines.append("")
        lines.append("")

    lines.append("=" * 56)
    return "\n".join(lines)


if __name__ == "__main__":
    import logging

    from news_fetcher import deduplicate, deduplicate_across_sections, fetch_all

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raw = fetch_all(per_source_limit=20)
    data = {k: deduplicate(v) for k, v in raw.items()}
    data = deduplicate_across_sections(data)

    html_out = build_email_html(data)
    Path("preview.html").write_text(html_out, encoding="utf-8")
    total = sum(min(len(v), SECTION_QUOTA.get(k, 5)) for k, v in data.items())
    print(f"\n预览已生成: preview.html（{total} 条，含三段详解）")
