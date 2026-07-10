# -*- coding: utf-8 -*-
"""可插拔的服务端推送通知（微信 / 邮件 / Server酱）。

全部按环境变量配置，未配置的渠道自动跳过（no-op），因此不配也不会报错。
被 bottom_volume_signal 的后台扫描在命中「底部放量」时调用。

支持渠道（配了哪个就走哪个，可同时配多个）：
  - Server酱 Turbo：  环境变量 SERVERCHAN_SENDKEY=SCTxxxx
      → https://sctapi.ftqq.com/<key>.send
  - 企业微信群机器人： 环境变量 WECHAT_WEBHOOK=<机器人 webhook url>
  - 邮件 SMTP：       环境变量 SMTP_HOST / SMTP_PORT(默认465) / SMTP_USER /
                     SMTP_PASS / SMTP_TO（收件人，逗号分隔；缺省=SMTP_USER）
  - WhatsApp(CallMeBot)：环境变量 CALLMEBOT_PHONE（带国家码，如 +8613800138000）/
                     CALLMEBOT_APIKEY（先用手机把 CallMeBot 加为好友并发激活消息取得）
      → https://api.callmebot.com/whatsapp.php
"""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

logger = logging.getLogger(__name__)


def channels_configured() -> List[str]:
    """当前已配置的推送渠道名列表（供 UI/接口显示）。"""
    out = []
    if os.environ.get('SERVERCHAN_SENDKEY'):
        out.append('serverchan')
    if os.environ.get('WECHAT_WEBHOOK'):
        out.append('wechat')
    if os.environ.get('SMTP_HOST') and os.environ.get('SMTP_USER'):
        out.append('email')
    if os.environ.get('CALLMEBOT_PHONE') and os.environ.get('CALLMEBOT_APIKEY'):
        out.append('whatsapp')
    return out


def _send_serverchan(title: str, body: str, images=None) -> bool:
    import requests
    key = os.environ.get('SERVERCHAN_SENDKEY')
    if not key:
        return False
    r = requests.post(f'https://sctapi.ftqq.com/{key}.send',
                      data={'title': title, 'desp': body}, timeout=8)
    return r.status_code == 200


def _send_wechat(title: str, body: str, images=None) -> bool:
    import requests
    url = os.environ.get('WECHAT_WEBHOOK')
    if not url:
        return False
    r = requests.post(url, json={'msgtype': 'text',
                                 'text': {'content': f'{title}\n{body}'}}, timeout=8)
    return r.status_code == 200


def _send_email(title: str, body: str, images=None) -> bool:
    """发邮件。images=[(filename, png_bytes), ...] 时作为图片附件一并发出（纯文本正文不变）。"""
    host = os.environ.get('SMTP_HOST')
    user = os.environ.get('SMTP_USER')
    pwd = os.environ.get('SMTP_PASS')
    if not (host and user and pwd):
        return False
    import html as _html
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.image import MIMEImage
    from email.header import Header

    port = int(os.environ.get('SMTP_PORT', '465'))
    to = os.environ.get('SMTP_TO') or user
    recipients = [x.strip() for x in to.split(',') if x.strip()]

    if images:
        # 图片内嵌正文（cid 引用）：手机/网页端直接显示，不必点开附件。
        # 图各自标题已自带股票名/结论，故 HTML 只需堆叠图 + 文本正文。
        msg = MIMEMultipart('related')
        parts = ['<div style="font-family:sans-serif;font-size:14px;white-space:pre-wrap;'
                 'color:#1e293b;">' + _html.escape(body) + '</div>']
        valid = [(f, d) for (f, d) in images if d]
        for idx, (fname, data) in enumerate(valid):
            parts.append(f'<div style="margin-top:14px;">'
                         f'<img src="cid:img{idx}" style="max-width:100%;height:auto;'
                         f'border:1px solid #eef2f7;border-radius:6px;"></div>')
        msg.attach(MIMEText('<html><body>' + ''.join(parts) + '</body></html>',
                            'html', 'utf-8'))
        for idx, (fname, data) in enumerate(valid):
            img = MIMEImage(data)   # 自动识别 PNG
            img.add_header('Content-ID', f'<img{idx}>')
            img.add_header('Content-Disposition', 'inline', filename=fname)
            msg.attach(img)
    else:
        msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(title, 'utf-8')
    msg['From'] = user
    msg['To'] = ', '.join(recipients)

    with smtplib.SMTP_SSL(host, port, timeout=15) as s:
        s.login(user, pwd)
        s.sendmail(user, recipients, msg.as_string())
    return True


def _send_whatsapp(title: str, body: str, images=None) -> bool:
    import requests
    phone = os.environ.get('CALLMEBOT_PHONE')
    apikey = os.environ.get('CALLMEBOT_APIKEY')
    if not (phone and apikey):
        return False
    r = requests.get('https://api.callmebot.com/whatsapp.php',
                     params={'phone': phone, 'text': f'{title}\n{body}',
                             'apikey': apikey}, timeout=10)
    return r.status_code == 200


_DISPATCH = {
    'serverchan': _send_serverchan,
    'wechat': _send_wechat,
    'email': _send_email,
    'whatsapp': _send_whatsapp,
}


def push(title: str, body: str, images=None, only=None) -> Tuple[List[str], List[str]]:
    """向所有已配置渠道推送。返回 (成功渠道, 失败渠道)。未配置任何渠道则均为空。

    images=[(filename, png_bytes), ...] 目前只有邮件渠道会作为附件发出，其余渠道忽略（仍发文本）。
    only=('email', ...) 时只走指定渠道（如每日小结只发邮件，不骚扰微信/WhatsApp）。
    """
    ok, fail = [], []
    for name, fn in _DISPATCH.items():
        if only and name not in only:
            continue
        try:
            if fn(title, body, images):
                ok.append(name)
        except Exception as e:
            fail.append(name)
            logger.warning(f'[notifier] {name} 推送失败: {e}')
    if ok:
        logger.info(f'[notifier] 已推送: {ok}')
    return ok, fail
