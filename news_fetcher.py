#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_fetcher.py — 多源新闻抓取模块

负责从多个公开数据源抓取当日热点新闻，统一清洗为标准化结构。

标准新闻结构:
    {
        "title":  "标题",
        "url":    "原文链接",
        "source": "来源名称",
        "time":   "时间描述，如 3小时前",
    }
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List

import requests

logger = logging.getLogger(__name__)

# 统一的请求头，模拟正常浏览器
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 需要过滤掉的非新闻类条目（广告、导航、栏目名等）
NOISE_PATTERNS = [
    r"^查看更多",
    r"^点击查看",
    r"^广告",
    r"^下载.*客户端",
    r"^登录$",
    r"^注册$",
    r"^首页$",
]


def _is_noise(title: str) -> bool:
    """判断标题是否为噪声内容。"""
    if not title or len(title.strip()) < 6:
        return True
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, title.strip()):
            return True
    return False


def _clean_title(title: str) -> str:
    """清洗标题：去除多余空白、HTML 标签、末尾来源标注。"""
    title = re.sub(r"<[^>]+>", "", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip()
    # 去掉结尾的 " | 来源"、" - 来源" 等
    title = re.sub(r"\s*[|｜\-–]\s*[^|｜\-–]{2,10}$", "", title)
    return title.strip()


def _get(url: str, timeout: int = 15, retry: int = 2) -> requests.Response | None:
    """带重试的 GET 请求。"""
    for attempt in range(retry + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt < retry:
                logger.debug("请求失败，重试 %s/%s: %s", attempt + 1, retry, exc)
                time.sleep(1.5 * (attempt + 1))
            else:
                logger.warning("请求最终失败: %s (%s)", url, exc)
    return None


# ============================================================
#  数据源 1：百度热搜榜
# ============================================================
def fetch_baidu(timeout: int = 15, retry: int = 2, limit: int = 30) -> List[Dict[str, str]]:
    """抓取百度实时热搜榜。"""
    url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
    resp = _get(url, timeout, retry)
    if resp is None:
        return []

    try:
        data = resp.json()
        # 结构: data.cards[0].content[0].content = [ {...}, ... ]
        cards = data.get("data", {}).get("cards", [])
        items: List[Dict[str, Any]] = []
        for card in cards:
            for block in card.get("content", []):
                inner = block.get("content")
                if isinstance(inner, list) and inner:
                    items.extend(inner)
    except (ValueError, AttributeError, KeyError) as exc:
        logger.warning("百度热搜解析失败: %s", exc)
        return []

    results: List[Dict[str, str]] = []
    for idx, item in enumerate(items[:limit], 1):
        word = item.get("word") or item.get("query") or ""
        title = _clean_title(str(word))
        if _is_noise(title):
            continue
        link = item.get("url") or ""
        # 补全相对链接
        if link.startswith("/"):
            link = "https://top.baidu.com" + link
        # 热度格式化：优先用官方标签名（热/爆/新），否则不显示
        hot_tag = str(item.get("hotTag") or "").strip()
        tag_name = str(item.get("newHotName") or "").strip()
        tag_map = {"1": "热", "2": "爆", "3": "新"}
        if tag_name and tag_name != "None":
            hot_text = tag_name
        elif hot_tag in tag_map:
            hot_text = tag_map[hot_tag]
        else:
            hot_text = ""
        results.append({
            "title": title,
            "url": link or f"https://www.baidu.com/s?wd={requests.utils.quote(title)}",
            "source": "百度热搜",
            "time": hot_text,
            "rank": idx,
        })
    return results


# ============================================================
#  数据源 2：澎湃新闻（Next.js 内嵌 JSON）
# ============================================================
def fetch_thepaper(timeout: int = 15, retry: int = 2, limit: int = 30) -> List[Dict[str, str]]:
    """抓取澎湃新闻首页推荐 + 要闻。"""
    url = "https://www.thepaper.cn/"
    resp = _get(url, timeout, retry)
    if resp is None:
        return []

    try:
        html = resp.text
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        if not match:
            logger.warning("澎湃新闻：未找到 __NEXT_DATA__")
            return []
        payload = json.loads(match.group(1))
        page_data = payload.get("props", {}).get("pageProps", {}).get("data", {})
    except (ValueError, AttributeError) as exc:
        logger.warning("澎湃新闻解析失败: %s", exc)
        return []

    # 递归收集所有带 contId/name 的新闻节点
    collected: List[Dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "contId" in node and node.get("name"):
                collected.append(node)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(page_data)

    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for node in collected:
        title = _clean_title(str(node.get("name", "")))
        if _is_noise(title) or title in seen:
            continue
        seen.add(title)

        cont_id = node.get("contId")
        link = node.get("link") or ""
        if not link and cont_id:
            link = f"https://www.thepaper.cn/newsDetail_forward_{cont_id}"

        results.append({
            "title": title,
            "url": link,
            "source": "澎湃新闻",
            "time": str(node.get("pubTimeNew") or node.get("pubTime") or ""),
        })
        if len(results) >= limit:
            break
    return results


# ============================================================
#  公共：RSS 解析
# ============================================================
def _parse_rss(xml_text: str, source_name: str, limit: int) -> List[Dict[str, str]]:
    """从标准 RSS XML 中解析新闻条目。"""
    # 去掉头部注释，避免注释内容干扰
    body = xml_text.split("-->", 1)[-1] if "-->" in xml_text else xml_text
    items = re.findall(r"<item>(.*?)</item>", body, re.S)
    results: List[Dict[str, str]] = []

    for raw in items[:limit]:
        def _pick(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", raw, re.S)
            if not m:
                return ""
            value = m.group(1).strip()
            value = re.sub(r"^\s*<!\[CDATA\[", "", value)
            value = re.sub(r"\]\]>\s*$", "", value)
            return value.strip()

        title = _clean_title(_pick("title"))
        if _is_noise(title):
            continue

        link = _pick("link")
        if link.startswith("//"):
            link = "http:" + link

        pub_date = _pick("pubDate")
        # 把 RFC822 日期转成友好的中文时间描述
        friendly_time = pub_date
        m = re.search(r"(\d{1,2})\s+(\w{3})\s+(\d{4})\s+(\d{2}:\d{2})", pub_date)
        if m:
            day, mon, year, hhmm = m.groups()
            months = {
                "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
                "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
                "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
            }
            friendly_time = f"{year}-{months.get(mon, '01')}-{int(day):02d} {hhmm}"

        results.append({
            "title": title,
            "url": link,
            "source": source_name,
            "time": friendly_time,
        })
    return results


# ============================================================
#  数据源 3：中国新闻网（实时滚动新闻 RSS）
# ============================================================
def fetch_chinanews(timeout: int = 15, retry: int = 2, limit: int = 30) -> List[Dict[str, str]]:
    """抓取中国新闻网滚动新闻 RSS。"""
    url = "http://www.chinanews.com.cn/rss/scroll-news.xml"
    resp = _get(url, timeout, retry)
    if resp is None:
        return []
    resp.encoding = "utf-8"
    return _parse_rss(resp.text, "中国新闻网", limit)


# ============================================================
#  数据源 4：澎湃新闻 - 国际频道
# ============================================================
def fetch_thepaper_world(timeout: int = 15, retry: int = 2, limit: int = 30) -> List[Dict[str, str]]:
    """抓取澎湃新闻国际频道列表。"""
    url = "https://www.thepaper.cn/channel_122908"
    resp = _get(url, timeout, retry)
    if resp is None:
        return []

    try:
        html = resp.text
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        if not match:
            return []
        payload = json.loads(match.group(1))
        page_data = payload.get("props", {}).get("pageProps", {}).get("data", {})
    except (ValueError, AttributeError) as exc:
        logger.warning("澎湃国际频道解析失败: %s", exc)
        return []

    collected: List[Dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "contId" in node and node.get("name"):
                collected.append(node)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(page_data)

    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for node in collected:
        title = _clean_title(str(node.get("name", "")))
        if _is_noise(title) or title in seen:
            continue
        seen.add(title)
        cont_id = node.get("contId")
        link = node.get("link") or (
            f"https://www.thepaper.cn/newsDetail_forward_{cont_id}" if cont_id else ""
        )
        results.append({
            "title": title,
            "url": link,
            "source": "澎湃·国际",
            "time": str(node.get("pubTimeNew") or node.get("pubTime") or ""),
        })
        if len(results) >= limit:
            break
    return results


# ============================================================
#  数据源注册表
# ============================================================
SOURCE_REGISTRY: Dict[str, Callable[..., List[Dict[str, str]]]] = {
    "baidu": fetch_baidu,
    "thepaper": fetch_thepaper,
    "chinanews": fetch_chinanews,
    "thepaper_world": fetch_thepaper_world,
}


def fetch_all(
    sources: List[str] | None = None,
    timeout: int = 15,
    retry: int = 2,
    per_source_limit: int = 30,
) -> Dict[str, List[Dict[str, str]]]:
    """
    抓取所有指定数据源。

    返回: { 数据源名称: [新闻项, ...] }
    每个数据源独立容错，单个源失败不影响其他源。
    """
    sources = sources or list(SOURCE_REGISTRY.keys())
    result: Dict[str, List[Dict[str, str]]] = {}

    for name in sources:
        fetcher = SOURCE_REGISTRY.get(name)
        if fetcher is None:
            logger.warning("未知数据源，已跳过: %s", name)
            continue
        logger.info("正在抓取: %s ...", name)
        try:
            items = fetcher(timeout=timeout, retry=retry, limit=per_source_limit)
        except Exception as exc:  # noqa: BLE001
            logger.error("数据源 %s 抓取异常: %s", name, exc)
            items = []
        logger.info("  %s 获取到 %d 条", name, len(items))
        result[name] = items

    return result


def _title_fingerprint(title: str, size: int = 12) -> str:
    """
    把标题压成指纹，用于识别"同一条新闻的换皮写法"。

    先剥掉常见的前缀噪声（视频丨、直播回放、独家等），
    再去掉所有标点空白，取前 size 个字符。
    """
    text = title or ""
    # 剥掉前缀噪声（可能叠加多层）
    text = re.sub(r"^(视频丨|视频\||视频[:：]|直播回放[:：]?|独家[:：]|快讯[:：]|图集丨|组图丨)+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)
    return text[:size]


def _is_similar_title(fp: str, seen_list: List[str], short_len: int = 10) -> bool:
    """
    指纹相似判定，命中任意一条即视为同一条新闻：
    1) 完全相同；
    2) 一方是另一方的前缀（长标题换皮）；
    3) 前 short_len 字符一致（例如"习近平会见多哥…"两种写法）。
    """
    if not fp:
        return True
    for seen in seen_list:
        if fp == seen:
            return True
        if fp.startswith(seen) or seen.startswith(fp):
            return True
        if len(fp) >= short_len and len(seen) >= short_len and fp[:short_len] == seen[:short_len]:
            return True
    return False


def deduplicate(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """按标题相似度去重（单组内去重）。"""
    seen_titles: List[str] = []
    unique: List[Dict[str, str]] = []

    for item in items:
        fingerprint = _title_fingerprint(item.get("title", ""))
        if _is_similar_title(fingerprint, seen_titles):
            continue
        seen_titles.append(fingerprint)
        unique.append(item)

    return unique


# 板块优先级：排在前的板块优先保留新闻（前端展示顺序）
SECTION_PRIORITY = ["baidu", "thepaper", "chinanews", "thepaper_world"]


def deduplicate_across_sections(
    sections: Dict[str, List[Dict[str, str]]],
    priority: List[str] | None = None,
) -> Dict[str, List[Dict[str, str]]]:
    """
    全局去重：同一条新闻在整封邮件/整个页面里只出现一次。

    先按板块优先级依次处理，每条新闻一旦被某个板块选中，
    就从后面的板块里剔除（避免"百度热搜"和"澎湃新闻"重复讲同一件事）。
    """
    order = priority or SECTION_PRIORITY
    ordered_keys = [k for k in order if k in sections]
    ordered_keys += [k for k in sections if k not in ordered_keys]

    seen: List[str] = []
    result: Dict[str, List[Dict[str, str]]] = {}

    for key in ordered_keys:
        kept: List[Dict[str, str]] = []
        for item in sections.get(key, []):
            fp = _title_fingerprint(item.get("title", ""))
            if _is_similar_title(fp, seen):
                continue
            seen.append(fp)
            kept.append(item)
        result[key] = kept

    # 按原始顺序返回，保证展示顺序稳定
    return {k: result.get(k, []) for k in sections}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    data = fetch_all()
    for src, items in data.items():
        print(f"\n{'=' * 50}\n【{src}】共 {len(items)} 条\n{'=' * 50}")
        for i, it in enumerate(items[:8], 1):
            print(f"{i:2d}. {it['title']}")
            print(f"    {it['url'][:80]}")
