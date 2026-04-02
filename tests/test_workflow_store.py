"""
Unit tests for WorkflowStore
"""

import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ai_forum.workflow_store import WorkflowStore


class TestWorkflowStore(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.store = WorkflowStore(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_create_workflow(self):
        """Test creating a new workflow."""
        workflow = self.store.create_workflow(
            title="Test Workflow",
            description="This is a test workflow",
            workflow_type="feature",
            priority="p1",
            created_by="Shadow",
            estimate_hours=4,
        )

        self.assertEqual(workflow["title"], "Test Workflow")
        self.assertEqual(workflow["status"], "open")
        self.assertEqual(workflow["assignee"], None)
        self.assertEqual(workflow["priority"], "p1")
        self.assertEqual(workflow["type"], "feature")

    def test_claim_workflow(self):
        """Test claiming a workflow."""
        workflow = self.store.create_workflow(
            title="Test Workflow",
            description="Test",
            workflow_type="feature",
            priority="p1",
            created_by="Shadow",
        )

        # Claim by IronGate
        claimed = self.store.claim_workflow(workflow["id"], "IronGate")

        self.assertEqual(claimed["status"], "assigned")
        self.assertEqual(claimed["assignee"], "IronGate")
        self.assertIsNotNone(claimed["claimed_at"])

        # Check comments
        comments = self.store.list_workflow_comments(workflow["id"])
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["comment_type"], "claim")

    def test_claim_already_claimed(self):
        """Test that claiming an already claimed workflow fails."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.store.claim_workflow(workflow["id"], "IronGate")

        with self.assertRaises(ValueError) as ctx:
            self.store.claim_workflow(workflow["id"], "Forge")

        self.assertIn("already assigned", str(ctx.exception))

    def test_unclaim_workflow(self):
        """Test unclaiming a workflow."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.store.claim_workflow(workflow["id"], "IronGate")
        unclaimed = self.store.unclaim_workflow(workflow["id"], "IronGate", "Need to reassign")

        self.assertEqual(unclaimed["status"], "open")
        self.assertEqual(unclaimed["assignee"], None)

    def test_unclaim_wrong_assignee(self):
        """Test that only current assignee can unclaim."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.store.claim_workflow(workflow["id"], "IronGate")

        with self.assertRaises(ValueError) as ctx:
            self.store.unclaim_workflow(workflow["id"], "Forge", "Trying to steal")

        self.assertIn("Only current assignee", str(ctx.exception))

    def test_reassign_workflow(self):
        """Test reassigning a workflow."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.store.claim_workflow(workflow["id"], "IronGate")
        reassigned = self.store.reassign_workflow(workflow["id"], "IronGate", "Forge", "Needs dev skills")

        self.assertEqual(reassigned["assignee"], "Forge")

        comments = self.store.list_workflow_comments(workflow["id"])
        self.assertEqual(comments[-1]["comment_type"], "reassign")

    def test_set_status(self):
        """Test updating workflow status."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.store.claim_workflow(workflow["id"], "IronGate")

        # Start work
        in_progress = self.store.set_workflow_status(workflow["id"], "in_progress", "IronGate")
        self.assertEqual(in_progress["status"], "in_progress")

        # Complete
        completed = self.store.set_workflow_status(workflow["id"], "completed", "IronGate", "All done!")
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["completed_at"])

    def test_set_status_by_non_assignee(self):
        """Test that only assignee can change status."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.store.claim_workflow(workflow["id"], "IronGate")

        with self.assertRaises(ValueError) as ctx:
            self.store.set_workflow_status(workflow["id"], "in_progress", "Forge")

        self.assertIn("Only assignee", str(ctx.exception))

    def test_list_workflows_with_filters(self):
        """Test listing workflows with filters."""
        self.store.create_workflow(
            title="P0 Bug", description="Critical bug", workflow_type="bug", priority="p0", created_by="Shadow"
        )
        self.store.create_workflow(
            title="P1 Feature", description="New feature", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        p0_workflows = self.store.list_workflows(priority="p0")
        self.assertEqual(len(p0_workflows), 1)
        self.assertEqual(p0_workflows[0]["priority"], "p0")

        bug_workflows = self.store.list_workflows(workflow_type="bug")
        self.assertEqual(len(bug_workflows), 1)
        self.assertEqual(bug_workflows[0]["type"], "bug")

    def test_add_comment(self):
        """Test adding comments to workflow."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        comment = self.store.add_comment(workflow["id"], "IronGate", "This looks good!", "comment")

        self.assertEqual(comment["author"], "IronGate")
        self.assertEqual(comment["body"], "This looks good!")
        self.assertEqual(comment["comment_type"], "comment")

        # Check workflow comment count
        updated = self.store.get_workflow_by_id(workflow["id"])
        self.assertEqual(updated["comment_count"], 1)

    def test_invalid_workflow_type(self):
        """Test that invalid workflow type raises error."""
        with self.assertRaises(ValueError) as ctx:
            self.store.create_workflow(
                title="Test", description="Test", workflow_type="invalid", priority="p1", created_by="Shadow"
            )

        self.assertIn("Invalid type", str(ctx.exception))

    def test_invalid_priority(self):
        """Test that invalid priority raises error."""
        with self.assertRaises(ValueError) as ctx:
            self.store.create_workflow(
                title="Test", description="Test", workflow_type="feature", priority="p5", created_by="Shadow"
            )

        self.assertIn("Invalid priority", str(ctx.exception))

    def test_workflow_comment_count(self):
        """Test that comment count is accurate."""
        workflow = self.store.create_workflow(
            title="Test", description="Test", workflow_type="feature", priority="p1", created_by="Shadow"
        )

        self.assertEqual(workflow["comment_count"], 0)

        self.store.add_comment(workflow["id"], "IronGate", "Comment 1")
        self.store.add_comment(workflow["id"], "Forge", "Comment 2")

        updated = self.store.get_workflow_by_id(workflow["id"])
        self.assertEqual(updated["comment_count"], 2)


if __name__ == "__main__":
    unittest.main()
