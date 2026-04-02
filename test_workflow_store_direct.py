#!/usr/bin/env python3
"""Direct test of workflow store without HTTP"""
import sys
import os
sys.path.insert(0, os.getcwd())

from ai_forum.workflow_store import WorkflowStore
import tempfile

# Create temp database
db_fd, db_path = tempfile.mkstemp(suffix=".db")
store = WorkflowStore(db_path)

print("=== 直接测试 WorkflowStore ===\n")

try:
    # 1. 创建工作流
    print("1. Shadow 创建工作流...")
    workflow = store.create_workflow(
        title="测试工作流",
        description="这是一个测试",
        workflow_type="feature",
        priority="p1",
        created_by="Shadow"
    )
    print(f"   ✅ 创建成功: #{workflow['id']} - {workflow['title']}")

    # 2. 查询工作流
    print("\n2. 查询工作流列表...")
    workflows = store.list_workflows()
    print(f"   ✅ 查询成功: 找到 {len(workflows)} 个工作流")

    # 3. IronGate 认领
    print(f"\n3. IronGate 认领工作流 #{workflow['id']}...")
    workflow = store.claim_workflow(workflow['id'], "IronGate")
    print(f"   ✅ 认领成功: 负责人 = {workflow['assignee']}")

    # 4. 更新状态
    print("\n4. IronGate 开始工作...")
    workflow = store.set_workflow_status(workflow['id'], "in_progress", "IronGate", "开始分析")
    print(f"   ✅ 状态更新成功: {workflow['status']}")

    # 5. 添加评论
    print("\n5. Shadow 添加评论...")
    comment = store.add_comment(workflow['id'], "Shadow", "请注意测试", "comment")
    print(f"   ✅ 评论成功")

    # 6. 完成工作流
    print("\n6. IronGate 完成工作流...")
    workflow = store.set_workflow_status(workflow['id'], "completed", "IronGate", "完成")
    print(f"   ✅ 完成成功: {workflow['status']}")

    # 7. 转派测试
    print("\n7. 测试转派功能...")
    wf2 = store.create_workflow(
        title="转派测试",
        description="测试转派",
        workflow_type="bug",
        priority="p0",
        created_by="Shadow"
    )
    store.claim_workflow(wf2['id'], "Forge")
    wf2 = store.reassign_workflow(wf2['id'], "Forge", "IronGate", "需要QA技能")
    print(f"   ✅ 转派成功: {wf2['id']} 从 Forge 转给 IronGate")

    # 8. 释放测试
    print("\n8. 测试释放功能...")
    wf3 = store.create_workflow(
        title="释放测试",
        description="测试释放",
        workflow_type="feature",
        priority="p2",
        created_by="Shadow"
    )
    store.claim_workflow(wf3['id'], "IronGate")
    wf3 = store.unclaim_workflow(wf3['id'], "IronGate", "需要重新评估")
    print(f"   ✅ 释放成功: {wf3['id']} 回到 open 状态")

    print("\n=== 所有测试通过！===")

except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()

finally:
    os.close(db_fd)
    os.unlink(db_path)
