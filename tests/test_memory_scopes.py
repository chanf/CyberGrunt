import unittest
import os
import sys
import tempfile
import shutil
import logging
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.memory import manager

class TestMemoryScopes(unittest.TestCase):

    def setUp(self):
        self.test_db_dir = tempfile.mkdtemp()
        self.mock_vec = [0.1] * 1024
        
        # Correct config structure
        config = {
            "memory": {
                "enabled": True,
                "embedding_api": {"api_key": "fake"},
                "similarity_threshold": 0.95
            }
        }
        manager.init(config, {}, self.test_db_dir)

    def tearDown(self):
        shutil.rmtree(self.test_db_dir)

    @patch('brain.memory.manager._embed')
    def test_scope_isolation(self, mock_embed):
        self.assertTrue(manager._enabled, "Memory manager must be enabled for this test")
        mock_embed.return_value = [self.mock_vec]
        
        # 1. Manually add a QA memory
        qa_sid = "web_irongate_session"
        manager._table.add([{
            "id": "qa_1",
            "fact": "QA secret facts",
            "keywords": "[]",
            "persons": "[]",
            "timestamp": "",
            "topic": "testing",
            "session_key": "qa::web_irongate_session",
            "created_at": 1000,
            "vector": self.mock_vec
        }])
        
        # 2. Retrieve as Forge (Dev)
        dev_sid = "web_forge_session"
        res_dev = manager.retrieve("any query", dev_sid)
        self.assertNotIn("QA secret", res_dev, "Dev should NOT see QA private memories")
        
        # 3. Retrieve as IronGate (QA)
        res_qa = manager.retrieve("any query", qa_sid)
        self.assertIn("QA secret", res_qa, "QA should see their own memories")

if __name__ == '__main__':
    unittest.main()
