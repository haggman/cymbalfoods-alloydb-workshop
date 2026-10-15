# =============================================================================
# Module 1 (aldb001) provisioning PROBE: everything Start Lab would build, at once
# =============================================================================
# A standalone root, deliberately NOT a call to ../../../../terraform: the
# skeleton conversation is reshaping that module now. The AlloyDB half copies
# terraform/main.tf (cluster, ZONAL 8-vCPU primary, public IP, IAM user, staging
# bucket); the rest is what Module 1 adds:
#
#   * a READ POOL (1 node, 2 vCPU, public IP for the Python connector)
#   * Cloud SQL for SQL Server 2022 Standard (public IP, DMS allowlisted)
#   * a built-in AlloyDB user for DMS: its name MUST equal the destination
#     database name (cymbalgoal_scouting)
#   * DMS's us-central1 egress IPs on BOTH ends: authorized networks on the
#     Cloud SQL instance and on the AlloyDB primary. DMS can't reach AlloyDB's
#     private IP through our VPC (peering doesn't chain), so public IP + allowlist
#     is the networking a startup script can build with no proxy VM.
#   * both passwords in Secret Manager, so notebooks and Cloud Shell read them
#     without anyone pasting a secret
#
# step1-provision.sh applies this and times every resource from Terraform's
# "Creation complete after ..." lines: the cold-start measurement.
# =============================================================================

locals {
  cluster_id     = "cymbalgoal-cluster"
  instance_id    = "cymbalgoal-primary"
  read_pool_id   = "cymbalgoal-read-pool"
  network        = "cymbalgoal-network"
  sql_instance   = "cymbalgoal-legacy-sql"
  dms_db_user    = "cymbalgoal_scouting" # = the destination database name (DMS rule)
  staging_bucket = "${var.gcp_project_id}-cymbalgoal-staging"

  student_email = can(regex("@", var.username)) ? var.username : "${var.username}@${var.student_email_domain}"

  # https://docs.cloud.google.com/database-migration/docs/sqlserver-to-alloydb/ip-allowlists-and-regions
  dms_egress_ips = {
    "us-central1" = ["34.72.28.29", "34.67.234.134", "34.67.6.157", "34.72.239.218", "34.71.242.81"]
    "us-east1"    = ["34.74.216.163", "34.75.166.194", "104.196.6.24", "34.73.50.6", "35.237.45.20"]
  }
  dms_ips = local.dms_egress_ips[var.gcp_region]

  # Flags are per instance: the read pool repeats the primary's.
  alloydb_flags = {
    "password.enforce_complexity" = "on" # mandatory with public IP
    "alloydb.iam_authentication"  = "on" # the student's IAM login
  }
}

data "google_project" "current" {
  project_id = var.gcp_project_id
}

# -----------------------------------------------------------------------------
# APIs
# -----------------------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "alloydb.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
    "monitoring.googleapis.com",
    "aiplatform.googleapis.com", # Colab Enterprise runtimes
    "dataform.googleapis.com",   # Colab Enterprise notebook storage
    "sqladmin.googleapis.com",   # Cloud SQL for SQL Server
    "datamigration.googleapis.com",
    "cloudaicompanion.googleapis.com", # Gemini for Google Cloud: DMS conversion assistance
    "secretmanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# Network (AlloyDB needs Private Service Access even when reached publicly)
# -----------------------------------------------------------------------------
resource "google_compute_network" "main" {
  name                    = local.network
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_global_address" "psa" {
  name          = "cymbalgoal-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.main.id
  depends_on    = [google_project_service.apis]
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]
  depends_on              = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Passwords. Both must satisfy complexity rules (SQL Server's, and AlloyDB's
# password.enforce_complexity), and both end up in connection strings and
# SQLAlchemy URLs (DVT), so specials are limited to - and _.
# -----------------------------------------------------------------------------
resource "random_password" "alloydb_initial" {
  length           = 24
  special          = true
  override_special = "-_"
}

resource "random_password" "sqlserver" {
  length           = 24
  special          = true
  override_special = "-_"
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 1
}

resource "random_password" "dms" {
  length           = 24
  special          = true
  override_special = "-_"
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 1
}

# -----------------------------------------------------------------------------
# AlloyDB cluster and primary (as terraform/main.tf, plus the DMS allowlist)
# -----------------------------------------------------------------------------
resource "google_alloydb_cluster" "main" {
  cluster_id       = local.cluster_id
  location         = var.gcp_region
  database_version = "POSTGRES_18"

  network_config {
    network = google_compute_network.main.id
  }

  initial_user {
    user     = "postgres"
    password = random_password.alloydb_initial.result
  }

  depends_on = [google_service_networking_connection.psa]
}

resource "google_alloydb_instance" "primary" {
  cluster           = google_alloydb_cluster.main.name
  instance_id       = local.instance_id
  instance_type     = "PRIMARY"
  availability_type = "ZONAL" # one node; REGIONAL doubles the capacity every student needs

  machine_config {
    cpu_count = var.cpu_count
  }

  # Public IP. The Python connector and Auth Proxy need no authorized network;
  # DMS connects directly, so its egress IPs (and only those) are allowed.
  network_config {
    enable_public_ip = true

    dynamic "authorized_external_networks" {
      for_each = local.dms_ips
      content {
        cidr_range = "${authorized_external_networks.value}/32"
      }
    }
  }

  database_flags = local.alloydb_flags

  depends_on = [google_service_networking_connection.psa]
}

# -----------------------------------------------------------------------------
# Read pool: one node at Start Lab; Task 6 scales it to two
# -----------------------------------------------------------------------------
resource "google_alloydb_instance" "read_pool" {
  cluster       = google_alloydb_cluster.main.name
  instance_id   = local.read_pool_id
  instance_type = "READ_POOL"

  read_pool_config {
    node_count = var.read_pool_node_count
  }

  machine_config {
    cpu_count = var.read_pool_cpu_count
  }

  network_config {
    enable_public_ip = true
  }

  database_flags = local.alloydb_flags

  # A read pool can only be created once the primary exists.
  depends_on = [google_alloydb_instance.primary]
}

# -----------------------------------------------------------------------------
# Database users
# -----------------------------------------------------------------------------
# The student, by IAM (same two second-apply traps as terraform/main.tf: the
# role list is complete and alphabetical).
resource "google_alloydb_user" "student" {
  cluster        = google_alloydb_cluster.main.id
  user_id        = local.student_email
  user_type      = "ALLOYDB_IAM_USER"
  database_roles = ["alloydbiamuser", "alloydbsuperuser"]
  # After the read pool: a user created while the cluster has another operation
  # running can fail with a conflicting-operation error (skeleton handback).
  depends_on = [google_alloydb_instance.read_pool]
}

# DMS's destination login. Its name must equal the destination database name.
# alloydbsuperuser for the probe; the docs list narrower grants (CREATE on the
# database and schema, DML on its tables, REPLICATION when the source has
# foreign keys or triggers). step3 records what was actually needed.
resource "google_alloydb_user" "dms" {
  cluster        = google_alloydb_cluster.main.id
  user_id        = local.dms_db_user
  user_type      = "ALLOYDB_BUILT_IN"
  password       = random_password.dms.result
  database_roles = ["alloydbsuperuser"]
  depends_on     = [google_alloydb_user.student] # one cluster operation at a time
}

# -----------------------------------------------------------------------------
# Cloud SQL for SQL Server: the legacy source
# -----------------------------------------------------------------------------
resource "google_sql_database_instance" "legacy" {
  name                = local.sql_instance
  region              = var.gcp_region
  database_version    = var.sqlserver_version
  root_password       = random_password.sqlserver.result # the `sqlserver` login
  deletion_protection = false

  settings {
    tier              = var.sqlserver_tier
    edition           = "ENTERPRISE" # Cloud SQL edition, not the SQL Server license
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 20

    backup_configuration {
      enabled = false # not needed for CDC (DMS reads the change tables); saves create time
    }

    # Public IP, allowlisted for DMS only. Notebooks and Cloud Shell come in
    # through the Cloud SQL connector or Auth Proxy, which need no allowlist.
    ip_configuration {
      ipv4_enabled = true

      dynamic "authorized_networks" {
        for_each = local.dms_ips
        content {
          name  = "dms-${authorized_networks.key}"
          value = "${authorized_networks.value}/32"
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Staging bucket: the .bak is copied here and imported from here, because
# `gcloud sql import bak` reads AS THE INSTANCE'S SERVICE ACCOUNT, which can't
# be granted anything on the course bucket in another project.
# -----------------------------------------------------------------------------
resource "google_storage_bucket" "staging" {
  name                        = local.staging_bucket
  location                    = upper(var.gcp_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  depends_on                  = [google_project_service.apis]
}

# objectAdmin: export (package build) writes, import (Start Lab) reads.
resource "google_storage_bucket_iam_member" "sql_uses_staging" {
  bucket = google_storage_bucket.staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_sql_database_instance.legacy.service_account_email_address}"
}

# -----------------------------------------------------------------------------
# Secrets
# -----------------------------------------------------------------------------
resource "google_secret_manager_secret" "sqlserver" {
  secret_id = "cymbalgoal-sqlserver-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "sqlserver" {
  secret      = google_secret_manager_secret.sqlserver.id
  secret_data = random_password.sqlserver.result
}

resource "google_secret_manager_secret" "dms" {
  secret_id = "cymbalgoal-dms-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "dms" {
  secret      = google_secret_manager_secret.dms.id
  secret_data = random_password.dms.result
}
