# The Helm chart needs three of these and reads them from the output rather
# than from a value somebody retypes. The fourth is here because the endpoint
# is the thing a reviewer asks about first.

output "cluster_name" {
  value       = google_container_cluster.gateway.name
  description = "Cluster the chart installs into."
}

output "cluster_endpoint" {
  value       = google_container_cluster.gateway.endpoint
  description = "Kubernetes API address. Private when private_endpoint is true."
  sensitive   = true
}

output "workload_service_account" {
  value       = google_service_account.gateway.email
  description = "The identity the chart annotates its service account with."
}

output "provider_key_secret" {
  value       = google_secret_manager_secret.provider_key.secret_id
  description = "Secret the gateway reads the vendor key from. The value is never set here."
}

output "nat_address_count" {
  value       = length(google_compute_router_nat.gateway.nat_ips)
  description = "Zero means the addresses are allocated automatically, which is what AUTO_ONLY does."
}
