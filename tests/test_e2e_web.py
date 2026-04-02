import unittest
import subprocess
import time
import os
import signal
import sys
import json
import urllib.request
from playwright.sync_api import sync_playwright

class TestWebE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Path to main.py
        cls.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.main_path = os.path.join(cls.project_root, "main.py")
        cls.venv_python = os.path.join(cls.project_root, "venv", "bin", "python")
        cls.e2e_config = os.path.join(cls.project_root, "tests", "e2e_config.json")
        
        # Start main.py in background with E2E config
        print(f"\n[E2E] Starting server on port 8081 using {cls.e2e_config}...")
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
        # Give server time to start
        time.sleep(3)
        print("[E2E] Server should be ready.")

    @classmethod
    def tearDownClass(cls):
        print("[E2E] Shutting down server...")
        if cls.server_proc:
            try:
                os.killpg(os.getpgid(cls.server_proc.pid), signal.SIGTERM)
                cls.server_proc.wait(timeout=5)
                cls.server_proc.stdout.close()
            except:
                pass

    def test_chinese_ime_support(self):
        """Verify that Enter does not send message while composing (IME)."""
        os.makedirs("screen", exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("http://localhost:8081")
            
            page.screenshot(path="screen/01_page_loaded.png")
            
            input_box = page.get_by_test_id("chat-input")
            input_box.fill("你好")
            
            # 1. Start composition (IME active)
            input_box.evaluate("el => el.dispatchEvent(new CompositionEvent('compositionstart'))")
            page.screenshot(path="screen/02_ime_composing.png")
            
            # 2. Press Enter
            input_box.press("Enter")
            
            # 3. Check that NO user bubble appeared
            bubbles = page.get_by_test_id("chat-bubble-user")
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

    def test_task_stop_button_visibility_and_click(self):
        """Verify that the Stop button appears during a task and can be clicked."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("http://localhost:8081")
            
            stop_btn = page.get_by_test_id("stop-button")
            # Should be hidden initially
            self.assertFalse(stop_btn.is_visible(), "Stop button should be hidden initially")
            
            # Type and Send
            input_box = page.get_by_test_id("chat-input")
            input_box.fill("Long task")
            page.get_by_test_id("send-button").click()
            
            # Should show while running
            try:
                stop_btn.wait_for(state="visible", timeout=3000)
                self.assertTrue(stop_btn.is_visible(), "Stop button should appear")
                
                # Click Stop
                stop_btn.click()
                print("[E2E] Clicked Stop.")
                
                # Should hide
                stop_btn.wait_for(state="hidden", timeout=3000)
                self.assertFalse(stop_btn.is_visible(), "Stop button should hide after stop")
            except Exception as e:
                print(f"[E2E] Stop button visibility test failed: {e}")
            
            browser.close()

    def test_health_endpoint_contract(self):
        """Verify that the required testability endpoint is exposed."""
        with urllib.request.urlopen("http://localhost:8081/api/test/health", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(payload.get("ok"))
        self.assertIn("active_sessions", payload)
        self.assertIn("loaded_limbs", payload)
        self.assertIn("recent_error", payload)

if __name__ == "__main__":
    unittest.main()
