# -----------------------------------------------------------------------------
# Entrada PRIVADA a Kubernetes:
#
#   NLB interno (subredes privadas)  --TCP-->  NodePort 30080 de los nodos EKS
#                                              --kube-proxy-->  pods graphql-gateway
#
# Sobre ese NLB se publica un VPC Endpoint Service (PrivateLink) y se crea un
# Interface VPC Endpoint que lo consume: así, cualquier consumidor dentro de la
# VPC (o de otra VPC autorizada) llega a los servicios del clúster por IPs
# privadas, sin exponer nada a internet. API Gateway usa el mismo NLB a través
# de su VPC Link.
# -----------------------------------------------------------------------------

variable "prefix" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "node_port" { type = number }
variable "node_asg_names" { type = list(string) }
variable "endpoint_sg_id" { type = string }
variable "enable_private_link_service" {
  description = "Crear el VPC Endpoint Service (PrivateLink) sobre el NLB. Floci no implementa CreateVpcEndpointServiceConfiguration."
  type        = bool
  default     = true
}
variable "attach_node_asg" {
  description = "Registrar el ASG del node group en el target group. Floci no crea el ASG real del node group."
  type        = bool
  default     = true
}

resource "aws_lb" "gateway" {
  name               = substr("${var.prefix}-gw-nlb", 0, 32)
  load_balancer_type = "network"
  internal           = true # sin IP pública
  subnets            = var.private_subnet_ids

  enable_cross_zone_load_balancing = true
}

resource "aws_lb_target_group" "gateway" {
  name        = substr("${var.prefix}-gw-tg", 0, 32)
  port        = var.node_port
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = var.vpc_id

  health_check {
    protocol            = "HTTP"
    path                = "/health"
    port                = tostring(var.node_port)
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }
}

resource "aws_lb_listener" "gateway" {
  load_balancer_arn = aws_lb.gateway.arn
  port              = 80
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}

# Los nodos del node group (ASG administrado por EKS) se registran solos en el target group.
# (count fijo: los nombres del ASG solo se conocen después del apply del node group)
resource "aws_autoscaling_attachment" "nodes" {
  count                  = var.attach_node_asg ? 1 : 0
  autoscaling_group_name = var.node_asg_names[0]
  lb_target_group_arn    = aws_lb_target_group.gateway.arn
}

# PrivateLink: "servicio" privado respaldado por el NLB del clúster.
resource "aws_vpc_endpoint_service" "gateway" {
  count                      = var.enable_private_link_service ? 1 : 0
  acceptance_required        = false
  network_load_balancer_arns = [aws_lb.gateway.arn]

  tags = { Name = "${var.prefix}-k8s-gateway-endpoint-service" }
}

# Interface VPC Endpoint que consume el servicio anterior desde las subredes privadas.
resource "aws_vpc_endpoint" "gateway" {
  count               = var.enable_private_link_service ? 1 : 0
  vpc_id              = var.vpc_id
  service_name        = aws_vpc_endpoint_service.gateway[0].service_name
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [var.endpoint_sg_id]
  private_dns_enabled = false

  tags = { Name = "${var.prefix}-vpce-k8s-gateway" }
}

output "nlb_arn" { value = aws_lb.gateway.arn }
output "nlb_dns_name" { value = aws_lb.gateway.dns_name }
output "private_link" {
  value = {
    endpoint_service_name = var.enable_private_link_service ? aws_vpc_endpoint_service.gateway[0].service_name : "no soportado por el emulador (se crea en AWS real)"
    vpc_endpoint_id       = var.enable_private_link_service ? aws_vpc_endpoint.gateway[0].id : null
    nlb_dns_name          = aws_lb.gateway.dns_name
  }
}
