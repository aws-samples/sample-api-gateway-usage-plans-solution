import json
import boto3
import os

config_client = boto3.client('config')
dynamodb = boto3.resource('dynamodb')
apigateway = boto3.client('apigateway')
table = dynamodb.Table(os.environ['USAGE_PLANS_TABLE'])

def lambda_handler(event, context):
    """Config Rule to validate resources against usage plan metadata"""
    
    invoking_event = json.loads(event['invokingEvent'])
    
    # Handle periodic evaluation (no specific resource)
    if 'configurationItem' not in invoking_event or not invoking_event['configurationItem']:
        evaluations = evaluate_deleted_usage_plans()
        if evaluations:
            config_client.put_evaluations(
                Evaluations=evaluations,
                ResultToken=event['resultToken']
            )
        return {'statusCode': 200, 'body': f'Periodic evaluation completed: {len(evaluations)} evaluations'}
    
    configuration_item = invoking_event['configurationItem']
    
    # Skip evaluation if resource is deleted or not available
    if configuration_item['configurationItemStatus'] in ['ResourceDeleted', 'ResourceNotRecorded']:
        return {'statusCode': 200, 'body': 'Skipped deleted/unrecorded resource'}
    
    resource_type = configuration_item['resourceType']
    resource_id = configuration_item['resourceId']
    
    # Unified evaluation for both Stage and UsagePlan resources
    if resource_type in ['AWS::ApiGateway::Stage', 'AWS::ApiGateway::UsagePlan']:
        evaluation = evaluate_resource_compliance(configuration_item)
    else:
        evaluation = {
            'ComplianceResourceType': resource_type,
            'ComplianceResourceId': resource_id,
            'ComplianceType': 'NOT_APPLICABLE',
            'Annotation': f'Resource type {resource_type} not supported',
            'OrderingTimestamp': configuration_item['configurationItemCaptureTime']
        }
    
    # Submit evaluation to Config
    config_client.put_evaluations(
        Evaluations=[evaluation],
        ResultToken=event['resultToken']
    )
    
    return {'statusCode': 200, 'body': 'Evaluation completed'}

def evaluate_deleted_usage_plans():
    """Evaluate usage plans that exist in DynamoDB but not in API Gateway"""
    evaluations = []
    
    try:
        # Get all usage plans from DynamoDB
        db_response = table.scan()
        db_plans = db_response.get('Items', [])
        
        # Get all usage plans from API Gateway
        api_response = apigateway.get_usage_plans()
        api_plans = {plan.get('name'): plan.get('id') for plan in api_response.get('items', [])}
        
        print(f"Found {len(db_plans)} plans in DynamoDB, {len(api_plans)} in API Gateway")
        
        for db_plan in db_plans:
            plan_id = db_plan.get('plan_id')
            
            # Skip deleted plans that are marked as deleted in DynamoDB
            if db_plan.get('deleted', False):
                continue
                
            # Check if plan exists in API Gateway by name
            if plan_id not in api_plans:
                # Try to find by configuration match
                found_match = False
                for api_plan_name, api_plan_id in api_plans.items():
                    try:
                        api_plan_details = apigateway.get_usage_plan(usagePlanId=api_plan_id)
                        if plans_match_by_config(db_plan, api_plan_details):
                            found_match = True
                            break
                    except Exception:
                        continue
                
                if not found_match:
                    # Usage plan exists in DynamoDB but not in API Gateway
                    evaluation = {
                        'ComplianceResourceType': 'AWS::ApiGateway::UsagePlan',
                        'ComplianceResourceId': f'missing-{plan_id}',
                        'ComplianceType': 'NON_COMPLIANT',
                        'Annotation': truncate_annotation(f'Usage plan {plan_id} exists in DynamoDB but not in API Gateway'),
                        'OrderingTimestamp': '2024-01-01T00:00:00.000Z'
                    }
                    evaluations.append(evaluation)
                    print(f"Found missing usage plan: {plan_id}")
        
        return evaluations
        
    except Exception as e:
        print(f"Error evaluating deleted usage plans: {str(e)}")
        return []

def plans_match_by_config(db_plan, api_plan):
    """Check if DynamoDB plan matches API Gateway plan by configuration"""
    try:
        api_throttle = api_plan.get('throttle', {})
        api_quota = api_plan.get('quota', {})
        
        return (int(db_plan.get('rate_limit', 0)) == int(api_throttle.get('rateLimit', 0)) and
                int(db_plan.get('burst_limit', 0)) == int(api_throttle.get('burstLimit', 0)) and
                int(db_plan.get('quota_limit', 0)) == int(api_quota.get('limit', 0)))
    except Exception:
        return False

def find_usage_plan_for_stage(stage_arn):
    """Find usage plan that contains the given stage ARN"""
    
    try:
        response = table.scan()
        print(f"Scanning DynamoDB for stage ARN: {stage_arn}")
        
        for item in response['Items']:
            stages = item.get('stages', [])
            print(f"Checking plan {item.get('plan_id')}: stages = {stages}")
            if stages and stage_arn in stages:
                print(f"Found matching plan: {item['plan_id']}")
                return item
        
        print(f"No usage plan found for stage: {stage_arn}")
        return None
        
    except Exception as e:
        print(f"Error scanning DynamoDB: {str(e)}")
        return None

def verify_stage_exists(stage_arn):
    """Verify that the stage actually exists in API Gateway"""
    
    try:
        # Extract API ID and stage name from ARN format: arn:aws:apigateway:region::/restapis/api-id/stages/stage-name
        arn_parts = stage_arn.split('/')
        api_id = arn_parts[-3]
        stage_name = arn_parts[-1]
        apigateway.get_stage(restApiId=api_id, stageName=stage_name)
        return True
        
    except Exception:
        return False

def find_api_gateway_usage_plan_id(db_plan_id):
    """Find the API Gateway usage plan ID that corresponds to the DynamoDB plan_id"""
    try:
        # Get all usage plans from API Gateway
        response = apigateway.get_usage_plans()
        print(f"Looking for API Gateway usage plan matching DB plan_id: {db_plan_id}")
        
        # First try to match by name (assuming db_plan_id is used as the name in API Gateway)
        for plan in response.get('items', []):
            if plan.get('name') == db_plan_id:
                print(f"Found API Gateway usage plan by name: {plan.get('id')}")
                return plan.get('id')
        
        # If no match by name, try to match by looking up the plan in DynamoDB and comparing settings
        db_response = table.get_item(Key={'plan_id': db_plan_id})
        if 'Item' not in db_response:
            print(f"No DynamoDB record found for plan_id: {db_plan_id}")
            return None
            
        db_plan = db_response['Item']
        
        # Match by rate limit, burst limit, and quota limit
        for plan in response.get('items', []):
            api_throttle = plan.get('throttle', {})
            api_quota = plan.get('quota', {})
            
            if (int(db_plan.get('rate_limit', 0)) == int(api_throttle.get('rateLimit', 0)) and
                int(db_plan.get('burst_limit', 0)) == int(api_throttle.get('burstLimit', 0)) and
                int(db_plan.get('quota_limit', 0)) == int(api_quota.get('limit', 0))):
                print(f"Found API Gateway usage plan by settings: {plan.get('id')}")
                return plan.get('id')
        
        print(f"No matching API Gateway usage plan found for DB plan_id: {db_plan_id}")
        return None
        
    except Exception as e:
        print(f"Error finding API Gateway usage plan ID: {str(e)}")
        return None

def evaluate_resource_compliance(configuration_item):
    """Unified evaluation for Stage and UsagePlan resources"""
    
    resource_type = configuration_item['resourceType']
    resource_id = configuration_item['resourceId']
    violations = []
    
    try:
        if resource_type == 'AWS::ApiGateway::Stage':
            # Stage evaluation: check if mapped to usage plan and validate that usage plan
            stage_arn = resource_id
            
            if not verify_stage_exists(stage_arn):
                return create_evaluation_result(resource_type, resource_id, 'NOT_APPLICABLE', 
                                              f'Stage does not exist', configuration_item)
            
            # Extract API ID and stage name from ARN for direct API Gateway check
            arn_parts = stage_arn.split('/')
            api_id = arn_parts[-3]
            stage_name = arn_parts[-1]
            
            # First check if the stage is directly associated with any usage plan in API Gateway
            api_usage_plans = apigateway.get_usage_plans()
            api_plan_id = None
            
            for plan in api_usage_plans.get('items', []):
                for stage in plan.get('apiStages', []):
                    if stage.get('apiId') == api_id and stage.get('stage') == stage_name:
                        api_plan_id = plan.get('id')
                        print(f"Found API Gateway usage plan {api_plan_id} directly associated with stage {stage_arn}")
                        break
                if api_plan_id:
                    break
            
            # Find associated usage plan in DynamoDB
            db_plan = find_usage_plan_for_stage(stage_arn)
            if not db_plan:
                if api_plan_id:
                    # Stage is associated with a usage plan in API Gateway but not in DynamoDB
                    return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                                  f'Stage associated with plan {api_plan_id} in API Gateway but not in DynamoDB', 
                                                  configuration_item)
                else:
                    # Stage is not associated with any usage plan
                    return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                                  'Stage not mapped to any usage plan', 
                                                  configuration_item)
            
            # Found a plan in DynamoDB but not in API Gateway
            if not api_plan_id:
                return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                              f'Stage mapped to plan {db_plan["plan_id"]} in DynamoDB but not in API Gateway', 
                                              configuration_item)
            
            # Get the actual usage plan from API Gateway
            try:
                api_plan = apigateway.get_usage_plan(usagePlanId=api_plan_id)
                # Validate usage plan configuration
                print(f"Plan validation: DB plan={db_plan['plan_id']}, API plan={api_plan['id']}")
                plan_violations = validate_plan_configuration(db_plan, api_plan)
                if plan_violations:
                    violations.extend(plan_violations)
            except Exception as e:
                violations.append(f"Error getting API Gateway usage plan {api_plan_id}: {str(e)}")
                
        elif resource_type == 'AWS::ApiGateway::UsagePlan':
            # Usage plan evaluation: check if exists in DynamoDB and validate configuration
            usage_plan_id = resource_id
            
            # Get the usage plan from API Gateway
            try:
                api_plan = apigateway.get_usage_plan(usagePlanId=usage_plan_id)
                api_plan_name = api_plan.get('name')
                
                # Try to find the corresponding plan in DynamoDB
                response = table.get_item(Key={'plan_id': api_plan_name})
                if 'Item' not in response:
                    # If not found by name, scan the table to find a matching plan
                    db_plan = None
                    scan_response = table.scan()
                    for item in scan_response.get('Items', []):
                        if (int(item.get('rate_limit', 0)) == int(api_plan.get('throttle', {}).get('rateLimit', 0)) and
                            int(item.get('burst_limit', 0)) == int(api_plan.get('throttle', {}).get('burstLimit', 0)) and
                            int(item.get('quota_limit', 0)) == int(api_plan.get('quota', {}).get('limit', 0))):
                            db_plan = item
                            break
                    
                    if not db_plan:
                        # AUTOMATIC REMEDIATION: Delete orphaned usage plan
                        print(f"Deleting orphaned usage plan: {api_plan_name} ({usage_plan_id})")
                        try:
                            apigateway.delete_usage_plan(usagePlanId=usage_plan_id)
                            print(f"Successfully deleted orphaned usage plan: {api_plan_name}")
                            return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                                          f'REMEDIATED: Deleted orphaned plan {api_plan_name}', 
                                                          configuration_item)
                        except Exception as e:
                            print(f"Failed to delete orphaned usage plan {api_plan_name}: {str(e)}")
                            return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                                          f'Usage plan not found in DynamoDB (auto-delete failed)', 
                                                          configuration_item)
                else:
                    db_plan = response['Item']
                
                # Validate configuration
                plan_violations = validate_plan_configuration(db_plan, api_plan)
                if plan_violations:
                    violations.extend(plan_violations)
                    
            except Exception as e:
                return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                              f'Error getting API Gateway usage plan: {str(e)}', 
                                              configuration_item)
        
        # Return result based on violations
        if violations:
            # Summarize violations to fit within annotation limit
            violation_summary = f"VIOLATIONS: {len(violations)} issues found"
            return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                          violation_summary, configuration_item)
        else:
            return create_evaluation_result(resource_type, resource_id, 'COMPLIANT',
                                          'Resource complies with DynamoDB metadata', configuration_item)
            
    except Exception as e:
        return create_evaluation_result(resource_type, resource_id, 'NON_COMPLIANT',
                                      f'Error evaluating resource: {str(e)}', configuration_item)

def create_evaluation_result(resource_type, resource_id, compliance_type, annotation, configuration_item):
    """Create standardized evaluation result"""
    return {
        'ComplianceResourceType': resource_type,
        'ComplianceResourceId': resource_id,
        'ComplianceType': compliance_type,
        'Annotation': truncate_annotation(annotation),
        'OrderingTimestamp': configuration_item['configurationItemCaptureTime']
    }

def truncate_annotation(annotation, max_length=256):
    """Truncate annotation to AWS Config limit of 256 characters"""
    if len(annotation) <= max_length:
        return annotation
    return annotation[:max_length-3] + '...'

def validate_plan_configuration(db_plan, api_plan):
    """Validate core usage plan configuration parameters"""
    violations = []
    
    # Validate rate_limit
    api_throttle = api_plan.get('throttle', {})
    if int(db_plan.get('rate_limit', 0)) != int(api_throttle.get('rateLimit', 0)):
        violations.append(f"Rate limit: DB={db_plan.get('rate_limit')} vs API={api_throttle.get('rateLimit')}")
    
    # Validate burst_limit
    if int(db_plan.get('burst_limit', 0)) != int(api_throttle.get('burstLimit', 0)):
        violations.append(f"Burst limit: DB={db_plan.get('burst_limit')} vs API={api_throttle.get('burstLimit')}")
    
    # Validate quota_limit
    api_quota = api_plan.get('quota', {})
    if int(db_plan.get('quota_limit', 0)) != int(api_quota.get('limit', 0)):
        violations.append(f"Quota limit: DB={db_plan.get('quota_limit')} vs API={api_quota.get('limit')}")
    
    # Validate stages
    api_stages_arns = []
    for stage in api_plan.get('apiStages', []):
        api_id = stage.get('apiId')
        stage_name = stage.get('stage')
        stage_arn = f"arn:aws:apigateway:{os.environ.get('AWS_REGION', 'us-east-1')}::/restapis/{api_id}/stages/{stage_name}"
        api_stages_arns.append(stage_arn)
    
    db_stages = db_plan.get('stages', [])
    
    # Convert both to sets for comparison, handling the case where db_stages might be None
    db_stages_set = set(db_stages) if db_stages else set()
    api_stages_set = set(api_stages_arns)
    
    # Debug logging
    print(f"DB stages: {db_stages_set}")
    print(f"API stages: {api_stages_set}")
    
    # Check if the API Gateway stages are a subset of the DB stages
    if not api_stages_set.issubset(db_stages_set):
        missing_count = len(api_stages_set - db_stages_set)
        violations.append(f"Stages in API Gateway not in DB: {missing_count} stages")
    
    # Check if the DB stages are in API Gateway
    if not db_stages_set.issubset(api_stages_set):
        missing_count = len(db_stages_set - api_stages_set)
        violations.append(f"Stages in DB not in API Gateway: {missing_count} stages")
    
    return violations
