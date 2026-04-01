"""
Messaging API Wrapper - Support for multiple platforms including Telegram
"""

import json
import logging
import urllib.request
import urllib.parse
import os
import time

log = logging.getLogger("agent")

_config = {}
_platform = "default"  # "default" (custom gateway) or "telegram"

def init(config):
    global _config, _platform
    _config = config
    if _config.get("telegram", {}).get("enabled"):
        _platform = "telegram"
        log.info("[messaging] Initialized with Telegram support")
    else:
        _platform = "default"
        log.info("[messaging] Initialized with default gateway support")

def send_text(to_id, content):
    if _platform == "telegram":
        return _send_telegram_text(to_id, content)
    else:
        return _send_default_text(to_id, content)

def _send_telegram_text(chat_id, text):
    token = _config["telegram"]["bot_token"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        log.error(f"[messaging] Telegram send error: {e}")
        return False

def _send_default_text(to_id, content):
    url = _config.get("api_url")
    token = _config.get("token")
    if not url: return False
    
    body = json.dumps({
        "method": "/msg/sendText",
        "params": {"toId": str(to_id), "content": content},
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=body, headers={"X-API-TOKEN": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("code") == 0
    except Exception: return False

def upload_and_send(to_id, path, caption="", workspace=""):
    """Send image/file to owner. Path can be URL or local path."""
    if _platform == "telegram":
        # Simplified: Telegram sendPhoto via URL or placeholder for local
        token = _config["telegram"]["bot_token"]
        if path.startswith("http"):
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            body = json.dumps({"chat_id": to_id, "photo": path, "caption": caption}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return {"code": 0 if json.loads(resp.read()).get("ok") else -1}
            except Exception as e: return {"code": -1, "msg": str(e)}
        else:
            # Local file upload would need multipart/form-data
            return {"code": -1, "msg": "Local file upload not implemented for TG"}
    else:
        # Default gateway: sendImage
        url = _config.get("api_url")
        if not url: return {"code": -1, "msg": "No API URL"}
        body = json.dumps({
            "method": "/msg/sendImage",
            "params": {"toId": str(to_id), "path": path, "caption": caption}
        }).encode("utf-8")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=15) as resp:
                return {"code": json.loads(resp.read()).get("code", -1)}
        except Exception as e: return {"code": -1, "msg": str(e)}

def send_link(to_id, title, desc, link_url, icon_url=""):
    """Send rich link card."""
    if _platform == "telegram":
        # Telegram links are just formatted text
        text = f"*{title}*\n{desc}\n[View Details]({link_url})"
        success = _send_telegram_text(to_id, text)
        return {"code": 0 if success else -1}
    else:
        url = _config.get("api_url")
        if not url: return {"code": -1}
        body = json.dumps({
            "method": "/msg/sendLink",
            "params": {"toId": str(to_id), "title": title, "desc": desc, "url": link_url, "iconUrl": icon_url}
        }).encode("utf-8")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=10) as resp:
                return {"code": json.loads(resp.read()).get("code", -1)}
        except Exception: return {"code": -1}
