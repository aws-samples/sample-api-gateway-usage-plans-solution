#!/usr/bin/env python3
import requests
import json
import sys

def test_create_usage_plan():
    """Test POST /usage-plans"""
    url = f"{BASE_URL}/usage-plans"
    payload = {
        "name": "Test Usage Plan Tier",
        "description": "Test Usage Plan tier with maximum limits", 
        "tier": "Test tier",
        "rate_limit": 500,
        "burst_limit": 100,
        "quota_limit": 100,
        "quota_period": "MONTH"
    }
    
    response = requests.post(url, json=payload, timeout=30)
    print(f"\nPOST /usage-plans: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code == 200:
        return response.json().get('plan_id')
    return None

def test_get_usage_plan(plan_id):
    """Test GET /usage-plans/{id}"""
    url = f"{BASE_URL}/usage-plans/{plan_id}"
    response = requests.get(url, timeout=30)
    print(f"\nGET /usage-plans/{plan_id}: {response.status_code}")
    print(f"Response: {response.text}")

def test_update_usage_plan(plan_id):
    """Test PUT /usage-plans/{id}"""
    url = f"{BASE_URL}/usage-plans/{plan_id}"
    payload = {
        "rate_limit": 150,
        "burst_limit": 300,
        "quota_limit": 2000,
        "stages": [STAGE_ARN]
    }
    
    response = requests.put(url, json=payload, timeout=30)
    print(f"\nPUT /usage-plans/{plan_id}: {response.status_code}")
    print(f"Response: {response.text}")

def test_get_lifecycle(plan_id):
    """Test GET /lifecycle/{id}"""
    url = f"{BASE_URL}/lifecycle/{plan_id}"
    response = requests.get(url, timeout=30)
    print(f"\nGET /lifecycle/{plan_id}: {response.status_code}")
    print(f"Response: {response.text}")

def test_update_lifecycle(plan_id):
    """Test POST /lifecycle/{id}"""
    url = f"{BASE_URL}/lifecycle/{plan_id}"
    payload = {
        "action": "deprecate",
        "reason": "Plan no longer accepting new customers"
    }
    
    response = requests.post(url, json=payload, timeout=30)
    print(f"\nPOST /lifecycle/{plan_id}: {response.status_code}")
    print(f"Response: {response.text}")

def test_associate_specific_stage(plan_id):
    """Test associating a specific stage with a usage plan"""
    url = f"{BASE_URL}/usage-plans/{plan_id}"
    payload = {
        "stages": [
            STAGE_ARN
        ]
    }
    
    response = requests.put(url, json=payload, timeout=30)
    print(f"\nPUT /usage-plans/{plan_id} (associate stage): {response.status_code}")
    print(f"Response: {response.text}")

def main():
    if len(sys.argv) > 1:
        global BASE_URL, STAGE_ARN
        BASE_URL = sys.argv[1]
        STAGE_ARN = sys.argv[2]
    
    print(f"Testing API at: {BASE_URL}")
    
    # Create usage plan
    plan_id = test_create_usage_plan()
    if not plan_id:
        print("Failed to create usage plan. Exiting.")
        return
    # Test associating a specific stage with a usage plan
    test_associate_specific_stage(plan_id)
    # Test all endpoints
    test_get_usage_plan(plan_id)
    
    test_get_lifecycle(plan_id)
    
    # Test deprecating the plan
    #test_update_lifecycle(plan_id)


if __name__ == "__main__":
    main()