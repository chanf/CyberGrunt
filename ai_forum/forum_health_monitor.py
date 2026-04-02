"""Forum Health Monitor - 自动检测和修复论坛问题"""

import os
import sys
import subprocess
import time
import urllib.request
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FORUM_PORT = 8090
FORUM_URL = f"http://localhost:{FORUM_PORT}"
FORUM_SERVER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forum_server.py")
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_reports", "forum_monitor_log.txt")


def log(message):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {message}\n"
    print(log_msg.strip())
    
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg)


def check_forum_health():
    """检查论坛健康状态"""
    try:
        # 检查健康端点
        req = urllib.request.Request(f"{FORUM_URL}/healthz", method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = response.read().decode("utf-8")
                if '"ok": true' in data or '"ok":true' in data:
                    return True
        return False
    except Exception as e:
        log(f"健康检查失败: {e}")
        return False


def get_forum_pid():
    """获取论坛服务器进程 ID"""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in result.stdout.split("\n"):
            if "forum_server.py" in line and "python" in line:
                parts = line.split()
                if parts:
                    try:
                        return int(parts[1])
                    except (ValueError, IndexError):
                        continue
        return None
    except Exception as e:
        log(f"获取进程 ID 失败: {e}")
        return None


def restart_forum():
    """重启论坛服务器"""
    log("尝试重启论坛服务器...")
    
    # 先停止现有进程
    pid = get_forum_pid()
    if pid:
        try:
            subprocess.run(["kill", str(pid)], capture_output=True, timeout=5)
            log(f"已停止进程 {pid}")
            time.sleep(2)
        except Exception as e:
            log(f"停止进程失败: {e}")
    
    # 启动新进程
    try:
        # 切换到正确的目录
        forum_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.Popen(
            ["python", "forum_server.py"],
            cwd=forum_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log("论坛服务器启动命令已发送")
        time.sleep(3)
        
        # 验证启动成功
        if check_forum_health():
            log("✅ 论坛服务器恢复成功")
            return True
        else:
            log("❌ 论坛服务器启动失败")
            return False
    except Exception as e:
        log(f"❌ 重启失败: {e}")
        return False


def monitor_and_fix():
    """监控并修复论坛"""
    log("=== 论坛健康检查开始 ===")
    
    if check_forum_health():
        log("✅ 论坛运行正常")
        return True
    
    log("❌ 论坛异常，开始修复...")
    
    # 尝试修复，最多重试 3 次
    for attempt in range(1, 4):
        log(f"修复尝试 {attempt}/3")
        
        if restart_forum():
            log("=== 论坛健康检查结束 ===")
            return True
        
        if attempt < 3:
            log("等待 5 秒后重试...")
            time.sleep(5)
    
    log("❌ 论坛修复失败，需要人工介入")
    log("=== 论坛健康检查结束 ===")
    return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="论坛健康监控")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    args = parser.parse_args()
    
    if args.once:
        monitor_and_fix()
    else:
        # 持续监控模式（用于调试）
        log("进入持续监控模式（按 Ctrl+C 退出）")
        try:
            while True:
                monitor_and_fix()
                time.sleep(120)  # 每 2 分钟检查一次
        except KeyboardInterrupt:
            log("\n监控已停止")
