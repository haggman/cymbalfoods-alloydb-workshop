output "alloydb_cluster" {
  description = "Cluster ID."
  value       = google_alloydb_cluster.main.cluster_id
}

output "alloydb_instance" {
  description = "Primary instance ID. Notebooks discover this themselves."
  value       = google_alloydb_instance.primary.instance_id
}

output "alloydb_public_ip" {
  description = "Reached by the AlloyDB Python Connector with IAM auth: no password, no authorized networks."
  value       = google_alloydb_instance.primary.public_ip_address
}

output "student_db_user" {
  description = "IAM database user holding alloydbsuperuser."
  value       = google_alloydb_user.student.user_id
}

output "staging_bucket" {
  description = "In-project bucket the AlloyDB service agent can read; the source for server-side imports."
  value       = "gs://${google_storage_bucket.staging.name}"
}

output "lab_module" {
  description = "Which Track A lab this provisioned."
  value       = var.lab_module
}

output "instance_path" {
  description = "Fully qualified primary instance path, for MCP, Data API and connector callers."
  value       = "projects/${var.gcp_project_id}/locations/${var.gcp_region}/clusters/${local.cluster_id}/instances/${local.instance_id}"
}

output "read_pool_instance" {
  description = "Read pool instance ID, or null when the module has none."
  value       = try(google_alloydb_instance.read_pool[0].instance_id, null)
}

output "setup_vm" {
  description = "The VM that loads this lab's data, or null when the module has none (aldb002)."
  value       = try(google_compute_instance.setup[0].name, null)
}

output "task0_check" {
  description = "One line for Task 0: prints ready, what is still loading, or what failed."
  value       = local.f.setup_vm ? local.task0_check : null
}
