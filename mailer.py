#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mailer.py — 邮件发送模块

负责通过 SMTP 把生成的 HTML 邮件发送出去。
支持 SSL(465) 与 STARTTLS(587) 两种加密方式。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from typing import Dict, List

logger = logging.getLogger(__name__)


class MailSendError(Exception):
    """邮件发送失败。"""


def send_mail(
    smtp_server: str,
    smtp_port: int,
    sender: str,
    password: str,
    recipients: List[str],
    subject: str,
    html_body: str,
    text_body: str = "",
    use_ssl: bool = True,
    sender_name: str = "每日新闻",
) -> None:
    """
    发送一封 HTML 邮件。

    抛出 MailSendError 表示发送失败（含详细的错误原因）。
    """
    if not recipients:
        raise MailSendError("收件人列表为空，请检查 config.yaml 中的 recipients")

    # 构建 MIME 多部分邮件（HTML + 纯文本备选）
    message = MIMEMultipart("alternative")
    message["From"] = formataddr((str(Header(sender_name, "utf-8")), sender))
    message["To"] = ", ".join(recipients)
    message["Subject"] = Header(subject, "utf-8")
    message["Date"] = formatdate(localtime=True)

    # 纯文本在前、HTML 在后（客户端优先显示后添加的部分）
    if text_body:
        message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_server, smtp_port,
                                  context=context, timeout=30) as server:
                server.login(sender, password)
                server.sendmail(sender, recipients, message.as_string())
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(sender, password)
                server.sendmail(sender, recipients, message.as_string())

        logger.info("邮件已发送至: %s", ", ".join(recipients))

    except smtplib.SMTPAuthenticationError as exc:
        raise MailSendError(
            "SMTP 认证失败。请检查：\n"
            "  1) 邮箱地址是否正确\n"
            "  2) 使用的是否为「授权码」而非登录密码（最常见原因）\n"
            "  3) 是否已在邮箱设置中开启 SMTP 服务\n"
            f"原始错误: {exc}"
        ) from exc
    except smtplib.SMTPConnectError as exc:
        raise MailSendError(
            f"无法连接 SMTP 服务器 {smtp_server}:{smtp_port}。\n"
            f"请检查服务器地址、端口，以及网络/防火墙设置。\n原始错误: {exc}"
        ) from exc
    except smtplib.SMTPException as exc:
        raise MailSendError(f"SMTP 发送失败: {exc}") from exc
    except OSError as exc:
        raise MailSendError(
            f"网络错误，无法连接 {smtp_server}:{smtp_port}: {exc}"
        ) from exc


# 常见邮箱服务商的 SMTP 配置预设
SMTP_PRESETS: Dict[str, Dict[str, object]] = {
    "qq.com":    {"server": "smtp.qq.com",        "port": 465, "ssl": True},
    "163.com":   {"server": "smtp.163.com",       "port": 465, "ssl": True},
    "126.com":   {"server": "smtp.126.com",       "port": 465, "ssl": True},
    "sina.com":  {"server": "smtp.sina.com",      "port": 465, "ssl": True},
    "outlook.com": {"server": "smtp.office365.com", "port": 587, "ssl": False},
    "hotmail.com": {"server": "smtp.office365.com", "port": 587, "ssl": False},
    "gmail.com": {"server": "smtp.gmail.com",     "port": 587, "ssl": False},
    "foxmail.com": {"server": "smtp.qq.com",      "port": 465, "ssl": True},
    "aliyun.com": {"server": "smtp.aliyun.com",   "port": 465, "ssl": True},
    "139.com":   {"server": "smtp.139.com",       "port": 465, "ssl": True},
}


def detect_smtp_config(email_address: str) -> Dict[str, object] | None:
    """根据邮箱后缀自动推断 SMTP 配置。"""
    if "@" not in email_address:
        return None
    domain = email_address.rsplit("@", 1)[-1].lower()
    return SMTP_PRESETS.get(domain)
