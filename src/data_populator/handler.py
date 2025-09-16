import json
import boto3
import urllib3
import urllib.parse
import time
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
apigateway = boto3.client('apigateway')

SAMPLE_DATA = [
    {
        "plan_id": "enterprise-tier-001",
        "name": "Enterprise Tier",
        "tier": "Enterprise",
        "rate_limit": 1000,
        "burst_limit": 2000,
        "quota_limit": 1000000,
        "quota_period": "MONTH",
        "lifecycle_state": "Active",
        "stages": [],
        "description": "Enterprise tier with maximum limits",


        "created_at": "2024-01-15T10:45:00Z"
    },
    {
        "plan_id": "free-tier-001",
        "name": "Free Tier",
        "tier": "Free",
        "rate_limit": 10,
        "burst_limit": 20,
        "quota_limit": 1000,
        "quota_period": "MONTH",
        "lifecycle_state": "Active",
        "stages": [],
        "description": "Free tier with basic limits",


        "created_at": "2024-01-15T10:30:00Z"
    },
    {
        "plan_id": "basic-tier-001",
        "name": "Basic Tier",
        "tier": "Basic",
        "rate_limit": 50,
        "burst_limit": 100,
        "quota_limit": 10000,
        "quota_period": "MONTH",
        "lifecycle_state": "Active",
        "stages": [],
        "description": "Basic tier for small businesses",


        "created_at": "2024-01-15T10:35:00Z"
    },
    {
        "plan_id": "premium-tier-001",
        "name": "Premium Tier",
        "tier": "Premium",
        "rate_limit": 200,
        "burst_limit": 400,
        "quota_limit": 100000,
        "quota_period": "MONTH",
        "lifecycle_state": "Active",
        "stages": [],
        "description": "Premium tier with enhanced limits",


        "created_at": "2024-01-15T10:40:00Z"
    },
    {
        "plan_id": "legacy-tier-001",
        "name": "Legacy Tier",
        "tier": "Legacy",
        "rate_limit": 25,
        "burst_limit": 50,
        "quota_limit": 5000,
        "quota_period": "MONTH",
        "lifecycle_state": "Deprecated",
        "stages": [],
        "description": "Legacy tier - deprecated",


        "created_at": "2023-06-01T08:00:00Z",
        "deprecated_at": "2024-01-01T00:00:00Z"
    }
]

def lambda_handler(event, context):
    """CloudFormation custom resource handler to populate DynamoDB"""
    print(event)
    request_type = event['RequestType']
    table_name = event['ResourceProperties']['TableName']
    test_api_id = event['ResourceProperties']['LifecycleAPIId']
    
    # Set environment variable for populate_table function
    import os
    os.environ['TEST_API_ID'] = test_api_id
    
    try:
        if request_type == 'Create':
            populate_table(table_name)
            send_response(event, context, 'SUCCESS', {'Message': 'Data populated successfully'})
        elif request_type == 'Delete':
            # Optionally clean up data on stack deletion
            send_response(event, context, 'SUCCESS', {'Message': 'Delete completed'})
        else:
            send_response(event, context, 'SUCCESS', {'Message': 'Update completed'})
            
    except Exception as e:
        print(f"Error: {str(e)}")
        send_response(event, context, 'FAILED', {'Message': str(e)})

def populate_table(table_name):
    """Create usage plans in API Gateway and populate DynamoDB with metadata"""
    
    table = dynamodb.Table(table_name)
    
    # Get actual API Gateway ID from environment
    import os
    region = os.environ['AWS_REGION']
    api_id = os.environ['TEST_API_ID']
    api_assignation = "True"
    
    for item in SAMPLE_DATA:
        try:
            # Create usage plan in API Gateway without stages first
            usage_plan = apigateway.create_usage_plan(
                name=item['name'],
                description=item['description'],
                throttle={
                    'rateLimit': float(item['rate_limit']),
                    'burstLimit': item['burst_limit']
                },
                quota={
                    'limit': item['quota_limit'],
                    'period': item['quota_period']
                }
            )
            
            # Use the actual usage plan ID from API Gateway
            actual_plan_id = usage_plan['id']
            if api_assignation == "True":
                # Try to associate stage if API ID is available
                stage_arn = None
                if api_id != 'PLACEHOLDER':
                    stage_arn1 = f"arn:aws:apigateway:{region}::/restapis/{api_id}/stages/dev"
                    for attempt in range(3):
                        try:
                            apigateway.update_usage_plan(
                                usagePlanId=actual_plan_id,
                                patchOperations=[
                                    {
                                        'op': 'add',
                                        'path': '/apiStages',
                                        'value': f'{api_id}:dev'
                                    }
                                ]
                            )
                            print(f"Associated stage {api_id}:dev with usage plan {actual_plan_id}")
                            stage_arn2 = f"arn:aws:apigateway:{region}::/restapis/{api_id}/stages/Stage"
                            apigateway.update_usage_plan(
                                usagePlanId=actual_plan_id,
                                patchOperations=[
                                    {
                                        'op': 'add',
                                        'path': '/apiStages',
                                        'value': f'{api_id}:Stage'
                                    }
                                ]
                            )
                            print(f"Associated stage {api_id}:dev with usage plan {actual_plan_id}")
                            api_assignation = "False"
                            stage_arn = [stage_arn1, stage_arn2]
                            break
                        except Exception as stage_error:
                            if attempt < 2:
                                print(f"Attempt {attempt + 1} failed, retrying in 2 seconds...")
                                time.sleep(2)
                            else:
                                print(f"Warning: Could not associate stage after 3 attempts: {stage_error}")
                                stage_arn = None
                    
                
            # Convert integers to Decimal for DynamoDB
            for key, value in item.items():
                if isinstance(value, int):
                    item[key] = Decimal(str(value))
            
            # Update item with actual plan ID and stage ARN
            item['plan_id'] = actual_plan_id
            if stage_arn:
                item['stages'] = stage_arn
            
            table.put_item(Item=item)
            stage_arn = None
            print(f"Created usage plan: {item['name']} (ID: {actual_plan_id}) with stages: {item.get('stages', [])}")
            
        except Exception as e:
            print(f"Error creating usage plan {item['name']}: {e}")
            # Continue with next item instead of failing completely

def send_response(event, context, status, data):
    """Send response to CloudFormation"""
    
    response_body = {
        'Status': status,
        'Reason': f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data
    }

    # Validate that the ResponseURL points to an AWS domain to mitigate SSRF
    parsed_url = urllib.parse.urlparse(event['ResponseURL'])
    allowed_suffixes = [
        '.amazonaws.com',
    ]
    if not any(parsed_url.hostname and parsed_url.hostname.endswith(suffix) for suffix in allowed_suffixes):
        # Defensive: do not attempt to send response if URL is not AWS
        print(f"Blocked attempted send_response to unapproved host: {parsed_url.hostname}")
        raise ValueError(f"ResponseURL host is not allowed: {parsed_url.hostname}")

    http = urllib3.PoolManager()
    response = http.request(
        'PUT',
        event['ResponseURL'],
        body=json.dumps(response_body),
        headers={'Content-Type': 'application/json'}
    )
    
    print(f"Response status: {response.status}")