import boto3
import json
import os
import decimal
from datetime import datetime

# Helper class to convert Decimal to float for JSON serialization
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o) if o % 1 else int(o)
        return super(DecimalEncoder, self).default(o)

# Initialize clients
apigateway = boto3.client('apigateway')
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')

# Get environment variables
USAGE_PLANS_TABLE = os.environ['USAGE_PLANS_TABLE']
NOTIFICATIONS_TOPIC = os.environ['NOTIFICATIONS_TOPIC']

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event, cls=DecimalEncoder)}")
    
    # Extract details from the CloudTrail event
    detail = event.get('detail', {})
    
    # Check if this is a DeleteUsagePlan event
    if detail.get('eventName') != 'DeleteUsagePlan':
        print("Not a DeleteUsagePlan event, ignoring")
        return
    
    # Extract the usage plan ID that was deleted
    request_params = detail.get('requestParameters', {})
    deleted_plan_id = request_params.get('usagePlanId')
    
    if not deleted_plan_id:
        print("Could not determine deleted usage plan ID")
        return
    
    print(f"Processing deletion of usage plan: {deleted_plan_id}")
    
    # Get the usage plan metadata from DynamoDB
    table = dynamodb.Table(USAGE_PLANS_TABLE)
    response = table.get_item(Key={'plan_id': deleted_plan_id})
    
    if 'Item' not in response:
        print(f"No metadata found for usage plan {deleted_plan_id}")
        return
    
    plan_metadata = response['Item']
    print(f"Retrieved metadata: {json.dumps(plan_metadata, cls=DecimalEncoder)}")
    
      
    # Send initial notification about the deletion
    send_deletion_notification(deleted_plan_id, plan_metadata)
    
    # Recreate the usage plan with a new ID
    new_plan_id = recreate_usage_plan(plan_metadata)
    
    if new_plan_id:
        # Update the DynamoDB table with the new ID
        update_dynamodb_record(deleted_plan_id, new_plan_id, plan_metadata)
        
        # Send a second notification with recovery details
        send_deletion_notification(deleted_plan_id, plan_metadata, new_plan_id)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Usage plan recreated successfully',
                'oldPlanId': deleted_plan_id,
                'newPlanId': new_plan_id
            }, cls=DecimalEncoder)
        }
    else:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Failed to recreate usage plan',
                'oldPlanId': deleted_plan_id
            }, cls=DecimalEncoder)
        }

def send_deletion_notification(plan_id, metadata, new_plan_id=None):
    """Send SNS notification about the deleted usage plan"""
    try:
        message = {
            'event': 'USAGE_PLAN_DELETED',
            'timestamp': datetime.utcnow().isoformat(),
            'plan_id': plan_id,
            'plan_name': metadata.get('name', 'Unknown'),
            'tier': metadata.get('tier', 'Unknown')
        }
        
        # Add recovery information if available
        if new_plan_id:
            message['new_plan_id'] = new_plan_id
            message['message'] = f"Usage plan {plan_id} ({metadata.get('name', 'Unknown')}) was deleted and has been recreated with ID {new_plan_id}"
            message['status'] = 'RECOVERED'
            
            # Add details about what was recovered
            recovery_details = {
                'rate_limit': metadata.get('rate_limit', 'N/A'),
                'burst_limit': metadata.get('burst_limit', 'N/A'),
                'quota_limit': metadata.get('quota_limit', 'N/A'),
                'quota_period': metadata.get('quota_period', 'N/A')
            }
            
            # Count associated stages
            stage_count = 0
            if 'stages' in metadata and isinstance(metadata['stages'], list):
                stage_count += len(metadata['stages'])
                
            recovery_details['associated_stages'] = stage_count
            message['recovery_details'] = recovery_details
        else:
            message['message'] = f"Usage plan {plan_id} ({metadata.get('name', 'Unknown')}) was deleted and will be recreated"
            message['status'] = 'PENDING_RECOVERY'
        
        sns.publish(
            TopicArn=NOTIFICATIONS_TOPIC,
            Subject=f"Usage Plan Deleted: {plan_id}",
            Message=json.dumps(message, indent=2, cls=DecimalEncoder)
        )
        print(f"Sent deletion notification for plan {plan_id}")
    except Exception as e:
        print(f"Error sending notification: {str(e)}")

def recreate_usage_plan(metadata):
    """Recreate the usage plan with a new ID"""
    try:
        # Create a new usage plan with the same settings
        create_params = {
            'name': metadata.get('name', 'Recreated-Plan'),
            'description': f"Recreated from deleted plan. Original description: {metadata.get('description', 'No description')}",
        }
        
        # Add throttle settings if they exist
        if 'rate_limit' in metadata and 'burst_limit' in metadata:
            create_params['throttle'] = {
                'rateLimit': float(metadata['rate_limit']),
                'burstLimit': int(metadata['burst_limit'])
            }
        
        # Add quota settings if they exist
        if 'quota_limit' in metadata and 'quota_period' in metadata:
            create_params['quota'] = {
                'limit': int(metadata['quota_limit']),
                'period': metadata['quota_period']
            }
        
        # Create the new usage plan
        response = apigateway.create_usage_plan(**create_params)
        new_plan_id = response['id']
        print(f"Created new usage plan with ID: {new_plan_id}")
        
        # Associate API stages from DynamoDB metadata
        try:
            # Extract stage information from DynamoDB metadata
            api_stages = []
            
            # Check all possible formats of stage information in DynamoDB
            # Format: 'stages' field with ARN strings
            if 'stages' in metadata and isinstance(metadata['stages'], list):
                for stage_arn in metadata['stages']:
                    try:
                        # Parse the stage ARN to get the API ID and stage name
                        parts = stage_arn.split('/')
                        if len(parts) >= 4:
                            api_id = parts[2]
                            stage_name = parts[4]
                            api_stages.append({'apiId': api_id, 'stage': stage_name})
                            print(f"Extracted stage from ARN: {api_id}:{stage_name}")
                    except Exception as e:
                        print(f"Error parsing stage ARN {stage_arn}: {str(e)}")
            
            # Associate all stages with the new usage plan
            associated_stages = 0
            for stage in api_stages:
                api_id = stage['apiId']
                stage_name = stage['stage']
                
                try:
                    # Associate the stage with the new usage plan
                    apigateway.update_usage_plan(
                        usagePlanId=new_plan_id,
                        patchOperations=[
                            {
                                'op': 'add',
                                'path': '/apiStages',
                                'value': f"{api_id}:{stage_name}"
                            }
                        ]
                    )
                    print(f"Successfully associated API stage {api_id}:{stage_name} with new plan")
                    associated_stages += 1
                except Exception as e:
                    print(f"Error associating stage {api_id}:{stage_name}: {str(e)}")
            
            print(f"Associated {associated_stages} stages with the new usage plan")
            
        except Exception as e:
            print(f"Error associating API stages: {str(e)}")
        
        return new_plan_id
    except Exception as e:
        print(f"Error recreating usage plan: {str(e)}")
        return None

def update_dynamodb_record(old_plan_id, new_plan_id, metadata):
    """Update the DynamoDB record with the new plan ID"""
    try:
        table = dynamodb.Table(USAGE_PLANS_TABLE)
        
        # Create a new record with the new plan ID
        new_metadata = dict(metadata)
        new_metadata['plan_id'] = new_plan_id
        new_metadata['recreated_from'] = old_plan_id
        new_metadata['recreated_at'] = datetime.utcnow().isoformat()
        
        # Put the new record
        table.put_item(Item=new_metadata)
        print(f"Created new DynamoDB record for plan {new_plan_id}")
        
        # Update the old record to mark it as deleted and recreated
        table.update_item(
            Key={'plan_id': old_plan_id},
            UpdateExpression="SET deleted = :deleted, recreated_as = :new_id, deleted_at = :timestamp",
            ExpressionAttributeValues={
                ':deleted': True,
                ':new_id': new_plan_id,
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        print(f"Updated old DynamoDB record for plan {old_plan_id}")
        
        return True
    except Exception as e:
        print(f"Error updating DynamoDB: {str(e)}")
        return False