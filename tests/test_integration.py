import unittest
import os
import shutil
import tempfile
import sys
import json
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain import central as llm
from limbs import hub as limbs_hub

class TestIntegration(unittest.TestCase):

    def setUp(self):
        # Create a temporary workspace and sessions dir
        self.test_root = tempfile.mkdtemp()
        self.workspace = os.path.join(self.test_root, "workspace")
        self.sessions = os.path.join(self.test_root, "sessions")
        os.makedirs(self.workspace)
        os.makedirs(self.sessions)
        
        # Initialize the Brain with mock config
        models_config = {
            "default": {
                "api_key": "fake_key",
                "api_base": "http://fake_api.com/v1",
                "model": "gpt-mock"
            }
        }
        llm.init(models_config, self.workspace, "test_owner", self.sessions)
        
        # Initialize limbs hub
        limbs_hub.init_extra({})

    def tearDown(self):
        # Clean up
        shutil.rmtree(self.test_root)

    @patch('brain.central._call_llm')
    def test_single_tool_use_flow(self, mock_call_llm):
        """Test user -> Brain -> write_file tool -> Brain -> user response."""
        
        # Scenario: User says "write hello to a.txt"
        # 1. LLM returns a tool call for write_file
        # 2. LLM returns a final confirmation content
        
        mock_responses = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "I will write the file for you.",
                        "tool_calls": [{
                            "id": "tc_01",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "a.txt", "content": "hello world"})
                            }
                        }]
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "The file has been written successfully."
                    }
                }]
            }
        ]
        mock_call_llm.side_effect = mock_responses
        
        # Execute chat
        reply = llm.chat("write hello to a.txt", "session_1")
        
        # Verify file creation
        file_path = os.path.join(self.workspace, "a.txt")
        self.assertTrue(os.path.exists(file_path))
        with open(file_path, "r") as f:
            self.assertEqual(f.read(), "hello world")
            
        # Verify final reply
        self.assertEqual(reply, "The file has been written successfully.")
        
        # Verify tool definition was sent in second call (to confirm session state)
        self.assertEqual(mock_call_llm.call_count, 2)

    @patch('brain.central._call_llm')
    def test_tool_error_handling_flow(self, mock_call_llm):
        """Test that Brain correctly receives and handles tool error results."""
        
        # Scenario: User says "read secret.txt" (which doesn't exist)
        mock_responses = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "tc_02",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "secret.txt"})
                            }
                        }]
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "I'm sorry, I couldn't find that file."
                    }
                }]
            }
        ]
        mock_call_llm.side_effect = mock_responses
        
        reply = llm.chat("read secret.txt", "session_2")
        
        self.assertIn("couldn't find that file", reply)
        # Verify first call included the user message
        first_call_messages = mock_call_llm.call_args_list[0][0][0]
        self.assertEqual(first_call_messages[1]["content"], "read secret.txt")

if __name__ == '__main__':
    unittest.main()
