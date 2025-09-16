import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
apigateway = boto3.client('apigateway')
sns = boto3.client('sns')
table = dynamodb.Table(os.environ['USAGE_PLANS_TABLE'])

def lambda_handler(event, context):
    """Configuration Enforcement - Monitor and correct usage plan drift"""
    
    # Handle CloudTrail events for usage plan changes
    if 'source' in event and event['source'] == 'aws.apigateway':
        return handle_usage_plan_change(event)
    
    # Handle direct invocation for specific plan
    plan_id = event.get('plan_id')
    if plan_id:
        return enforce_configuration_for_plan(plan_id)
    
    return {'statusCode': 400, 'body': 'Invalid event'}

def handle_usage_plan_change(event):
    """Handle usage plan configuration changes from CloudTrail"""
    
    detail = event.get('detail', {})
    event_name = detail.get('eventName')
    
    if event_name not in ['UpdateUsagePlan', 'CreateUsagePlan', 'DeleteUsagePlan']:
        return {'statusCode': 200, 'body': 'Event not relevant'}
    
    # Extract usage plan ID from CloudTrail event
    response_elements = detail.get('responseElements', {})
    plan_id = response_elements.get('id')
    
    if not plan_id:
        return {'statusCode': 400, 'body': 'No usage plan ID found'}
    
    # CreateUsagePlan events, check if it's unmanaged
    if event_name == 'CreateUsagePlan':
        check_if_unmanaged_plan(plan_id)
        return enforce_configuration_for_plan(plan_id)
    
    # DeleteUsagePlan events, check if it was a managed plan
    if event_name == 'DeleteUsagePlan':
        return handle_usage_plan_deletion(plan_id)
    
    return enforce_configuration_for_plan(plan_id)

def enforce_configuration_for_plan(plan_id):
    """Enforce correct configuration for a specific usage plan"""
    
    # Get governance record from DynamoDB
    try:
        response = table.get_item(Key={'plan_id': plan_id}, ConsistentRead=True)
        if 'Item' not in response:
            # Plan exists in API Gateway but not in governance table - it's unmanaged
            try:
                plan_details = apigateway.get_usage_plan(usagePlanId=plan_id)
                unmanaged_plan = {
                    'id': plan_details['id'],
                    'name': plan_details.get('name', 'Unnamed'),
                    'description': plan_details.get('description', ''),
                    'apiStages': plan_details.get('apiStages', []),
                    'throttle': plan_details.get('throttle', {}),
                    'quota': plan_details.get('quota', {})
                }
                apigateway.delete_usage_plan(usagePlanId=plan_id)
            except Exception:
                pass  # Plan might have been deleted
            return {'statusCode': 404, 'body': f'No governance record for plan {plan_id}'}
        
        governance_record = response['Item']
    except Exception as e:
        return {'statusCode': 500, 'body': f'Error reading governance record: {str(e)}'}
    
    # Get current API Gateway configuration
    try:
        current_config = apigateway.get_usage_plan(usagePlanId=plan_id)
    except Exception as e:
        return {'statusCode': 500, 'body': f'Error reading API Gateway config: {str(e)}'}
    
    # Compare and identify drift
    drift_detected = []
    corrections_needed = []
    
    # Check rate limit
    current_rate = current_config.get('throttle', {}).get('rateLimit', 0)
    expected_rate = int(governance_record['rate_limit'])
    if current_rate != expected_rate:
        drift_detected.append(f'Rate limit: {current_rate} != {expected_rate}')
        corrections_needed.append({'op': 'replace', 'path': '/throttle/rateLimit', 'value': str(expected_rate)})
    
    # Check burst limit
    current_burst = current_config.get('throttle', {}).get('burstLimit', 0)
    expected_burst = int(governance_record['burst_limit'])
    if current_burst != expected_burst:
        drift_detected.append(f'Burst limit: {current_burst} != {expected_burst}')
        corrections_needed.append({'op': 'replace', 'path': '/throttle/burstLimit', 'value': str(expected_burst)})
    
    # Check quota limit
    current_quota = current_config.get('quota', {}).get('limit', 0)
    expected_quota = int(governance_record['quota_limit'])
    if current_quota != expected_quota:
        drift_detected.append(f'Quota limit: {current_quota} != {expected_quota}')
        corrections_needed.append({'op': 'replace', 'path': '/quota/limit', 'value': str(expected_quota)})
    
    # Apply corrections if drift detected
    if corrections_needed:
        try:
            apigateway.update_usage_plan(
                usagePlanId=plan_id,
                patchOperations=corrections_needed
            )
            
            # Send notification about drift correction
            send_drift_correction_notification(plan_id, governance_record, drift_detected)
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'plan_id': plan_id,
                    'action': 'configuration_corrected',
                    'drift_detected': drift_detected,
                    'corrections_applied': len(corrections_needed)
                })
            }
            
        except Exception as e:
            # Send alert about failed correction
            send_correction_failure_notification(plan_id, governance_record, str(e))
            return {'statusCode': 500, 'body': f'Failed to apply corrections: {str(e)}'}
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'plan_id': plan_id,
            'action': 'no_drift_detected',
            'configuration': 'compliant'
        })
    }

def send_drift_correction_notification(plan_id, governance_record, drift_detected):
    """Send notification when configuration drift is corrected"""
    
    message = {
        'event': 'configuration_drift_corrected',
        'plan_id': plan_id,
        'plan_name': governance_record['name'],
        'drift_detected': drift_detected,
        'corrected_at': datetime.utcnow().isoformat(),
        'action_taken': 'Automatically restored to governance configuration'
    }
    
    sns.publish(
        TopicArn=os.environ['NOTIFICATIONS_TOPIC'],
        Message=json.dumps(message),
        Subject=f'Configuration Drift Corrected - {governance_record["name"]}'
    )

def send_correction_failure_notification(plan_id, governance_record, error):
    """Send alert when automatic correction fails"""
    
    message = {
        'event': 'configuration_correction_failed',
        'plan_id': plan_id,
        'plan_name': governance_record['name'],
        'error': error,
        'timestamp': datetime.utcnow().isoformat(),
        'action_required': 'Manual intervention needed to restore compliance'
    }
    
    sns.publish(
        TopicArn=os.environ['NOTIFICATIONS_TOPIC'],
        Message=json.dumps(message),
        Subject=f'ALERT: Configuration Correction Failed - {governance_record["name"]}'
    )

def check_if_unmanaged_plan(plan_id):
    """Check if a newly created usage plan is unmanaged"""
    
    try:
        # Check if plan exists in governance table
        response = table.get_item(Key={'plan_id': plan_id})
        if 'Item' not in response:
            # Plan is unmanaged, get details and send notification
            plan_details = apigateway.get_usage_plan(usagePlanId=plan_id)
            unmanaged_plan = {
                'id': plan_details['id'],
                'name': plan_details.get('name', 'Unnamed'),
                'description': plan_details.get('description', ''),
                'apiStages': plan_details.get('apiStages', []),
                'throttle': plan_details.get('throttle', {}),
                'quota': plan_details.get('quota', {})
            }
            send_unmanaged_plans_notification([unmanaged_plan])
    except Exception as e:
        print(f'Error checking unmanaged plan {plan_id}: {str(e)}')

def send_unmanaged_plans_notification(unmanaged_plans):
    """Send notification about unmanaged usage plans"""
    
    message = {
        'event': 'unmanaged_usage_plans_detected',
        'count': len(unmanaged_plans),
        'plans': unmanaged_plans,
        'detected_at': datetime.utcnow().isoformat(),
        'action_required': 'Review and add to governance table. Usage plan removed automatically'
    }
    
    sns.publish(
        TopicArn=os.environ['NOTIFICATIONS_TOPIC'],
        Message=json.dumps(message, indent=2),
        Subject=f'DRIFT ALERT: {len(unmanaged_plans)} Unmanaged Usage Plans Detected'
    )

def handle_usage_plan_deletion(plan_id):
    """Handle deletion of usage plans and detect unauthorized deletions"""
    
    try:
        # Check if deleted plan was managed
        response = table.get_item(Key={'plan_id': plan_id})
        if 'Item' in response:
            governance_record = response['Item']
            send_managed_plan_deletion_notification(plan_id, governance_record)
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'plan_id': plan_id,
                    'action': 'managed_plan_deleted',
                    'alert_sent': True
                })
            }
        else:
            # Unmanaged plan was deleted - just log it
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'plan_id': plan_id,
                    'action': 'unmanaged_plan_deleted',
                    'alert_sent': False
                })
            }
    except Exception as e:
        return {'statusCode': 500, 'body': f'Error handling deletion: {str(e)}'}

def send_managed_plan_deletion_notification(plan_id, governance_record):
    """Send notification when a managed usage plan is deleted"""
    
    message = {
        'event': 'managed_usage_plan_deleted',
        'plan_id': plan_id,
        'plan_name': governance_record['name'],
        'tier': governance_record.get('tier', 'Unknown'),
        'deleted_at': datetime.utcnow().isoformat(),
        'action_required': 'Verify if deletion was authorized and update governance records'
    }
    
    sns.publish(
        TopicArn=os.environ['NOTIFICATIONS_TOPIC'],
        Message=json.dumps(message, indent=2),
        Subject=f'DRIFT ALERT: Managed Usage Plan Deleted - {governance_record["name"]}'
    )