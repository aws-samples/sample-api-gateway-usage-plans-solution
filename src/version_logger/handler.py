import json
import boto3
import os
from datetime import datetime
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
version_log_table = dynamodb.Table(os.environ.get('VERSION_LOG_TABLE', 'usage-plan-version-log'))

def lambda_handler(event, context):
    """Process DynamoDB Stream events to log usage plan changes"""
    
    for record in event['Records']:
        if record['eventSource'] != 'aws:dynamodb':
            continue
            
        event_name = record['eventName']
        plan_id = record['dynamodb']['Keys']['plan_id']['S']
        timestamp = datetime.utcnow().isoformat()
        
        # Create version log entry with composite key
        log_entry = {
            'plan_id': plan_id,
            'version_timestamp': timestamp,
            'event_type': event_name,
            'timestamp': timestamp,
            'source': 'dynamodb-stream'
        }
        
        # Add old image for UPDATE and REMOVE events
        if 'OldImage' in record['dynamodb']:
            log_entry['old_values'] = convert_dynamodb_to_json(record['dynamodb']['OldImage'])
        
        # Add new image for INSERT and UPDATE events
        if 'NewImage' in record['dynamodb']:
            log_entry['new_values'] = convert_dynamodb_to_json(record['dynamodb']['NewImage'])
        
        # Determine change type and details
        if event_name == 'INSERT':
            log_entry['change_summary'] = 'Usage plan created'
        elif event_name == 'MODIFY':
            log_entry['change_summary'] = get_change_summary(
                log_entry.get('old_values', {}),
                log_entry.get('new_values', {})
            )
        elif event_name == 'REMOVE':
            log_entry['change_summary'] = 'Usage plan deleted'
        
        # Save to version log table with error handling
        try:
            version_log_table.put_item(Item=log_entry)
            print(f"Logged version change for plan {plan_id}: {log_entry['change_summary']}")
        except Exception as e:
            print(f"Error logging version change for plan {plan_id}: {str(e)}")
            # Don't fail the entire function if logging fails
            continue
    
    return {'statusCode': 200, 'processed': len(event['Records'])}

def convert_dynamodb_to_json(dynamodb_item):
    """Convert DynamoDB item format to regular JSON"""
    result = {}
    
    for key, value in dynamodb_item.items():
        if 'S' in value:
            result[key] = value['S']
        elif 'N' in value:
            result[key] = Decimal(value['N'])
        elif 'BOOL' in value:
            result[key] = value['BOOL']
        elif 'L' in value:
            result[key] = [convert_dynamodb_to_json({'item': item})['item'] for item in value['L']]
        elif 'M' in value:
            result[key] = convert_dynamodb_to_json(value['M'])
        elif 'NULL' in value:
            result[key] = None
    
    return result

def get_change_summary(old_values, new_values):
    """Generate human-readable summary of changes"""
    changes = []
    
    # Check for lifecycle state changes
    if old_values.get('lifecycle_state') != new_values.get('lifecycle_state'):
        changes.append(f"State: {old_values.get('lifecycle_state')} → {new_values.get('lifecycle_state')}")
    
    # Check for rate limit changes
    if old_values.get('rate_limit') != new_values.get('rate_limit'):
        changes.append(f"Rate limit: {old_values.get('rate_limit')} → {new_values.get('rate_limit')}")
    
    # Check for burst limit changes
    if old_values.get('burst_limit') != new_values.get('burst_limit'):
        changes.append(f"Burst limit: {old_values.get('burst_limit')} → {new_values.get('burst_limit')}")
    
    # Check for quota changes
    if old_values.get('quota_limit') != new_values.get('quota_limit'):
        changes.append(f"Quota: {old_values.get('quota_limit')} → {new_values.get('quota_limit')}")
    
    # Check for tier changes
    if old_values.get('tier') != new_values.get('tier'):
        changes.append(f"Tier: {old_values.get('tier')} → {new_values.get('tier')}")
    
    return '; '.join(changes) if changes else 'Minor updates'