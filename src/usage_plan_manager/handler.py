import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
apigateway = boto3.client('apigateway')
table = dynamodb.Table(os.environ['USAGE_PLANS_TABLE'])

def lambda_handler(event, context):
    """UC1.1: Tiered Usage Plan Creation and Management"""
    
    if 'httpMethod' in event:
        return handle_api_request(event)
        
    return {'statusCode': 400, 'body': 'Invalid action'}

def handle_api_request(event):
    """Handle API Gateway requests"""
    method = event['httpMethod']
    path = event['path']
    
    if method == 'POST' and path == '/usage-plans':
        body = json.loads(event['body'])
        return create_usage_plan(body)
    elif method == 'GET' and '/usage-plans/' in path:
        plan_id = event['pathParameters']['planId']
        return get_usage_plan(plan_id)
    elif method == 'PUT' and '/usage-plans/' in path:
        plan_id = event['pathParameters']['planId']
        body = json.loads(event['body'])
        return update_usage_plan(plan_id, body)
    
    return {'statusCode': 404, 'body': 'Not found'}

def create_usage_plan(plan_data):
    """Create usage plan in API Gateway and store metadata in DynamoDB"""
    
    # Create in API Gateway
    response = apigateway.create_usage_plan(
        name=plan_data['name'],
        description=plan_data.get('description', ''),
        throttle={
            'rateLimit': plan_data['rate_limit'],
            'burstLimit': plan_data['burst_limit']
        },
        quota={
            'limit': plan_data['quota_limit'],
            'period': plan_data['quota_period']
        }
    )
    
    # Store metadata in DynamoDB
    table.put_item(Item={
        'plan_id': response['id'],
        'name': plan_data['name'],
        'tier': plan_data['tier'],
        'rate_limit': plan_data['rate_limit'],
        'burst_limit': plan_data['burst_limit'],
        'quota_limit': plan_data['quota_limit'],
        'quota_period': plan_data['quota_period'],
        'lifecycle_state': 'Active',
        'stages': plan_data.get('stages', []),
        'description': plan_data.get('description', ''),
        'created_at': datetime.utcnow().isoformat()
    })
    
    return {
        'statusCode': 200,
        'body': json.dumps({'plan_id': response['id'], 'status': 'created'})
    }

def update_usage_plan(plan_id, plan_data):
    """Update existing usage plan"""
    
    # Update DynamoDB conditionally
    if any(key in plan_data for key in ['rate_limit', 'burst_limit', 'quota_limit']):
        # Update both limits and stages
        table.update_item(
            Key={'plan_id': plan_id},
            UpdateExpression='SET rate_limit = :rl, burst_limit = :bl, quota_limit = :ql, updated_at = :ua',
            ExpressionAttributeValues={
                ':rl': plan_data['rate_limit'],
                ':bl': plan_data['burst_limit'], 
                ':ql': plan_data['quota_limit'],
                ':ua': datetime.utcnow().isoformat()
            },
            ConditionExpression='attribute_exists(plan_id)',
            ReturnValues='UPDATED_NEW'
        )

    if 'stages' in plan_data:
        # Update only stages
        table.update_item(
            Key={'plan_id': plan_id},
            UpdateExpression='SET stages = :stages, updated_at = :ua',
            ExpressionAttributeValues={
                ':stages': plan_data['stages'],
                ':ua': datetime.utcnow().isoformat()
            },
            ConditionExpression='attribute_exists(plan_id)',
            ReturnValues='UPDATED_NEW'
        )
    
    patch_ops = []
    
    # Only add limit patch ops if limits are provided
    if any(key in plan_data for key in ['rate_limit', 'burst_limit', 'quota_limit']):
        patch_ops = [
            {'op': 'replace', 'path': '/throttle/rateLimit', 'value': str(plan_data['rate_limit'])},
            {'op': 'replace', 'path': '/throttle/burstLimit', 'value': str(plan_data['burst_limit'])},
            {'op': 'replace', 'path': '/quota/limit', 'value': str(plan_data['quota_limit'])}
        ]
    
    # Update API Gateway limits if provided
    if patch_ops:
        apigateway.update_usage_plan(usagePlanId=plan_id, patchOperations=patch_ops)
    
    # Handle stage associations separately
    if 'stages' in plan_data:
        for stage_arn in plan_data['stages']:
            api_id, stage_name = stage_arn.split('/')[-3], stage_arn.split('/')[-1]
            apigateway.update_usage_plan(
                usagePlanId=plan_id,
                patchOperations=[
                    {
                        'op': 'add',
                        'path': '/apiStages',
                        'value': f'{api_id}:{stage_name}'
                    }
                ]
            )
    return {'statusCode': 200, 'body': json.dumps({'status': 'updated'})}

def get_usage_plan(plan_id):
    """Get usage plan metadata"""
    
    response = table.get_item(Key={'plan_id': plan_id})
    
    if 'Item' not in response:
        return {'statusCode': 404, 'body': 'Plan not found'}
    
    return {
        'statusCode': 200,
        'body': json.dumps(response['Item'], default=str)
    }