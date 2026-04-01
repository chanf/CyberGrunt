"""
Messaging API Wrapper - Support for multiple platforms including Telegram
"""

import json
import logging
import urllib.request
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
    
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        log.error(f"[messaging] Telegram send error: {e}")
        # Fallback without markdown if error
        try:
            body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read()).get("ok", False)
        except:
            pass
        return False

def _send_default_text(to_id, content):
    # Original logic from router.py
    url = _config.get("api_url")
    token = _config.get("token")
    guid = _config.get("guid")
    
    if not url or not token or not guid:
        return False
        
    body = json.dumps({
        "method": "/msg/sendText",
        "params": {"guid": guid, "toId": str(to_id), "content": content},
    }).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-TOKEN": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("code") == 0
    except Exception as e:
        log.error(f"[messaging] Default gateway send error: {e}")
        return False

# Placeholder for other media types (images, files, etc.)
def send_image(to_id, file_path):
    if _platform == "telegram":
        # Implementation for TG photo upload could be added here
        pass
    return True
