import unittest
import os
import sys
import json
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import limbs.hub as hub
from limbs.hub import limb

class TestLimbsHub(unittest.TestCase):

    def setUp(self):
        # Clear registry before each test via the new Registry class
        hub.Registry.clear()

    def test_limb_registration(self):
        """Test if the @limb decorator correctly registers tools."""
        @limb("test_tool", "A test tool", {"param1": {"type": "string"}}, ["param1"])
        def my_test_fn(args, ctx):
            return f"Hello {args['param1']}"

        self.assertIsNotNone(hub.Registry.get("test_tool"))
        definition = hub.Registry.get("test_tool")["definition"]
        self.assertEqual(definition["function"]["name"], "test_tool")
        self.assertEqual(definition["function"]["description"], "A test tool")
        self.assertIn("param1", definition["function"]["parameters"]["properties"])

    def test_limb_execution_success(self):
        """Test if a registered tool executes correctly with args and ctx."""
        @limb("add", "Add two numbers", {"a": {"type": "number"}, "b": {"type": "number"}})
        def add_fn(args, ctx):
            return args['a'] + args['b']

        ctx = {"workspace": "/tmp"}
        args = {"a": 10, "b": 20}
        result = hub.execute("add", args, ctx)
        self.assertEqual(result, 30)

    def test_limb_execution_unknown(self):
        """Test error message for unknown tool execution."""
        result = hub.execute("non_existent", {}, {})
        self.assertTrue(result.startswith("[error] unknown tool"))

    def test_limb_execution_exception(self):
        """Test if exceptions in tools are caught and returned as error strings."""
        @limb("fail", "A tool that fails", {})
        def fail_fn(args, ctx):
            raise ValueError("Something went wrong")

        result = hub.execute("fail", {}, {})
        self.assertIn("[error]", result)
        self.assertIn("Something went wrong", result)

    @patch('mcp_client.execute')
    @patch('mcp_client.get_all_tool_defs')
    def test_mcp_routing(self, mock_get_defs, mock_execute):
        """Test if tools with double underscores are routed to MCP client."""
        mock_execute.return_value = "MCP Result"
        
        # In hub.execute, if tool name has __ it should call mcp_client.execute
        result = hub.execute("server__tool", {"q": "test"}, {"ctx": "test"})
        
        mock_execute.assert_called_once_with("server__tool", {"q": "test"})
        self.assertEqual(result, "MCP Result")

    def test_get_definitions_merging(self):
        """Test if get_definitions merges local and MCP tools."""
        @limb("local_tool", "Local", {})
        def local_fn(args, ctx): pass
        
        # Mock mcp_client.get_all_tool_defs
        with patch('mcp_client.get_all_tool_defs') as mock_mcp_defs:
            mock_mcp_defs.return_value = [{"type": "function", "function": {"name": "mcp_tool"}}]
            
            defs = hub.get_definitions()
            
            names = [d["function"]["name"] for d in defs]
            self.assertIn("local_tool", names)
            self.assertIn("mcp_tool", names)
            self.assertEqual(len(defs), 2)

if __name__ == '__main__':
    unittest.main()
