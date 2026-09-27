import requests
import json
import time

BASE_URL = "http://127.0.0.1:8765"

print("=== 创建测试任务 ===")
task_data = {
    "task_type": "process_generation",
    "params": {"material": "45钢", "part_type": "轴类"},
}

try:
    response = requests.post(f"{BASE_URL}/api/v1/tasks", json=task_data)
    print(f"状态码: {response.status_code}")
    result = response.json()
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if result.get("code") == 0 or result.get("code") == 200:
        task_id = result["data"]["task_id"]
        print(f"\n任务ID: {task_id}")

        time.sleep(3)

        print("\n=== 获取任务Traces ===")
        traces_response = requests.get(f"{BASE_URL}/api/v1/traces/{task_id}")
        print(f"状态码: {traces_response.status_code}")
        print(json.dumps(traces_response.json(), indent=2, ensure_ascii=False))

        print("\n=== 获取SOTA指标 ===")
        sota_response = requests.get(f"{BASE_URL}/api/v1/traces/{task_id}/sota")
        print(f"状态码: {sota_response.status_code}")
        print(json.dumps(sota_response.json(), indent=2, ensure_ascii=False))

        print("\n=== 获取Mermaid DAG图 ===")
        mermaid_response = requests.get(f"{BASE_URL}/api/v1/traces/{task_id}/mermaid")
        print(f"状态码: {mermaid_response.status_code}")
        print(json.dumps(mermaid_response.json(), indent=2, ensure_ascii=False))

        print("\n=== 获取演化链 ===")
        chain_response = requests.get(f"{BASE_URL}/api/v1/traces/{task_id}/chain")
        print(f"状态码: {chain_response.status_code}")
        print(json.dumps(chain_response.json(), indent=2, ensure_ascii=False))

        print("\n=== 获取分支 ===")
        branches_response = requests.get(f"{BASE_URL}/api/v1/traces/{task_id}/branches")
        print(f"状态码: {branches_response.status_code}")
        print(json.dumps(branches_response.json(), indent=2, ensure_ascii=False))

except Exception as e:
    print(f"请求失败: {str(e)}")
