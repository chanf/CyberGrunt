import unittest
import subprocess
import time
import os
import signal
import sys
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

    def test_chinese_ime_support(self):
        """Verify that Enter does not send message while composing (IME)."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("http://localhost:8081")
            
            input_box = page.locator("#userInput")
            input_box.fill("你好")
            
            # 1. Start composition (IME active)
            input_box.evaluate("el => el.dispatchEvent(new CompositionEvent('compositionstart'))")
            
            # 2. Press Enter
            input_box.press("Enter")
            
            # 3. Check that NO user bubble appeared (except maybe initial empty ones if any)
            bubbles = page.locator(".bubble.user")
            self.assertEqual(bubbles.count(), 0, "Message should NOT be sent during composition")
            
            # 4. End composition
            input_box.evaluate("el => el.dispatchEvent(new CompositionEvent('compositionend'))")
            
            # 5. Press Enter again
            input_box.press("Enter")
            
            # 6. Now it should be sent
            bubbles.last.wait_for(state="visible", timeout=2000)
            self.assertEqual(bubbles.count(), 1, "Message should be sent after composition ends")
            
            browser.close()

if __name__ == "__main__":
    unittest.main()
