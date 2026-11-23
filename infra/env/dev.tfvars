# Ambiente DEV · workspace "dev"
environment        = "dev"
vpc_cidr           = "10.10.0.0/16"
log_retention_days = 7
node_desired_size  = 1

feature_flags = {
  ENABLE_DYNAMIC_SURGE_PRICING    = true
  ENABLE_FAST_CHARGE_BOOST        = true
  SIMULATE_CHARGER_HARDWARE_FAULT = false
}

# Respaldo si el VPC Link no enruta en Floci: api_integration_mode = "http_proxy"
api_integration_mode = "vpc_link"
local_backend_url    = "http://host.docker.internal:8081"
