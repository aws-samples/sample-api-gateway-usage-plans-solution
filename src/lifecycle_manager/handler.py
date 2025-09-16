import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
apigateway = boto3.client('apigateway')
sns = boto3.client('sns')
table = dynamodb.Table(os.environ['USAGE_PLANS_TABLE'])

def lambda_handler(event, context):
    """UC1.2: Usage Plan Lifecycle Management"""
    
    # Handle API Gateway events
    if 'httpMethod' in event:
        return handle_lifecycle_api_request(event)
    
    # Handle direct Lambda invocation
    action = event.get('action')
    plan_id = event.get('plan_id')
    
    if action == 'deprecate':
        return deprecate_plan(plan_id)
    elif action == 'get_lifecycle_state':
        return get_lifecycle_state(plan_id)
    
    return {'statusCode': 400, 'body': 'Invalid action'}

def handle_lifecycle_api_request(event):
    """Handle API Gateway requests for lifecycle management"""
    method = event['httpMethod']
    plan_id = event['pathParameters']['planId']
    
    if method == 'GET':
        return get_lifecycle_state(plan_id)
    elif method == 'POST':
        body = json.loads(event['body'])
        action = body.get('action')
        
        if action == 'deprecate':
            return deprecate_plan(plan_id)
    
    return {'statusCode': 400, 'body': 'Invalid action'}

def deprecate_plan(plan_id):
    """Deprecate usage plan - prevents new subscriptions"""
    
    # Update lifecycle state
    table.update_item(
        Key={'plan_id': plan_id},
        UpdateExpression='SET lifecycle_state = :state, deprecated_at = :da',
        ExpressionAttributeValues={
            ':state': 'Deprecated',
            ':da': datetime.utcnow().isoformat()
        }
    )
    
    # Send notification
    sns.publish(
        TopicArn=os.environ['NOTIFICATIONS_TOPIC'],
        Message=json.dumps({
            'event': 'plan_deprecated',
            'plan_id': plan_id,
            'timestamp': datetime.utcnow().isoformat()
        }),
        Subject=f'Usage Plan {plan_id} Deprecated'
    )
    
    return {'statusCode': 200, 'body': json.dumps({'status': 'deprecated'})}



def get_lifecycle_state(plan_id):
    """Get current lifecycle state of usage plan"""
    
    response = table.get_item(Key={'plan_id': plan_id})
    
    if 'Item' not in response:
        return {'statusCode': 404, 'body': 'Plan not found'}
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'plan_id': plan_id,
            'lifecycle_state': response['Item']['lifecycle_state'],
            'created_at': response['Item'].get('created_at'),
            'deprecated_at': response['Item'].get('deprecated_at')
        })
    }