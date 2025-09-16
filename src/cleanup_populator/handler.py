import json
import boto3
import time

cloudformation = boto3.client('cloudformation')

def lambda_handler(event, context):
    """Cleanup Lambda to remove data populator after successful completion"""
    
    try:
        # Extract stack name from the event (triggered by SNS or EventBridge)
        stack_name = event.get('stack_name') or event['Records'][0]['Sns']['Message']
        
        print(f"Starting cleanup for stack: {stack_name}")
        
        # Wait to ensure CloudFormation has fully processed the custom resource
        time.sleep(60)
        
        # Update stack to remove data populator
        response = cloudformation.update_stack(
            StackName=stack_name,
            UsePreviousTemplate=True,
            Parameters=[
                {
                    'ParameterKey': 'EnableDataPopulator',
                    'ParameterValue': 'false'
                }
            ],
            Capabilities=['CAPABILITY_IAM']
        )
        
        print(f"Successfully initiated cleanup: {response['StackId']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Cleanup initiated successfully',
                'stackId': response['StackId']
            })
        }
        
    except Exception as e:
        print(f"Error during cleanup: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }
