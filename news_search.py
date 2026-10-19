#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按标题反查原文 —— 给抓不到正文的新闻找一条真正能读的报道"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("news_search")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 搜索页的跳转域名（要拆出真实地址）
REDIRECT_HOSTS = ("bing.com/ck/a", "sogou.com/link", "baidu.com/link")

# 不要的站点：聚合洗稿、短视频、问答
BLOCK = [
    "toutiao.com", "baijiahao.baidu.com", "mbd.baidu.com", "zhihu.com",
    "weibo.com", "douyin.com", "kuaishou.com", "bilibili.com",
    "sohu.com/a/", "hao.360.cn", "sogou.com", "bing.com",
    "xiaohongshu.com", "163.com/dy/article",
]


def _norm(t: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", t or "")


def _unwrap(url: str) -> str:
    """把搜索跳转链拆成真实地址。"""
    if not url:
        return ""
    if "bing.com/ck/a" in url:
        q = parse_qs(urlparse(url).query)
        raw = (q.get("u") or [""])[0]
        if raw.startswith("a1"):
            raw = raw[2:]
        return unquote(raw)
    q = parse_qs(urlparse(url).query)
    for k in ("url", "target"):
        if q.get(k):
            return unquote(q[k][0])
    return url


def search_bing(title: str, timeout: int = 10) -> Optional[str]:
    """Bing 搜索，按标题找一条正规媒体的报道链接。"""
    try:
        resp = requests.get("https://cn.bing.com/search",
                            params={"q": title, "setlang": "zh-CN"},
                            headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as exc:  # noqa: BLE001
        logger.debug("Bing 搜索失败: %s", exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    key = _norm(title)[:10]

    cands = []
    for li in soup.select("li.b_algo"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = _unwrap(a["href"])
        if not href.startswith("http"):
            continue
        if any(b in href for b in BLOCK):
            continue
        text = _norm(a.get_text(" ", strip=True))
        cands.append((href, text))

    if not cands:
        return None
    # 优先选标题能对上的，否则取第一条正规媒体
    for href, text in cands:
        if key and (key[:6] in text or text[:6] in key):
            return href
    return cands[0][0]


def find_article(title: str, timeout: int = 10) -> Optional[str]:
    """按标题找一条可读的报道链接，找不到返回 None。"""
    if not title or len(title.strip()) < 6:
        return None
    try:
        return search_bing(title, timeout)
    except Exception as exc:  # noqa: BLE001
        logger.debug("搜索异常: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for t in ["大额存单重出江湖", "汉庭快捷“欢聚六人房”走红", "六张网布局里的中国之治"]:
        print(t, "->", find_article(t))
