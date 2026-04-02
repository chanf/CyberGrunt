#!/usr/bin/env python3
"""
Simple manual test for workflow system
"""
import json
import urllib.request
import time

BASE_URL = "http://localhost:5678"

def api_call(method, endpoint, data=None):
    url = f"{BASE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}

    body = None
    if data:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

print("=== 工作流系统手动测试 ===\n")

# 1. 检查服务器
print("1. 检查服务器状态...")
result = api_call("GET", "/healthz")
print(f"   服务器: {'✅ 正常' if result.get('ok') else '❌ 异常'}")

# 2. 创建工作流
print("\n2. Shadow 创建工作流...")
result = api_call("POST", "/api/workflows", {
    "title": "测试工作流",
    "description": "这是一个测试工作流",
    "type": "feature",
    "priority": "p1",
    "created_by": "Shadow"
})
if "error" in result:
    print(f"   ❌ 创建失败: {result['error']}")
else:
    print(f"   ✅ 创建成功: #{result['workflow']['id']} - {result['workflow']['title']}")
    workflow_id = result['workflow']['id']

# 3. 查询工作流列表
print("\n3. 查询工作流列表...")
result = api_call("GET", "/api/workflows")
if "error" in result:
    print(f"   ❌ 查询失败: {result['error']}")
else:
    print(f"   ✅ 查询成功: 找到 {len(result['workflows'])} 个工作流")
    for w in result['workflows'][:3]:
        print(f"      #{w['id']} {w['title']} - {w['status']} - {w.get('assignee', '未认领')}")

# 4. IronGate 认领工作流
if 'workflow_id' in locals():
    print(f"\n4. IronGate 认领工作流 #{workflow_id}...")
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "IronGate"
    })
    if "error" in result:
        print(f"   ❌ 认领失败: {result['error']}")
    else:
        print(f"   ✅ 认领成功: 负责人 = {result['workflow']['assignee']}")

    # 5. 更新状态
    print(f"\n5. IronGate 开始工作...")
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "in_progress",
        "updated_by": "IronGate",
        "note": "开始分析需求"
    })
    if "error" in result:
        print(f"   ❌ 状态更新失败: {result['error']}")
    else:
        print(f"   ✅ 状态更新成功: {result['workflow']['status']}")

    # 6. 添加评论
    print(f"\n6. Shadow 添加评论...")
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "Shadow",
        "body": "请注意测试覆盖率",
        "comment_type": "comment"
    })
    if "error" in result:
        print(f"   ❌ 评论失败: {result['error']}")
    else:
        print(f"   ✅ 评论成功")

    # 7. 完成工作流
    print(f"\n7. IronGate 完成工作流...")
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "completed",
        "updated_by": "IronGate",
        "note": "测试完成"
    })
    if "error" in result:
        print(f"   ❌ 完成失败: {result['error']}")
    else:
        print(f"   ✅ 完成成功: {result['workflow']['status']}")

print("\n=== 测试完成 ===")
print(f"\n访问 http://localhost:5678 查看工作流列表")
