##Request examples

curl -X POST <Management API URL>/Stage/usage-plans \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{
    "name": "EnterpriseAPI Tier",
    "description": "Enterprise tier with maximum limits", 
    "tier": "Enterprise",
    "rate_limit": 5000,
    "burst_limit": 10000,
    "quota_limit": 100000,
    "quota_period": "MONTH"
  }'

curl -X PUT <Management API URL>/Stage/usage-plans/<Usage Plan ID> \
  -H "Content-Type: application/json" \
  -d '{
    "stages": [
      "arn:aws:apigateway:us-east-2::/restapis/xxxxxxxxxx/stages/dev1",
      "arn:aws:apigateway:us-east-2::/restapis/xxxxxxxxxx/stages/dev2"
    ]
  }'

  curl -X GET <Management API URL>/Stage/usage-plans/<Usage Plan ID> \
  -H "Content-Type: application/json"

  curl -X PUT <Management API URL>/Stage/usage-plans/<Usage Plan ID> \
  -H "Content-Type: application/json" \
  -d '{
    "rate_limit": 100,
    "burst_limit": 100,
    "quota_limit": 90,
    "quota_period": "MONTH"
  }'