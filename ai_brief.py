#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 新闻简报生成 —— 把新闻原文交给大模型，写成一段完整说明

设计要点：
  1. 兼容 OpenAI 风格的接口（DeepSeek、通义、Kimi、智谱都支持）
  2. 没有配置密钥时自动降级为「只用原文」，不影响推送
  3. 每条新闻生成两样东西：
        one_line  一句话说明（用于顶部速览）
        explain   完整说明（用于正文，150-250字，讲清前因后果）
  4. 失败重试 + 超时保护，绝不拖垮整体流程
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

logger = logging.getLogger("ai_brief")

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


# ============================================================
#  配置
# ============================================================
DEFAULT_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "moonshot": "https://api.moonshot.cn/v1/chat/completions",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
}

DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "moonshot": "moonshot-v1-8k",
    "dashscope": "qwen-plus",
    "zhipu": "glm-4-flash",
}


class AIConfig:
    """从环境变量读取 AI 配置。"""

    def __init__(self) -> None:
        self.api_key = (os.environ.get("AI_API_KEY") or "").strip()
        provider = (os.environ.get("AI_PROVIDER") or "deepseek").strip().lower()
        self.provider = provider
        self.endpoint = (os.environ.get("AI_ENDPOINT") or
                         DEFAULT_ENDPOINTS.get(provider, DEFAULT_ENDPOINTS["deepseek"]))
        self.model = (os.environ.get("AI_MODEL") or
                      DEFAULT_MODELS.get(provider, "deepseek-chat"))
        self.timeout = int(os.environ.get("AI_TIMEOUT") or 60)
        # 是否关闭思考模式（省钱关键）。默认关闭思考。
        self.disable_thinking = (os.environ.get("AI_THINKING") or "").strip().lower() \
            not in ("1", "true", "on", "yes")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and requests is not None


# ============================================================
#  提示词
# ============================================================
SYSTEM_PROMPT = """你是一位给中学生讲新闻的老师。你的任务是读懂新闻，然后用简单、准确、有信息量的话讲清楚。

严格要求：
1. 只说新闻里真实存在的事实，绝对不许编造数字、人名、时间、地点。
2. 如果材料里信息不足，就只说能确定的部分，不要硬凑。
3. 语言要通俗，像跟朋友聊天，不要用官话套话。
4. 不要写"这体现了""充分说明了"这类空话。
5. 严格按指定格式输出，不要加任何多余的话。"""

USER_TEMPLATE = """请阅读下面这条新闻，输出两段内容。

【新闻标题】{title}
【新闻来源】{source}
【新闻正文】
{body}

请按以下格式输出（不要用markdown，不要加序号，不要加解释）：

一句话：<用一句话说清这条新闻讲了什么事，30-45个字，必须包含主体和结果>
说明：<150到250字，把这件事讲清楚。要包含：事情的具体经过、关键细节（数字/地点/人物/时间）、这件事和普通人有什么关系或影响。分2到3段来写，段落之间用 || 分隔，不要用换行>"""
EXAMPLE = """示例输出：
一句话：第140届广交会开幕，2.5万家企业参展，境外采购商到会人数创历史新高
说明：第140届中国进出口商品交易会10月15日在广州开幕，展期持续至11月4日，分三期举办，分别聚焦家电电子、日用消费品和纺织服装。本届广交会共有约2.5万家企业参展，其中首次参展企业超过3000家。 || 从预登记情况看，来自全球200多个国家和地区的境外采购商到会人数有望超过上届，欧美采购商占比回升明显。多家参展企业表示，本届展会接到的询盘量比去年同期增长。 || 广交会被看作中国外贸的"晴雨表"，它的成交情况能反映接下来半年的出口走势，也和不少外贸工厂的订单、用工直接相关。"""


# ============================================================
#  调用
# ============================================================
def _call_api(cfg: AIConfig, messages: List[Dict[str, str]],
              max_retries: int = 2) -> Optional[str]:
    """调用大模型，失败重试。返回文本或 None。"""
    if not cfg.enabled:
        return None

    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 800,
        "stream": False,
    }
    # DeepSeek 新模型默认开「思考模式」，会先生成一大段推理再给答案，
    # 推理部分同样按输出计费，成本会翻好几倍。这里明确关掉。
    if cfg.disable_thinking:
        payload["thinking"] = {"type": "disabled"}
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                cfg.endpoint, headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=cfg.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            # 限流与服务器错误重试
            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning("AI 接口返回 %s，第 %d 次重试", resp.status_code, attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue
            logger.error("AI 接口错误 %s: %s", resp.status_code, resp.text[:200])
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 调用异常（第 %d 次）: %s", attempt + 1, exc)
            time.sleep(2 * (attempt + 1))

    return None


def _parse_output(text: str) -> Dict[str, str]:
    """从模型输出里抠出「一句话」和「说明」。"""
    if not text:
        return {}

    text = text.strip()

    one_line = ""
    explain = ""

    m1 = re.search(r"一句话[：:]\s*(.+)", text)
    if m1:
        one_line = m1.group(1).strip().split("\n")[0].strip()

    m2 = re.search(r"说明[：:]\s*([\s\S]+)$", text)
    if m2:
        explain = m2.group(1).strip()

    # 兜底：如果模型没按格式，就把第一行当一句话，其余当说明
    if not one_line and not explain and text:
        lines = [x.strip() for x in text.split("\n") if x.strip()]
        if lines:
            one_line = lines[0].lstrip("-•* ").strip()
            explain = " ".join(lines[1:]).strip()

    # 清洗
    one_line = re.sub(r"^[\-\*\d\.、）\)]+", "", one_line).strip()
    explain = re.sub(r"^[\-\*\d\.、）\)]+", "", explain).strip()

    # 长度保护：一句话不超过 60 字
    if len(one_line) > 60:
        cut = one_line[:60]
        for mark in ["，", "。", "；"]:
            pos = cut.rfind(mark)
            if pos > 25:
                cut = cut[:pos]
                break
        one_line = cut

    return {"one_line": one_line, "explain": explain}


def generate_brief(title: str, body: str, source: str = "",
                   cfg: AIConfig | None = None) -> Dict[str, str]:
    """
    为一条新闻生成「一句话 + 说明」。

    返回 {"one_line": ..., "explain": ...}，失败时返回空 dict。
    """
    cfg = cfg or AIConfig()
    if not cfg.enabled:
        return {}

    body = (body or "").strip()
    if not body:
        # 没有原文时，只能让模型凭标题写，容易编造，所以直接跳过
        return {}

    # 正文太长会浪费 token，截断到 1500 字
    if len(body) > 1500:
        body = body[:1500] + "…"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(
            title=title, source=source or "未知", body=body)},
        {"role": "assistant", "content": EXAMPLE},
        {"role": "user", "content": "好的，现在请按格式输出上面这条新闻的两段内容。"},
    ]

    raw = _call_api(cfg, messages)
    if not raw:
        return {}

    result = _parse_output(raw)
    if not result.get("explain"):
        return {}

    return result


def enrich_items(items: List[Dict[str, str]], source_key: str = "",
                 cfg: AIConfig | None = None,
                 delay: float = 0.3) -> Dict[str, int]:
    """
    给一批新闻项就地补上 ai_one_line / ai_explain 字段。

    返回统计：{"ok": 成功数, "skip": 跳过数, "fail": 失败数}
    """
    cfg = cfg or AIConfig()
    stat = {"ok": 0, "skip": 0, "fail": 0}

    if not cfg.enabled:
        logger.info("未配置 AI 密钥，跳过 AI 生成（仅用原文）")
        return stat

    for it in items:
        title = it.get("title", "")
        body = it.get("body", "")
        if not body:
            stat["skip"] += 1
            continue
        try:
            res = generate_brief(title, body, it.get("source", ""), cfg)
            if res:
                it["ai_one_line"] = res["one_line"]
                it["ai_explain"] = res["explain"]
                stat["ok"] += 1
            else:
                stat["fail"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 生成失败: %s (%s)", title[:20], exc)
            stat["fail"] += 1
        time.sleep(delay)

    return stat


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = AIConfig()
    print("AI 已启用:", cfg.enabled)
    print("服务商:", cfg.provider, "| 模型:", cfg.model)
    print("接口:", cfg.endpoint)
    if not cfg.enabled:
        print("\n请先设置环境变量 AI_API_KEY")
