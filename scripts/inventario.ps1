# Inventario de todo lo desplegado en Floci, por ambiente (para la presentación).
#   .\scripts\inventario.ps1            # dev, test y prod
#   .\scripts\inventario.ps1 -Env prod  # uno solo
param([ValidateSet("dev", "test", "prod", "all")][string]$Env = "all")
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\aws_env.ps1"
$envs = if ($Env -eq "all") { "dev", "test", "prod" } else { @($Env) }
function Title($t) { Write-Host ""; Write-Host "==================== $t ====================" -ForegroundColor Cyan }
function Sub($t) { Write-Host ""; Write-Host "--- $t" -ForegroundColor Yellow }

Title "ESTADO DE TERRAFORM (S3 versionado)"
aws s3api get-bucket-versioning --bucket aegisvolt-tfstate --output table
aws s3api list-object-versions --bucket aegisvolt-tfstate --prefix "env:/" --query "Versions[?IsLatest].{Key:Key,Ultima:LastModified,Tamano:Size}" --output table
aws dynamodb describe-table --table-name aegisvolt-tf-locks --query "Table.{TablaLocks:TableName,Estado:TableStatus}" --output table

foreach ($e in $envs) {
  Title "AMBIENTE $($e.ToUpper())"
  $vpc = aws ec2 describe-vpcs --filters "Name=tag:Name,Values=aegisvolt-$e-vpc" --query "Vpcs[0].VpcId" --output text
  Sub "Red: VPC $vpc"
  aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" --query "Subnets[].{Nombre:Tags[?Key=='Name']|[0].Value,CIDR:CidrBlock,AZ:AvailabilityZone}" --output table
  Sub "VPC Endpoints"
  aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpc" --query "VpcEndpoints[].{Servicio:ServiceName,Tipo:VpcEndpointType,Id:VpcEndpointId}" --output table
  Sub "DynamoDB (Database-per-Service)"
  foreach ($t in "reservations", "billing", "dispatch", "sagas", "feature-flags") {
    $name = "aegisvolt-$t-$e"
    $count = aws dynamodb scan --table-name $name --select COUNT --query "Count" --output text 2>$null
    Write-Host ("  {0,-32} {1,6} items" -f $name, $count)
  }
  Sub "Feature flags"
  aws dynamodb scan --table-name "aegisvolt-feature-flags-$e" --query "Items[].{Flag:name.S,Activa:enabled.BOOL,CambiadaPor:updated_by.S}" --output table
  Sub "CloudWatch: grupos de logs y alarmas"
  aws logs describe-log-groups --log-group-name-prefix "/aegisvolt/$e/" --query "logGroups[].{Grupo:logGroupName,Retencion:retentionInDays}" --output table
  aws cloudwatch describe-alarms --alarm-name-prefix "aegisvolt-$e-" --query "MetricAlarms[].{Alarma:AlarmName,Estado:StateValue}" --output table
  Sub "ECR: repositorios e imágenes"
  foreach ($r in "station-reservation-service", "billing-wallet-service", "energy-dispatch-service", "graphql-gateway") {
    $tags = aws ecr list-images --repository-name "aegisvolt-$e/$r" --query "imageIds[].imageTag" --output text 2>$null
    Write-Host ("  aegisvolt-{0}/{1,-30} {2}" -f $e, $r, $tags)
  }
  Sub "EKS + Kubernetes"
  aws eks describe-cluster --name "aegisvolt-$e-eks" --query "cluster.{Cluster:name,Version:version,Estado:status,Endpoint:endpoint}" --output table
  kubectl --context "aegisvolt-$e-eks" -n aegisvolt get deploy -o wide 2>$null
  Sub "API Gateway privado"
  aws apigateway get-rest-apis --query "items[?name=='aegisvolt-$e-api'].{Api:name,Id:id,Tipo:endpointConfiguration.types[0]}" --output table
}
