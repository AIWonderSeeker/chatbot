output "alb_dns_name" {
  value       = aws_lb.this.dns_name
  description = "Point your DNS record (matching the certificate) at this."
}
output "alb_arn" { value = aws_lb.this.arn }
output "conversations_table" { value = aws_dynamodb_table.conversations.name }
output "cluster_name" { value = aws_ecs_cluster.this.name }
