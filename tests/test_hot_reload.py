import unittest
import os
import shutil
import tempfile
import sys
import time
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import limbs.hub as hub
from limbs.skills import self_repair

class TestHotReload(unittest.TestCase):

    def setUp(self):
        # Create a temporary project structure
        self.test_root = tempfile.mkdtemp()
        self.plugins_dir = os.path.join(self.test_root, "plugins")
        os.makedirs(self.plugins_dir)
        
        # Override hub's PLUGINS_DIR for testing
        self.original_plugins_dir = hub.PLUGINS_DIR
        hub.PLUGINS_DIR = self.plugins_dir
        
        # Also override in self_repair
        self.original_skill_plugins_dir = self_repair._plugins_dir
        self_repair._plugins_dir = self.plugins_dir
        
        # Clear registry via class method
        hub.Registry.clear()
        hub._loaded_mtimes = {}
        
        self.ctx = {"workspace": self.test_root}

    def tearDown(self):
        # Restore original paths
        hub.PLUGINS_DIR = self.original_plugins_dir
        self_repair._plugins_dir = self.original_skill_plugins_dir
        shutil.rmtree(self.test_root)

    def test_create_and_hot_load(self):
        """Test creating a tool and verify it's loaded immediately."""
        tool_name = "hello_plugin"
        tool_code = """
@limb("hello_plugin", "A hot-loaded plugin", {"name": {"type": "string"}})
def hello_plugin(args, ctx):
    return f"Hello, {args['name']} from plugin!"
"""
        # 1. Execute create_tool
        args = {"name": tool_name, "code": tool_code}
        res = self_repair.tool_create_tool(args, self.ctx)
        
        self.assertIn("hot-loaded successfully", res)
        self.assertIsNotNone(hub.Registry.get(tool_name))
        
        # 2. Execute the new tool
        exec_res = hub.execute(tool_name, {"name": "CyberGrunt"}, self.ctx)
        self.assertEqual(exec_res, "Hello, CyberGrunt from plugin!")

    def test_plugin_update_reload(self):
        """Test updating a plugin file and verify it reloads."""
        tool_name = "version_tool"
        
        def create_ver(v):
            code = f"""
@limb("{tool_name}", "Version tool", {{}})
def {tool_name}(args, ctx):
    return "version {v}"
"""
            self_repair.tool_create_tool({"name": tool_name, "code": code}, self.ctx)

        # 1. Load V1
        create_ver(1)
        self.assertEqual(hub.execute(tool_name, {}, self.ctx), "version 1")
        
        # 2. Update to V2 (Force mtime change)
        fpath = os.path.join(self.plugins_dir, f"{tool_name}.py")
        old_mtime = os.path.getmtime(fpath)
        
        # Wait until clock ticks for a different mtime
        attempts = 0
        while os.path.getmtime(fpath) <= old_mtime and attempts < 100:
            time.sleep(0.01)
            create_ver(2)
            attempts += 1
        
        # Should be reloaded automatically
        self.assertEqual(hub.execute(tool_name, {}, self.ctx), "version 2")

    def test_remove_tool(self):
        """Test removing a custom tool."""
        tool_name = "to_be_deleted"
        code = f'@limb("{tool_name}", "delete me", {{}})\ndef fn(args,ctx): return "hi"'
        
        self_repair.tool_create_tool({"name": tool_name, "code": code}, self.ctx)
        self.assertIsNotNone(hub.Registry.get(tool_name))
        
        # Remove
        res = self_repair.tool_remove_tool({"name": tool_name}, self.ctx)
        self.assertIn("Deleted", res)
        self.assertIsNone(hub.Registry.get(tool_name))
        self.assertFalse(os.path.exists(os.path.join(self.plugins_dir, f"{tool_name}.py")))

if __name__ == '__main__':
    unittest.main()
