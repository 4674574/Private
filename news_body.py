#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新闻正文抓取 —— 从新闻链接抓取「原文正文」

支持：
  - 中国新闻网  chinanews.com.cn   -> #cont_1_1_2 / .content_desc
  - 澎湃新闻    thepaper.cn        -> __NEXT_DATA__ 里的 contentDetail.content
  - 央视网      news.cctv.com      -> .content_area / #content_area
  - 通用兜底    任意站点            -> 按 <p> 段落密度挑最像正文的容器

设计要点：
  1. 逐条容错：抓不到正文不算失败，返回空串，页面/邮件自动降级
  2. 结果做本地缓存（data/cache_text.json），同一天重复运行不重复请求
  3. 正文清洗：去广告、去"责任编辑"、去分享按钮文字，只留段落
  4. 长度截断：默认最多 1200 字，避免邮件过长
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("news_body")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    requests = None
    BeautifulSoup = None

CACHE_PATH = Path(__file__).with_name("data") / "cache_text.json"

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA_DESKTOP,
    "Accept-Language": "zh-CN,zh;q=0.9",
}

MAX_CHARS = 1200          # 单条正文最多保留多少字
MIN_CHARS = 40            # 少于这个字数视为没抓到

# 需要从正文里剔除的噪声行
NOISE_PATTERNS = [
    r"^责任编辑[:：]",
    r"^来源[:：]",
    r"^作者[:：]",
    r"^编辑[:：]",
    r"^分享到[:：]?$",
    r"^大字体$", r"^小字体$",
    r"^扫一扫在手机打开",
    r"^点击进入专题",
    r"^原标题[:：]",
    r"^【?编辑[:：]",
    r"^返回顶部$",
    r"^上一篇", r"^下一篇",
    r"^相关新闻", r"^相关推荐",
    r"^免责声明",
    r"^版权声明",
    r"^[（(]?完[)）]?$",
    r"^更多精彩内容",
    r"^下载.*客户端$",
    r"^关注.*公众号$",
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$",
    r"^来源：\S+$",
    r"^发布于$", r"^问AI$", r"^关注$", r"^收藏$", r"^分享$",
    r"^\S{2,6}官方账号$", r"^\S{2,8}(报|日报|晚报|新闻|网|社)$",
]


# ============================================================
#  缓存
# ============================================================
def _load_cache() -> Dict[str, str]:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_cache(cache: Dict[str, str]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 只保留最近 800 条，避免无限膨胀
        if len(cache) > 800:
            cache = dict(list(cache.items())[-800:])
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("写缓存失败: %s", exc)


# ============================================================
#  清洗
# ============================================================
# 搜索建议词/分页导航的特征串
TAIL_NOISE_PATTERNS = [
    r"下一页", r"上一页", r"^\d+\s*$", r"清风剑", r"^#.*#",
    r"^\d+月\d+日[，,]?\s*连打", r"^一日两通报",
]


# 零宽字符：百度资讯卡片拿它当段落分隔符
ZW_MARKS = ("\u200b", "\u200c", "\u200d", "\ufeff")


def _first_zw(text: str) -> int:
    """返回第一个零宽字符的位置，没有则返回 -1。"""
    for mark in ZW_MARKS:
        p = text.find(mark)
        if p >= 0:
            return p
    return -1


def _trim_tail_noise(text: str) -> str:
    """
    砍掉正文末尾的搜索建议词。

    例：「…移送检察机关。」后面跟着
        「‌‌ 1 一日两通报! 中纪委连打 两“虎” 中纪委连打三“虎”… 下一页」
    这些不是新闻内容，留着会污染后面交给 AI 的材料。
    """
    if not text:
        return text

    # 找第一条明显是建议词的行，从那里整段砍掉
    lines = text.split("\n")
    cut_at = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if len(s) < 4:
            continue
        # 建议词行的普遍特征：短、含省略号或「_网」、或命中特征串
        # 只有「整行都像建议词」才整行砍；正文里出现省略号不算
        if len(s) < 160 and (s.count("...") or s.count("…") or "_网" in s):
            if len(lines) > 1:      # 多行时才按行砍，单行交给下面的零宽字符逻辑
                cut_at = i
                break
        if any(re.search(p, s) for p in TAIL_NOISE_PATTERNS):
            if len(lines) > 1:
                cut_at = i
                break

    kept = "\n".join(lines[:cut_at]).strip()

    # 百度资讯卡片用零宽字符分隔「正文 / 相关搜索」，第一个零宽字符之后全是垃圾
    pos = _first_zw(kept)
    if pos > 0:
        kept = kept[:pos]
    tail = kept.find("下一页")
    if tail > 0 and tail > len(kept) * 0.5:
        kept = kept[:tail]
    kept = kept.rstrip(" \u3000,，.。、")

    return kept or text


def _clean_text(raw: str) -> str:
    """把粗糙的正文清洗成干净段落，并截断长度。"""
    if not raw:
        return ""

    # 去 HTML 标签（澎湃 content 是富文本）
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)

    # 反转义
    for a, b in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&ldquo;", "“"), ("&rdquo;", "”")]:
        text = text.replace(a, b)

    lines = []
    for line in text.split("\n"):
        s = line.strip()
        s = re.sub(r"\s+", " ", s)
        if not s or len(s) <= 2:
            continue
        if any(re.search(p, s) for p in NOISE_PATTERNS):
            continue
        lines.append(s)

    body = "\n".join(lines).strip()

    # 截掉搜索建议词尾巴：百度热搜页正文后面常跟一串相关搜索
    body = _trim_tail_noise(body)

    if len(body) > MAX_CHARS:
        # 尽量在句号处截断，读起来完整
        cut = body[:MAX_CHARS]
        for mark in ["。", "！", "？", "…"]:
            pos = cut.rfind(mark)
            if pos > MAX_CHARS * 0.6:
                cut = cut[: pos + 1]
                break
        body = cut + "…"

    return body


# ============================================================
#  各站点解析
# ============================================================
def _from_chinanews(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in ["#cont_1_1_2", ".content_desc", ".left_cont", ".content"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text("\n", strip=True)
            if len(t) >= MIN_CHARS:
                return t
    return ""


def _from_thepaper(html: str) -> str:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S,
    )
    if not m:
        return ""
    try:
        data = json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return ""

    found = {"main": "", "summary": ""}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "content" and isinstance(v, str) and len(v) > len(found["main"]):
                    found["main"] = v
                elif k == "summary" and isinstance(v, str) and len(v) > len(found["summary"]):
                    found["summary"] = v
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)

    main = _clean_text(found["main"])
    summ = _clean_text(found["summary"])
    if len(main) >= MIN_CHARS:
        # summary 是导语，放最前面
        if summ and summ not in main:
            return f"{summ}\n{main}"
        return main
    return summ if len(summ) >= MIN_CHARS else found["main"]


def _from_cctv(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in [".text_area", "#content_area", ".content_area", ".cnt_bd", ".content"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text("\n", strip=True)
            if len(t) >= MIN_CHARS:
                return t
    return ""


def _from_generic(html: str) -> str:
    """通用兜底：找出 <p> 段落文字最多的容器。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    best_text, best_len = "", 0
    for el in soup.find_all(["article", "div", "section"]):
        ps = el.find_all("p", recursive=False) or el.find_all("p")
        if len(ps) < 3:
            continue
        t = "\n".join(p.get_text(" ", strip=True) for p in ps)
        t = re.sub(r"\s+", " ", t)
        if len(t) > best_len:
            best_text, best_len = t, len(t)

    if best_len >= MIN_CHARS:
        return best_text

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"]
    return ""


SEARCH_URL_PATTERNS = (
    r"baidu\.com/s\?", r"sogou\.com/web", r"bing\.com/search",
    r"so\.com/s\?", r"google\.com/search", r"search\.yahoo",
    r"s\.weibo\.com", r"toutiao\.com/search",
)


def _is_search_page(url: str) -> bool:
    """
    判断链接是否为搜索引擎结果页。

    例外：百度热搜榜的 m.baidu.com/s?...&sa=fyb_news 虽然长得像搜索页，
    但它是百度自己排好版的资讯卡片页，里面是正常的新闻正文，要放行。
    """
    low = (url or "").lower()
    if "sa=fyb_news" in low or "sa=re_dqa" in low:
        return False
    return any(re.search(pat, low) for pat in SEARCH_URL_PATTERNS)


def _looks_like_garbage(text: str) -> bool:
    """
    粗筛「不像新闻正文」的文本。

    典型垃圾：搜索建议词列表（大量引号、省略号、重复短句）、导航文字。
    """
    if not text:
        return True
    # 省略号/站名后缀堆砌：典型搜索建议词列表
    if (text.count("...") + text.count("…") + text.count("_网")) > 4:
        return True
    # 全篇没有几个句号，却塞满引号和冒号 —— 搜索建议词的特征
    stops = text.count("。") + text.count("！") + text.count("？")
    if stops <= 2:
        marks = text.count("“") + text.count("”") + text.count(":") + text.count("：")
        if marks >= 6:
            return True
    # 碎句过多：按句号/感叹号切分后，平均每句不到 12 字，说明是词条罗列
    chunks = [c for c in re.split(r"[。！？!?\n]", text) if c.strip()]
    if len(chunks) >= 6 and sum(len(c) for c in chunks) / len(chunks) < 12:
        return True
    return False


def fetch_body(url: str, source_key: str = "", timeout: int = 12,
               use_cache: bool = True) -> str:
    """
    抓取单条新闻的正文原文。

    返回清洗后的正文（可能为空字符串——表示没抓到，调用方需降级处理）。
    """
    if not url or requests is None:
        return ""

    # 搜索引擎结果页不是新闻正文，直接放弃（交给标题反查）
    if _is_search_page(url):
        return ""

    cache = _load_cache() if use_cache else {}
    if url in cache:
        return cache[url]

    html = ""
    for headers in (HEADERS, {**HEADERS, "User-Agent": UA_MOBILE}):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            # 编码纠正
            if resp.encoding in (None, "ISO-8859-1"):
                resp.encoding = resp.apparent_encoding or "utf-8"
            html = resp.text
            break
        except Exception as exc:  # noqa: BLE001
            logger.debug("抓正文失败(%s): %s", url[:60], exc)

    if not html:
        return ""

    # 按站点选解析器
    if "chinanews.com" in url:
        raw = _from_chinanews(html)
    elif "thepaper.cn" in url:
        raw = _from_thepaper(html)
    elif "cctv.com" in url:
        raw = _from_cctv(html)
    else:
        raw = ""
        for fn in (_from_chinanews, _from_thepaper, _from_cctv, _from_generic):
            raw = fn(html)
            if raw:
                break

    body = _clean_text(raw)
    if len(body) < MIN_CHARS or _looks_like_garbage(body):
        body = ""

    if use_cache and body:
        cache[url] = body
        _save_cache(cache)

    return body


def fetch_bodies(items, source_key: str = "", timeout: int = 12,
                 use_cache: bool = True, retry_search: bool = True) -> int:
    """
    给一批新闻项就地补上 body 字段。返回成功条数。

    retry_search=True 时，直接抓失败的条目会按标题去搜索引擎反查一篇报道再抓，
    这样百度热搜这种「链接本身是搜索页」的条目也能拿到正文。
    """
    ok = 0
    for it in items:
        try:
            body = fetch_body(it.get("url", ""), source_key, timeout, use_cache)
        except Exception as exc:  # noqa: BLE001
            logger.debug("fetch_body 异常: %s", exc)
            body = ""

        # 直接抓失败 → 按标题反查一篇可读报道
        if not body and retry_search:
            try:
                from news_search import find_article
                new_url = find_article(it.get("title", ""), timeout)
                if new_url:
                    body = fetch_body(new_url, "", timeout, use_cache)
                    if body:
                        it["found_url"] = new_url
                        logger.debug("反查成功: %s -> %s",
                                     it.get("title", "")[:16], new_url[:50])
            except Exception as exc:  # noqa: BLE001
                logger.debug("反查异常: %s", exc)

        it["body"] = body
        if body:
            ok += 1
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    tests = [
        ("https://www.chinanews.com.cn/cj/2026/10-14/10714133.shtml", "chinanews"),
        ("https://www.thepaper.cn/newsDetail_forward_34246694", "thepaper_world"),
        ("https://news.cctv.com/2026/10/13/ARTI2KuI4fIsTdJoNetx1vm8261013.shtml", "thepaper"),
    ]
    for u, k in tests:
        b = fetch_body(u, k)
        print(f"\n{'=' * 60}\n{u}\n{'=' * 60}")
        print(f"长度 {len(b)}")
        print(b[:400] if b else "（未抓到）")
