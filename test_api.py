"""快速测试 API 是否正常"""
import requests
import json

BASE_URL = "http://localhost:8000"

print("=" * 60)
print("测试 API 接口")
print("=" * 60)

# 测试 1: 健康检查
print("\n1. 测试健康检查...")
try:
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    print(f"   状态码: {resp.status_code}")
    print(f"   响应: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
except Exception as e:
    print(f"   ❌ 错误: {e}")

# 测试 2: 获取概览统计
print("\n2. 测试概览统计...")
try:
    resp = requests.get(f"{BASE_URL}/api/logs/overview", timeout=5)
    print(f"   状态码: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        if 'data' in data:
            stats = data['data']
            print(f"   总日志数: {stats.get('total', 0)}")
            print(f"   请求数: {stats.get('request_count', 0)}")
            print(f"   级别分布: {stats.get('level_distribution', {})}")
        else:
            print(f"   响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
    else:
        print(f"   ❌ 响应: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
except Exception as e:
    print(f"   ❌ 错误: {e}")

# 测试 3: 获取日志列表
print("\n3. 测试日志列表...")
try:
    resp = requests.get(f"{BASE_URL}/api/logs/list?page=1&page_size=5", timeout=5)
    print(f"   状态码: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        if 'data' in data and 'data' in data['data']:
            list_data = data['data']
            print(f"   总数: {list_data.get('total', 0)}")
            print(f"   当前页: {list_data.get('page', 0)}")
            print(f"   数据条数: {len(list_data.get('data', []))}")
        else:
            print(f"   响应: {json.dumps(data, ensure_ascii=False, indent=2)[:200]}...")
    else:
        print(f"   ❌ 响应: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")
except Exception as e:
    print(f"   ❌ 错误: {e}")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
