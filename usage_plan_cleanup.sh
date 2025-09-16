#!/bin/bash

# cleanup_usage_plans.sh - CLI script to delete all API Gateway usage plans
# 
# ⚠️  WARNING: This script is intended for DEVELOPMENT environments only!
# ⚠️  DO NOT use this script in PRODUCTION environments!
# ⚠️  This will delete ALL usage plans, API keys, and stage associations!
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to show usage
show_usage() {
    echo "Usage: $0 [--confirm] [--check-stages] [--remove-stages] [--regions REGIONS] [--all-regions]"
    echo "  --confirm        Actually delete the usage plans, API keys, and stage associations"
    echo "                   (without this, just shows what would be deleted)"
    echo "  --check-stages   Check and display attached stages for all usage plans"
    echo "  --remove-stages  Remove all attached stages from all usage plans"
    echo "  --profile        AWS CLI Profile"
    echo "  --regions        Comma-separated list of regions (e.g., us-east-1,eu-west-1)"
    echo "  --all-regions    Process all available AWS regions"
    echo ""
    echo "WARNING: This script is for DEVELOPMENT environments only!"
    exit 1
}

# Function to process region
process_region() {
    local region=$1
    local operation=$2
    
    echo -e "\n${YELLOW}=== Region: $region ===${NC}"
    
    if [ "$operation" = "check-stages" ]; then
        echo "🔍 Checking attached stages for all usage plans in $region..."
        USAGE_PLANS=$(aws apigateway get-usage-plans --query 'items[].{id:id,name:name}' --output json --region "$region" $PROFILE_ARG)
        
        if [ "$USAGE_PLANS" = "[]" ]; then
            echo "No usage plans found in $region."
            return
        fi
        
        echo "$USAGE_PLANS" | jq -r '.[].id' | while read -r PLAN_ID; do
            PLAN_NAME=$(echo "$USAGE_PLANS" | jq -r ".[] | select(.id==\"$PLAN_ID\") | .name")
            echo -e "\n${YELLOW}Usage Plan: $PLAN_NAME ($PLAN_ID)${NC}"
            
            PLAN_DETAILS=$(aws apigateway get-usage-plan --usage-plan-id "$PLAN_ID" --region "$region" $PROFILE_ARG 2>/dev/null || echo '{}')
            API_STAGES=$(echo "$PLAN_DETAILS" | jq -r '.apiStages[]? | "\(.apiId) \(.stage)"' 2>/dev/null || echo "")
            
            if [ -n "$API_STAGES" ]; then
                echo "  Attached stages:"
                echo "$API_STAGES" | while read -r API_ID STAGE_NAME; do
                    if [ -n "$API_ID" ] && [ -n "$STAGE_NAME" ]; then
                        echo "    - API: $API_ID, Stage: $STAGE_NAME"
                    fi
                done
            else
                echo "  No attached stages"
            fi
        done
        return
    fi
    
    if [ "$operation" = "remove-stages" ]; then
        echo "🔧 Removing all attached stages from usage plans in $region..."
        USAGE_PLANS=$(aws apigateway get-usage-plans --query 'items[].{id:id,name:name}' --output json --region "$region" $PROFILE_ARG)
        
        if [ "$USAGE_PLANS" = "[]" ]; then
            echo "No usage plans found in $region."
            return
        fi
        
        echo "$USAGE_PLANS" | jq -r '.[].id' | while read -r PLAN_ID; do
            PLAN_NAME=$(echo "$USAGE_PLANS" | jq -r ".[] | select(.id==\"$PLAN_ID\") | .name")
            echo -e "\n${YELLOW}Processing: $PLAN_NAME ($PLAN_ID)${NC}"
            
            PLAN_DETAILS=$(aws apigateway get-usage-plan --usage-plan-id "$PLAN_ID" --region "$region" $PROFILE_ARG 2>/dev/null || echo '{}')
            API_STAGES=$(echo "$PLAN_DETAILS" | jq -r '.apiStages[]? | "\(.apiId) \(.stage)"' 2>/dev/null || echo "")
            
            if [ -n "$API_STAGES" ]; then
                echo "  Removing stage associations..."
                echo "$API_STAGES" | while read -r API_ID STAGE_NAME; do
                    if [ -n "$API_ID" ] && [ -n "$STAGE_NAME" ]; then
                        if aws apigateway update-usage-plan --usage-plan-id "$PLAN_ID" --patch-operations "op=remove,path=/apiStages,value=$API_ID:$STAGE_NAME" --region "$region" $PROFILE_ARG 2>/dev/null; then
                            echo -e "    ${GREEN}✅ Removed: $API_ID/$STAGE_NAME${NC}"
                        else
                            echo -e "    ${RED}❌ Failed to remove: $API_ID/$STAGE_NAME${NC}"
                        fi
                    fi
                done
            else
                echo "  No attached stages to remove"
            fi
        done
        return
    fi
    
    # Main cleanup operation
    echo "🔍 Fetching all API Gateway resources in $region..."
    
    USAGE_PLANS=$(aws apigateway get-usage-plans --query 'items[].{id:id,name:name}' --output json --region "$region" $PROFILE_ARG)
    API_KEYS=$(aws apigateway get-api-keys --query 'items[].{id:id,name:name}' --output json --region "$region" $PROFILE_ARG)
    
    if [ "$USAGE_PLANS" = "[]" ] && [ "$API_KEYS" = "[]" ]; then
        echo "No usage plans or API keys found in $region."
        return
    fi
    
    PLAN_COUNT=$(echo "$USAGE_PLANS" | jq length)
    KEY_COUNT=$(echo "$API_KEYS" | jq length)
    
    echo -e "${YELLOW}Found $PLAN_COUNT usage plans and $KEY_COUNT API keys in $region:${NC}"
    
    if [ "$USAGE_PLANS" != "[]" ]; then
        echo "Usage Plans:"
        echo "$USAGE_PLANS" | jq -r '.[] | "  - \(.name) (\(.id))"'
    fi
    
    if [ "$API_KEYS" != "[]" ]; then
        echo "API Keys:"
        echo "$API_KEYS" | jq -r '.[] | "  - \(.name) (\(.id))"'
    fi
    
    if [ "$CONFIRM" = false ]; then
        return
    fi
    
    echo -e "\n${RED}⚠️  Deleting all usage plans, API keys, and stage associations in $region...${NC}"
    
    if [ "$USAGE_PLANS" != "[]" ]; then
        echo "$USAGE_PLANS" | jq -r '.[].id' | while read -r PLAN_ID; do
            PLAN_NAME=$(echo "$USAGE_PLANS" | jq -r ".[] | select(.id==\"$PLAN_ID\") | .name")
            echo "Processing usage plan: $PLAN_NAME ($PLAN_ID)"
            
            PLAN_DETAILS=$(aws apigateway get-usage-plan --usage-plan-id "$PLAN_ID" --region "$region" $PROFILE_ARG 2>/dev/null || echo '{}')
            PLAN_API_KEYS=$(aws apigateway get-usage-plan-keys --usage-plan-id "$PLAN_ID" --region "$region" $PROFILE_ARG --query 'items[].id' --output text 2>/dev/null || echo "")
            
            if [ -n "$PLAN_API_KEYS" ] && [ "$PLAN_API_KEYS" != "None" ]; then
                echo "  Removing API key associations..."
                for KEY_ID in $PLAN_API_KEYS; do
                    if aws apigateway delete-usage-plan-key --usage-plan-id "$PLAN_ID" --key-id "$KEY_ID" --region "$region" $PROFILE_ARG 2>/dev/null; then
                        echo "    Removed API key: $KEY_ID"
                    else
                        echo "    Failed to remove API key: $KEY_ID"
                    fi
                done
            fi
            
            API_STAGES=$(echo "$PLAN_DETAILS" | jq -r '.apiStages[]? | "\(.apiId) \(.stage)"' 2>/dev/null || echo "")
            if [ -n "$API_STAGES" ]; then
                echo "  Removing stage associations..."
                while IFS=' ' read -r API_ID STAGE_NAME; do
                    if [ -n "$API_ID" ] && [ -n "$STAGE_NAME" ]; then
                        if aws apigateway update-usage-plan --usage-plan-id "$PLAN_ID" --patch-operations "op=remove,path=/apiStages,value=$API_ID:$STAGE_NAME" --region "$region" $PROFILE_ARG 2>/dev/null; then
                            echo "    Removed stage: $API_ID/$STAGE_NAME"
                        else
                            echo "    Failed to remove stage: $API_ID/$STAGE_NAME"
                        fi
                    fi
                done <<< "$API_STAGES"
            fi
            
            sleep 1
            
            if aws apigateway delete-usage-plan --usage-plan-id "$PLAN_ID" --region "$region" $PROFILE_ARG 2>/dev/null; then
                echo -e "  ${GREEN}✅ Deleted usage plan: $PLAN_NAME${NC}"
            else
                echo -e "  ${RED}❌ Error deleting usage plan: $PLAN_NAME${NC}"
            fi
        done
    fi
    
    if [ "$API_KEYS" != "[]" ]; then
        echo "\nDeleting standalone API keys in $region..."
        echo "$API_KEYS" | jq -r '.[].id' | while read -r KEY_ID; do
            KEY_NAME=$(echo "$API_KEYS" | jq -r ".[] | select(.id==\"$KEY_ID\") | .name")
            
            if aws apigateway delete-api-key --api-key "$KEY_ID" --region "$region" $PROFILE_ARG 2>/dev/null; then
                echo -e "${GREEN}✅ Deleted API key: $KEY_NAME ($KEY_ID)${NC}"
            else
                echo -e "${RED}❌ Error deleting API key: $KEY_NAME${NC}"
            fi
        done
    fi
}

# Parse arguments
CONFIRM=false
CHECK_STAGES=false
REMOVE_STAGES=false
PROFILE_ARG=""
REGIONS=""
ALL_REGIONS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --confirm)
            CONFIRM=true
            shift
            ;;
        --check-stages)
            CHECK_STAGES=true
            shift
            ;;
        --remove-stages)
            REMOVE_STAGES=true
            shift
            ;;
        --profile)
            PROFILE_ARG="--profile $2"
            shift 2
            ;;
        --regions)
            REGIONS="$2"
            shift 2
            ;;
        --all-regions)
            ALL_REGIONS=true
            shift
            ;;
        -h|--help)
            show_usage
            ;;
        *)
            echo "Unknown option $1"
            show_usage
            ;;
    esac
done

# Get regions to process
if [ "$ALL_REGIONS" = true ]; then
    REGION_LIST=$(aws ec2 describe-regions --query 'Regions[].RegionName' --output text $PROFILE_ARG | tr '\t' ',')
elif [ -n "$REGIONS" ]; then
    REGION_LIST="$REGIONS"
else
    REGION_LIST=$(aws configure get region $PROFILE_ARG || echo "us-east-1")
fi

IFS=',' read -ra REGION_ARRAY <<< "$REGION_LIST"

# Process regions
if [ "$CHECK_STAGES" = true ]; then
    for region in "${REGION_ARRAY[@]}"; do
        process_region "$region" "check-stages"
    done
    exit 0
fi

if [ "$REMOVE_STAGES" = true ]; then
    for region in "${REGION_ARRAY[@]}"; do
        process_region "$region" "remove-stages"
    done
    echo -e "\n${GREEN}Stage removal completed across all regions!${NC}"
    exit 0
fi

for region in "${REGION_ARRAY[@]}"; do
    process_region "$region" "cleanup"
done

if [ "$CONFIRM" = false ]; then
    echo -e "\n${YELLOW}Use --confirm to actually delete these resources${NC}"
else
    echo -e "\n${GREEN}Cleanup completed across all regions!${NC}"
fi