import unittest
import subprocess
import time
import os
import signal
import sys
import urllib.request
import json
from playwright.sync_api import sync_playwright

class TestWebE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.main_path = os.path.join(cls.project_root, "main.py")
        cls.venv_python = os.path.join(cls.project_root, "venv", "bin", "python")
        cls.e2e_config = os.path.join(cls.project_root, "tests", "e2e_config.json")
        
        env = os.environ.copy()
        env["AGENT_CONFIG"] = cls.e2e_config
        
        cls.server_proc = subprocess.Popen(
            [cls.venv_python, cls.main_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cls.project_root,
            env=env,
            preexec_fn=os.setsid,
            text=True
        )
        time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        if cls.server_proc:
            try:
                os.killpg(os.getpgid(cls.server_proc.pid), signal.SIGTERM)
                cls.server_proc.wait(timeout=5)
            except:
                pass
            finally:
                try:
                    if cls.server_proc.stdout:
                        cls.server_proc.stdout.close()
                except Exception:
                    pass
                cls.server_proc = None

    def test_chinese_ime_support(self):
        """Verify that Enter does not send message while composing (IME)."""
        os.makedirs("screen", exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("http://localhost:8081")

            page.screenshot(path="screen/01_page_loaded.png")

            input_box = page.locator("#userInput")
            input_box.fill("你好")

            # 1. Start composition (IME active)
            input_box.evaluate("el => el.dispatchEvent(new CompositionEvent('compositionstart'))")
            page.screenshot(path="screen/02_ime_composing.png")

            # 2. Press Enter
            input_box.press("Enter")

            # 3. Check that NO user bubble appeared
            bubbles = page.locator(".bubble.user")
            self.assertEqual(bubbles.count(), 0, "Message should NOT be sent during composition")
            page.screenshot(path="screen/03_enter_pressed_during_ime.png")

            # 4. End composition
            input_box.evaluate("el => el.dispatchEvent(new CompositionEvent('compositionend'))")

            # 5. Press Enter again
            input_box.press("Enter")

            # 6. Now it should be sent
            bubbles.last.wait_for(state="visible", timeout=2000)
            self.assertEqual(bubbles.count(), 1, "Message should be sent after composition ends")
            page.screenshot(path="screen/04_message_sent_successfully.png")

            browser.close()

    def test_test_health_endpoint_contract(self):
        with urllib.request.urlopen("http://localhost:8081/api/test/health", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(payload.get("ok"))
        self.assertIn("active_sessions", payload)
        self.assertIn("loaded_limbs", payload)
        self.assertIn("recent_error", payload)
        self.assertIsInstance(payload["active_sessions"], int)
        self.assertIsInstance(payload["loaded_limbs"], list)
        self.assertIn("exec", payload["loaded_limbs"])

if __name__ == "__main__":
    unittest.main()
