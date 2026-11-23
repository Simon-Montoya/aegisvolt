# Ambiente PROD · workspace "prod"
environment        = "prod"
vpc_cidr           = "10.30.0.0/16"
log_retention_days = 30
node_desired_size  = 2

# En prod las tarifas dinámicas arrancan apagadas y la inyección de fallos
# jamás se puede encender (el código lo bloquea aunque se intente).
feature_flags = {
  ENABLE_DYNAMIC_SURGE_PRICING    = false
  ENABLE_FAST_CHARGE_BOOST        = false
  SIMULATE_CHARGER_HARDWARE_FAULT = false
}

api_integration_mode = "vpc_link"
local_backend_url    = "http://host.docker.internal:8083"
