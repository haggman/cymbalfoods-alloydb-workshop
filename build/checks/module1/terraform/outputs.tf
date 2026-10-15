output "alloydb_cluster" {
  value = google_alloydb_cluster.main.cluster_id
}

output "alloydb_primary_uri" {
  description = "For the AlloyDB Python connector and Auth Proxy."
  value       = google_alloydb_instance.primary.name
}

output "alloydb_primary_public_ip" {
  value = google_alloydb_instance.primary.public_ip_address
}

output "alloydb_read_pool_uri" {
  value = google_alloydb_instance.read_pool.name
}

output "student_db_user" {
  value = google_alloydb_user.student.user_id
}

output "dms_db_user" {
  value = google_alloydb_user.dms.user_id
}

output "sqlserver_instance" {
  value = google_sql_database_instance.legacy.name
}

output "sqlserver_connection_name" {
  description = "For the Cloud SQL Python connector and Auth Proxy."
  value       = google_sql_database_instance.legacy.connection_name
}

output "sqlserver_public_ip" {
  value = google_sql_database_instance.legacy.public_ip_address
}

output "sqlserver_service_account" {
  value = google_sql_database_instance.legacy.service_account_email_address
}

output "staging_bucket" {
  value = "gs://${google_storage_bucket.staging.name}"
}

output "secrets" {
  value = {
    sqlserver = google_secret_manager_secret.sqlserver.secret_id
    dms       = google_secret_manager_secret.dms.secret_id
  }
}
