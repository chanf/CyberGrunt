#!/usr/bin/env python3
"""
Comprehensive Workflow System Test Suite

This script simulates all 3 AIs (IronGate, Forge, Shadow) and tests
all workflow scenarios including:
1. Creating workflows
2. Claiming workflows
3. Updating status
4. Adding comments
5. Reassigning workflows
6. Unclaiming workflows
7. Error cases and edge cases
"""

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict


BASE_URL = "http://localhost:5678"


def api_call(method: str, endpoint: str, data: Dict = None) -> Dict:
    """Make API call to workflow server."""
    url = f"{BASE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}

    body = None
    if data:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "details": e.read().decode("utf-8")}
    except Exception as e:
        return {"error": str(e)}


def print_section(title: str):
    """Print section header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_result(description: str, result: Dict):
    """Print test result."""
    if "error" in result:
        print(f"❌ {description}")
        print(f"   Error: {result['error']}")
    else:
        print(f"✅ {description}")
        if "workflow" in result:
            wf = result["workflow"]
            print(f"   Workflow #{wf['id']}: {wf['title']} [{wf['status']}]")
        elif "workflows" in result:
            print(f"   Found {len(result['workflows'])} workflows")
        elif "comment" in result:
            print(f"   Comment: {result['comment']['body'][:50]}...")


def test_basic_workflow_lifecycle():
    """测试基本工作流生命周期"""
    print_section("场景 1: 基本工作流生命周期")

    # 1. Shadow 创建工作流
    result = api_call("POST", "/api/workflows", {
        "title": "实现用户认证系统",
        "description": "需要实现完整的用户认证系统，包括注册、登录、Token管理和权限控制。",
        "type": "feature",
        "priority": "p0",
        "created_by": "Shadow",
        "estimate_hours": 8
    })
    print_result("Shadow 创建 P0 工作流", result)

    if "error" in result:
        return

    workflow_id = result["workflow"]["id"]

    # 2. 列出可认领的工作流
    result = api_call("GET", "/api/workflows?status=open&priority=p0")
    print_result("查询可认领的 P0 工作流", result)

    # 3. IronGate 认领工作流
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "IronGate"
    })
    print_result("IronGate 认领工作流", result)

    # 4. IronGate 开始工作
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "in_progress",
        "updated_by": "IronGate",
        "note": "开始分析需求"
    })
    print_result("IronGate 将状态改为 in_progress", result)

    # 5. IronGate 添加评论
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "IronGate",
        "body": "需求分析完成，准备开始设计认证方案。",
        "comment_type": "comment"
    })
    print_result("IronGate 添加评论", result)

    # 6. IronGate 完成工作
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "completed",
        "updated_by": "IronGate",
        "note": "认证系统实现完成，等待测试验证"
    })
    print_result("IronGate 将状态改为 completed", result)

    # 7. 查看已完成的工作流
    result = api_call("GET", "/api/workflows?status=completed")
    print_result("查询已完成的工作流", result)

    print("\n✅ 场景 1 完成！")


def test_reassign_workflow():
    """测试工作流转派"""
    print_section("场景 2: 工作流转派")

    # 1. Forge 创建工作流
    result = api_call("POST", "/api/workflows", {
        "title": "修复 API 性能问题",
        "description": "用户认证 API 响应时间过长，需要优化查询逻辑。",
        "type": "bug",
        "priority": "p1",
        "created_by": "Forge"
    })
    print_result("Forge 创建 Bug 工作流", result)

    if "error" in result:
        return

    workflow_id = result["workflow"]["id"]

    # 2. Shadow 认领
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "Shadow"
    })
    print_result("Shadow 认领工作流", result)

    # 3. Shadow 发现这是开发问题，转派给 Forge
    result = api_call("POST", f"/api/workflows/{workflow_id}/reassign", {
        "from": "Shadow",
        "to": "Forge",
        "reason": "需要修改底层查询逻辑，转到 Forge 处理"
    })
    print_result("Shadow 将工作流转派给 Forge", result)

    # 4. 验证转派结果
    result = api_call("GET", f"/api/workflows/{workflow_id}")
    print_result("验证转派结果", result)

    print("\n✅ 场景 2 完成！")


def test_unclaim_workflow():
    """测试释放工作流"""
    print_section("场景 3: 释放工作流")

    # 1. Shadow 创建工作流
    result = api_call("POST", "/api/workflows", {
        "title": "编写 API 文档",
        "description": "需要为所有新增的 API 端点编写详细文档。",
        "type": "doc",
        "priority": "p2",
        "created_by": "Shadow"
    })
    print_result("Shadow 创建文档工作流", result)

    if "error" in result:
        return

    workflow_id = result["workflow"]["id"]

    # 2. IronGate 认领
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "IronGate"
    })
    print_result("IronGate 认领工作流", result)

    # 3. IronGate 释放任务
    result = api_call("POST", f"/api/workflows/{workflow_id}/unclaim", {
        "assignee": "IronGate",
        "reason": "当前有更高优先级的任务，先释放此任务"
    })
    print_result("IronGate 释放工作流", result)

    # 4. 验证任务回到 open 状态
    result = api_call("GET", f"/api/workflows/{workflow_id}")
    print_result("验证任务回到 open 状态", result)

    # 5. Forge 可以重新认领
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "Forge"
    })
    print_result("Forge 认领已释放的工作流", result)

    print("\n✅ 场景 3 完成！")


def test_comment_discussion():
    """测试评论讨论功能"""
    print_section("场景 4: 通过评论协作")

    # 1. Shadow 创建工作流
    result = api_call("POST", "/api/workflows", {
        "title": "设计数据库架构",
        "description": "为新功能设计高效可扩展的数据库架构。",
        "type": "feature",
        "priority": "p1",
        "created_by": "Shadow"
    })
    print_result("Shadow 创建架构设计工作流", result)

    if "error" in result:
        return

    workflow_id = result["workflow"]["id"]

    # 2. Forge 认领
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "Forge"
    })
    print_result("Forge 认领工作流", result)

    # 3. IronGate 提出建议（非负责人也能评论）
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "IronGate",
        "body": "建议使用 PostgreSQL 的 JSONB 字段存储灵活的数据结构。",
        "comment_type": "comment"
    })
    print_result("IronGate 添加技术建议评论", result)

    # 4. Shadow 提出需求
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "Shadow",
        "body": "需要支持事务和 ACID 特性。",
        "comment_type": "comment"
    })
    print_result("Shadow 添加需求评论", result)

    # 5. Forge 回应并开始工作
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "Forge",
        "body": "收到建议，将采用 PostgreSQL + JSONB 方案，并确保事务支持。",
        "comment_type": "comment"
    })
    print_result("Forge 回应评论", result)

    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "in_progress",
        "updated_by": "Forge"
    })
    print_result("Forge 开始工作", result)

    # 6. 查看所有评论
    result = api_call("GET", f"/api/workflows/{workflow_id}/comments")
    print_result(f"查看工作流的所有评论 (共 {len(result.get('comments', []))} 条)", result)

    print("\n✅ 场景 4 完成！")


def test_blocked_workflow():
    """测试工作流阻塞场景"""
    print_section("场景 5: 工作流阻塞")

    # 1. Shadow 创建工作流
    result = api_call("POST", "/api/workflows", {
        "title": "集成支付网关",
        "description": "集成第三方支付网关API。",
        "type": "feature",
        "priority": "p1",
        "created_by": "Shadow"
    })
    print_result("Shadow 创建支付集成工作流", result)

    if "error" in result:
        return

    workflow_id = result["workflow"]["id"]

    # 2. Forge 认领并开始工作
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "Forge"
    })
    print_result("Forge 认领工作流", result)

    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "in_progress",
        "updated_by": "Forge"
    })
    print_result("Forge 开始工作", result)

    # 3. Forge 遇到阻塞问题
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "blocked",
        "updated_by": "Forge",
        "note": "等待支付网关提供商的 API 密钥"
    })
    print_result("Forge 将工作流标记为 blocked", result)

    # 4. 添加阻塞原因评论
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "Forge",
        "body": "已提交 API 密钥申请，等待提供商审核。预计需要 2-3 个工作日。",
        "comment_type": "comment"
    })
    print_result("Forge 添加阻塞详情", result)

    # 5. Shadow 获取到 API 密钥后通知 Forge
    result = api_call("POST", f"/api/workflows/{workflow_id}/comments", {
        "author": "Shadow",
        "body": "API 密钥已获取，请查收邮件。",
        "comment_type": "comment"
    })
    print_result("Shadow 通知阻塞已解除", result)

    # 6. Forge 恢复工作
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "in_progress",
        "updated_by": "Forge",
        "note": "收到 API 密钥，继续集成工作"
    })
    print_result("Forge 恢复工作", result)

    print("\n✅ 场景 5 完成！")


def test_error_cases():
    """测试错误场景"""
    print_section("场景 6: 错误处理测试")

    # 1. Shadow 创建工作流
    result = api_call("POST", "/api/workflows", {
        "title": "测试工作流",
        "description": "用于测试错误场景",
        "type": "feature",
        "priority": "p3",
        "created_by": "Shadow"
    })
    print_result("Shadow 创建测试工作流", result)

    if "error" in result:
        return

    workflow_id = result["workflow"]["id"]

    # 测试 1: 非负责人无法更新状态
    print("\n--- 测试: 非负责人无法更新状态 ---")
    result = api_call("POST", f"/api/workflows/{workflow_id}/status", {
        "status": "in_progress",
        "updated_by": "Forge"  # Forge 不是负责人
    })
    if "error" in result:
        print("✅ 正确阻止了非负责人更新状态")
    else:
        print("❌ 应该阻止非负责人更新状态")

    # 测试 2: 已认领的工作流无法被认领
    print("\n--- 测试: 已认领的工作流无法被认领 ---")
    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "IronGate"
    })
    print_result("IronGate 认领工作流", result)

    result = api_call("POST", f"/api/workflows/{workflow_id}/claim", {
        "assignee": "Forge"  # 尝试验证认领
    })
    if "error" in result:
        print("✅ 正确阻止了重复认领")
    else:
        print("❌ 应该阻止重复认领")

    # 测试 3: 只有负责人可以释放
    print("\n--- 测试: 只有负责人可以释放 ---")
    result = api_call("POST", f"/api/workflows/{workflow_id}/unclaim", {
        "assignee": "Forge",  # Forge 不是负责人
        "reason": "尝试释放别人的任务"
    })
    if "error" in result:
        print("✅ 正确阻止了非负责人释放")
    else:
        print("❌ 应该阻止非负责人释放")

    # 测试 4: 只有负责人可以转派
    print("\n--- 测试: 只有负责人可以转派 ---")
    result = api_call("POST", f"/api/workflows/{workflow_id}/reassign", {
        "from": "Forge",  # Forge 不是负责人
        "to": "Shadow",
        "reason": "尝试转派别人的任务"
    })
    if "error" in result:
        print("✅ 正确阻止了非负责人转派")
    else:
        print("❌ 应该阻止非负责人转派")

    # 测试 5: 无效的工作流类型
    print("\n--- 测试: 无效的工作流类型 ---")
    result = api_call("POST", "/api/workflows", {
        "title": "测试",
        "description": "测试",
        "type": "invalid_type",  # 无效类型
        "priority": "p1",
        "created_by": "Shadow"
    })
    if "error" in result:
        print("✅ 正确拒绝了无效的工作流类型")
    else:
        print("❌ 应该拒绝无效的工作流类型")

    # 测试 6: 无效的优先级
    print("\n--- 测试: 无效的优先级 ---")
    result = api_call("POST", "/api/workflows", {
        "title": "测试",
        "description": "测试",
        "type": "feature",
        "priority": "p5",  # 无效优先级
        "created_by": "Shadow"
    })
    if "error" in result:
        print("✅ 正确拒绝了无效的优先级")
    else:
        print("❌ 应该拒绝无效的优先级")

    print("\n✅ 场景 6 完成！")


def test_filter_and_query():
    """测试过滤和查询功能"""
    print_section("场景 7: 过滤和查询")

    # 创建多个不同类型的工作流
    workflows = []

    workflows.append(api_call("POST", "/api/workflows", {
        "title": "P0 Bug 修复",
        "description": "紧急Bug",
        "type": "bug",
        "priority": "p0",
        "created_by": "Shadow"
    }))

    workflows.append(api_call("POST", "/api/workflows", {
        "title": "P1 新功能",
        "description": "重要功能",
        "type": "feature",
        "priority": "p1",
        "created_by": "Forge"
    }))

    workflows.append(api_call("POST", "/api/workflows", {
        "title": "P2 文档更新",
        "description": "更新文档",
        "type": "doc",
        "priority": "p2",
        "created_by": "IronGate"
    }))

    time.sleep(0.5)

    # 测试各种过滤
    print("\n--- 测试: 按优先级过滤 ---")
    result = api_call("GET", "/api/workflows?priority=p0")
    print_result("查询 P0 工作流", result)

    print("\n--- 测试: 按类型过滤 ---")
    result = api_call("GET", "/api/workflows?type=feature")
    print_result("查询 feature 类型工作流", result)

    print("\n--- 测试: 按状态过滤 ---")
    result = api_call("GET", "/api/workflows?status=open")
    print_result("查询 open 状态工作流", result)

    print("\n--- 测试: 按创建者过滤 ---")
    result = api_call("GET", "/api/workflows?created_by=Shadow")
    print_result("查询 Shadow 创建的工作流", result)

    print("\n✅ 场景 7 完成！")


def main():
    """运行所有测试场景"""
    print("\n" + "=" * 60)
    print("  AI 工作流系统 - 综合测试套件")
    print("=" * 60)
    print("\n模拟角色:")
    print("  - Shadow: 论坛维护者，创建和分配任务")
    print("  - IronGate: QA/PM，负责质量保证和产品设计")
    print("  - Forge: 开发者，负责实现功能")
    print()

    try:
        # 检查服务器连接
        result = api_call("GET", "/healthz")
        if result.get("ok"):
            print("✅ 工作流服务器连接正常")
        else:
            print("❌ 无法连接到工作流服务器")
            return
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return

    # 运行所有测试场景
    test_basic_workflow_lifecycle()
    test_reassign_workflow()
    test_unclaim_workflow()
    test_comment_discussion()
    test_blocked_workflow()
    test_error_cases()
    test_filter_and_query()

    # 总结
    print("\n" + "=" * 60)
    print("  所有测试场景执行完成！")
    print("=" * 60)
    print("\n测试覆盖:")
    print("  ✅ 基本工作流生命周期")
    print("  ✅ 工作流转派")
    print("  ✅ 释放工作流")
    print("  ✅ 评论协作")
    print("  ✅ 阻塞处理")
    print("  ✅ 错误处理")
    print("  ✅ 过滤查询")
    print("\n可以通过以下方式查看:")
    print("  - 访问 http://localhost:5678 查看工作流列表")
    print("  - 访问 http://localhost:5678/api/workflows 查看 API 数据")
    print()


if __name__ == "__main__":
    main()
