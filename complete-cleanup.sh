#!/bin/bash

# Complete AWS API Gateway Usage Plan Solution Cleanup Script
# 1. Detaches Lambda functions from VPCs
# 2. Cleans up ENIs from CloudFormation stack VPCs  
# 3. Deletes CloudFormation stack with SAM
# 4. Supports multiple AWS regions

set -e

# Colors for output
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROFILE_ARG=""
DELETE_PROTECTED=""
REGIONS=""
ALL_REGIONS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --profile)
            PROFILE_ARG="--profile $2"
            PROFILE_NAME="$2"
            shift 2
            ;;
        --delete-protected)
            DELETE_PROTECTED="true"
            shift
            ;;
        --regions)
            REGIONS="$2"
            shift 2
            ;;
        --all-regions)
            ALL_REGIONS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--profile <profile-name>] [--delete-protected] [--regions REGIONS] [--all-regions]"
            echo "  --delete-protected  Also delete DynamoDB tables and S3 buckets with deletion protection"
            echo "  --regions          Comma-separated list of regions (e.g., us-east-1,eu-west-1)"
            echo "  --all-regions      Process all available AWS regions"
            exit 1
            ;;
    esac
done

if ! aws sts get-caller-identity $PROFILE_ARG &>/dev/null; then
    echo "Error: AWS CLI not configured or session expired"
    exit 1
fi

echo "Current AWS identity:"
aws sts get-caller-identity $PROFILE_ARG --query '[Account,UserId,Arn]' --output table

# Get regions to process
if [ "$ALL_REGIONS" = true ]; then
    REGION_LIST=$(aws ec2 describe-regions --query 'Regions[].RegionName' --output text $PROFILE_ARG | tr '\t' ',')
elif [ -n "$REGIONS" ]; then
    REGION_LIST="$REGIONS"
else
    REGION_LIST=$(aws configure get region $PROFILE_ARG || echo "us-east-1")
fi

IFS=',' read -ra REGION_ARRAY <<< "$REGION_LIST"

# Process each region
for region in "${REGION_ARRAY[@]}"; do
    echo -e "\n${YELLOW}=== Processing Region: $region ===${NC}"
    
    # Find CloudFormation stack in this region
    STACK_NAME=$(aws cloudformation list-stacks $PROFILE_ARG --region "$region" \
        --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE CREATE_FAILED \
        --query 'StackSummaries[?contains(StackName, `usage-plan`) || contains(StackName, `api-gateway`)].StackName' \
        --output text | head -1)
    
    if [ -z "$STACK_NAME" ]; then
        echo "No matching CloudFormation stack found in $region"
        continue
    fi
    
    echo "Found stack in $region: $STACK_NAME"

    # ===== STEP 1: DETACH LAMBDA FUNCTIONS FROM VPCs =====
    echo -e "\n===== STEP 1: DETACHING LAMBDA FUNCTIONS FROM VPCs in $region ====="
    
    FUNCTIONS=$(aws lambda list-functions $PROFILE_ARG --region "$region" \
        --query "Functions[?VpcConfig.VpcId != null && contains(FunctionName, 'api-gateway-usage-plan')].FunctionName" \
        --output text)

    if [ -n "$FUNCTIONS" ]; then
        echo "Found Lambda functions with VPC configurations in $region:"
        echo "$FUNCTIONS" | tr ' ' '\n'
        
        for FUNCTION in $FUNCTIONS; do
            echo "Detaching: $FUNCTION"
            aws lambda update-function-configuration $PROFILE_ARG --region "$region" \
                --function-name "$FUNCTION" \
                --vpc-config SubnetIds=[],SecurityGroupIds=[] \
                --output text --query 'FunctionName' >/dev/null
            echo "✓ Detached $FUNCTION from VPC"
        done
        
        echo "Waiting 60 seconds for Lambda detachment to complete..."
        sleep 60
    else
        echo "No Lambda functions with VPC configurations found in $region"
    fi

    # ===== STEP 2: CLEANUP ENIs FROM STACK VPCs =====
    echo -e "\n===== STEP 2: CLEANING UP ENIs FROM STACK VPCs in $region ====="
    
    # Get VPCs from CloudFormation stack
    STACK_VPCS=$(aws cloudformation describe-stack-resources $PROFILE_ARG --region "$region" \
        --stack-name "$STACK_NAME" \
        --query 'StackResources[?ResourceType==`AWS::EC2::VPC`].PhysicalResourceId' \
        --output text)

    if [ -n "$STACK_VPCS" ]; then
        echo "VPCs in stack: $STACK_VPCS"
        
        for VPC_ID in $STACK_VPCS; do
            echo -e "\nProcessing VPC: $VPC_ID"
            
            # Get all ENIs in this VPC
            VPC_ENIS=$(aws ec2 describe-network-interfaces $PROFILE_ARG --region "$region" \
                --filters "Name=vpc-id,Values=$VPC_ID" \
                --query 'NetworkInterfaces[].{Id:NetworkInterfaceId,Status:Status,Description:Description}' \
                --output json)
            
            if [ "$VPC_ENIS" != "[]" ]; then
                echo "Current ENIs in VPC:"
                echo "$VPC_ENIS" | jq -r '.[] | "  \(.Id) - \(.Status) - \(.Description)"'
                
                # Delete available ENIs
                AVAILABLE_ENIS=$(echo "$VPC_ENIS" | jq -r '.[] | select(.Status=="available") | .Id')
                
                if [ -n "$AVAILABLE_ENIS" ]; then
                    echo "Deleting available ENIs:"
                    for ENI in $AVAILABLE_ENIS; do
                        if [ -n "$ENI" ]; then
                            echo "  Deleting $ENI"
                            aws ec2 delete-network-interface $PROFILE_ARG --region "$region" --network-interface-id "$ENI" || echo "    Failed to delete $ENI"
                        fi
                    done
                fi
                
                # Handle in-use ENIs by type
                echo "$VPC_ENIS" | jq -r '.[] | select(.Status=="in-use") | .Id' | while read -r ENI; do
                    if [ -n "$ENI" ]; then
                        ENI_DETAILS=$(aws ec2 describe-network-interfaces $PROFILE_ARG --region "$region" --network-interface-ids "$ENI" --query 'NetworkInterfaces[0]' --output json)
                        DESCRIPTION=$(echo "$ENI_DETAILS" | jq -r '.Description')
                        INSTANCE_OWNER=$(echo "$ENI_DETAILS" | jq -r '.Attachment.InstanceOwnerId // "none"')
                        
                        if [[ "$DESCRIPTION" == *"NAT Gateway"* ]] || [[ "$INSTANCE_OWNER" == "amazon-aws" ]]; then
                            echo "  Skipping AWS-managed ENI $ENI ($DESCRIPTION)"
                        else
                            echo "  Detaching user-managed ENI $ENI"
                            aws ec2 detach-network-interface $PROFILE_ARG --region "$region" --network-interface-id "$ENI" --force || echo "    Failed to detach $ENI"
                            sleep 5
                            echo "  Deleting ENI $ENI"
                            aws ec2 delete-network-interface $PROFILE_ARG --region "$region" --network-interface-id "$ENI" || echo "    Failed to delete $ENI"
                        fi
                    fi
                done
            else
                echo "No ENIs found in VPC $VPC_ID"
            fi
        done
    else
        echo "No VPCs found in stack"
    fi

    # ===== STEP 3: DELETE S3 BUCKETS =====
    echo -e "\n===== STEP 3: DELETING S3 BUCKETS in $region ====="
    
    # Find S3 buckets from CloudFormation stack
    STACK_BUCKETS=$(aws cloudformation describe-stack-resources $PROFILE_ARG --region "$region" \
        --stack-name "$STACK_NAME" \
        --query 'StackResources[?ResourceType==`AWS::S3::Bucket`].PhysicalResourceId' \
        --output text)
    echo "S3 buckets in stack: $STACK_BUCKETS"

    if [ -n "$STACK_BUCKETS" ]; then
        for BUCKET in $STACK_BUCKETS; do
            if [[ "$BUCKET" == *"access-logs"* ]] || [[ "$BUCKET" == *"replica"* ]] || [[ "$BUCKET" == *"config"* ]]; then
                echo "Processing bucket: $BUCKET"
                
                # Empty bucket first
                echo "  Emptying bucket..."
                aws s3 rm s3://"$BUCKET" --recursive $PROFILE_ARG || echo "  Warning: Could not empty $BUCKET"
                
                # Delete bucket
                echo "  Deleting bucket..."
                aws s3 rb s3://"$BUCKET" $PROFILE_ARG || echo "  Warning: Could not delete $BUCKET"
                echo "✓ Processed bucket: $BUCKET"
            else
                echo "Skipping non-target bucket: $BUCKET"
            fi
        done
    else
        echo "No S3 buckets found in stack"
    fi

    # ===== STEP 4: DELETE PROTECTED RESOURCES =====
    if [ "$DELETE_PROTECTED" = "true" ]; then
        echo -e "\n===== STEP 4: DELETING PROTECTED RESOURCES in $region ====="
        
        DYNAMODB_TABLES=$(aws cloudformation describe-stack-resources $PROFILE_ARG --region "$region" \
        --stack-name "$STACK_NAME" \
        --query 'StackResources[?ResourceType==`AWS::DynamoDB::Table`].PhysicalResourceId' \
        --output text)
    
        for TABLE in $DYNAMODB_TABLES; do
            echo "Processing DynamoDB table: $TABLE"
    
            # Remove deletion protection and delete table
            echo "  Removing deletion protection..."
            aws dynamodb update-table --table-name "$TABLE" --no-deletion-protection-enabled --region "$region" $PROFILE_ARG || echo "  Warning: Could not remove deletion protection"
    
            echo "  Deleting table..."
            aws dynamodb delete-table --table-name "$TABLE" --region "$region" $PROFILE_ARG || echo "  Warning: Could not delete table"
            echo "✓ Processed table: $TABLE"
        done
    fi

    # ===== STEP 5: DELETE CLOUDFORMATION STACK =====
    echo -e "\n===== STEP 5: DELETING CLOUDFORMATION STACK in $region ====="
    
    echo "Deleting stack: $STACK_NAME"
    
    if [ -n "$PROFILE_NAME" ]; then
        sam delete --stack-name "$STACK_NAME" --region "$region" $PROFILE_ARG --no-prompts
    else
        sam delete --stack-name "$STACK_NAME" --region "$region" --no-prompts
    fi
    
    # Clean up usage plans in this region
    if [ -n "$PROFILE_NAME" ]; then
        ./usage_plan_cleanup.sh --confirm --regions "$region" $PROFILE_ARG
    else
        ./usage_plan_cleanup.sh --confirm --regions "$region"
    fi
    
    echo "✓ Region $region cleanup completed"
done

echo -e "\n🎉 COMPLETE CLEANUP FINISHED ACROSS ALL REGIONS! 🎉"
echo "✓ Lambda functions detached from VPCs"
echo "✓ ENIs cleaned up from stack VPCs"
echo "✓ S3 buckets (access-logs and replica) deleted"
if [ "$DELETE_PROTECTED" = "true" ]; then
    echo "✓ DynamoDB VersionLogTable deletion protection removed and deleted"
    echo "✓ All S3 buckets from stack deleted"
fi
echo "✓ CloudFormation stacks deleted"
echo "✓ Usage plans deleted"
echo "✓ Processed regions: ${REGION_ARRAY[*]}"