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
