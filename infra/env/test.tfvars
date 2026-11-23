# Ambiente TEST · workspace "test"
environment        = "test"
vpc_cidr           = "10.20.0.0/16"
log_retention_days = 14
node_desired_size  = 1

feature_flags = {
  ENABLE_DYNAMIC_SURGE_PRICING    = true
  ENABLE_FAST_CHARGE_BOOST        = true
  SIMULATE_CHARGER_HARDWARE_FAULT = false
}

api_integration_mode = "vpc_link"
local_backend_url    = "http://host.docker.internal:8082"
