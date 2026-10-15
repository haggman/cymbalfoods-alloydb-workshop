# =============================================================================
# CymbalGoal AlloyDB Workshop — cluster provisioning
# =============================================================================
# Provisions the CLUSTER, its primary instance, the student's database login, and
# a staging bucket AlloyDB can import from. Nothing that needs SQL: Terraform has
# no google_alloydb_database resource and its runner is outside the VPC. The
# database, schema, data load and indexes are done from the notebooks, which
# reach the cluster through the AlloyDB Python Connector with IAM auth.
#
# A database created by provisioning would also be owned by `postgres`, and
# alloydbsuperuser is not a true superuser, so the student could not fully manage
# it. The student creates their own database instead.
#
# Apply twice when testing: `terraform plan -detailed-exitcode` must exit 0 on the
# second run. Two traps below only show up on a re-apply.
# =============================================================================

locals {
  cluster_id     = "cymbalgoal-cluster"
  instance_id    = "cymbalgoal-primary"
  network        = "cymbalgoal-network"
  staging_bucket = "${var.gcp_project_id}-cymbalgoal-staging"

  # Append the domain ONLY if it is not already there ("x@qwiklabs.net@qwiklabs.net"
  # would apply cleanly and log in nobody).
  student_email = can(regex("@", var.username)) ? var.username : "${var.username}@${var.student_email_domain}"

  alloydb_service_agent = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-alloydb.iam.gserviceaccount.com"
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
    "aiplatform.googleapis.com", # model calls from SQL, and Colab Enterprise runtimes
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
    "monitoring.googleapis.com", # instance CPU, read by the benchmark notebooks
    # ai.rank()'s semantic reranker is served by Discovery Engine. Enabling the API
    # is necessary but NOT sufficient: see alloydb_discoveryengine below.
    "discoveryengine.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# Network: AlloyDB is VPC-native and needs Private Service Access even when it is
# reached over its public IP. No subnet, NAT or firewall rules are needed.
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
# Cluster
# -----------------------------------------------------------------------------
# AlloyDB requires an initial user with a password even in an IAM-only setup. It
# is generated, never used, and appears in no output.
resource "random_password" "initial" {
  length           = 24
  special          = true
  override_special = "-_=+"
}

resource "google_alloydb_cluster" "main" {
  cluster_id       = local.cluster_id
  location         = var.gcp_region
  database_version = "POSTGRES_18" # pinned, never left to the default

  network_config {
    network = google_compute_network.main.id
  }

  initial_user {
    user     = "postgres"
    password = random_password.initial.result
  }

  depends_on = [google_service_networking_connection.psa]
}

# -----------------------------------------------------------------------------
# Primary instance
# -----------------------------------------------------------------------------
resource "google_alloydb_instance" "primary" {
  cluster       = google_alloydb_cluster.main.name
  instance_id   = local.instance_id
  instance_type = "PRIMARY"

  # ⚠️ NOT the default. A PRIMARY defaults to REGIONAL: an HA pair, which doubles
  # the capacity every student needs scheduled at the same moment. Measured in
  # us-central1: "does not have enough resources available to fulfill the
  # request". ZONAL is one node; nothing in the workshop needs failover.
  # ⚠️ Do NOT add gce_zone: pinning a zone narrows where capacity can be found.
  availability_type = "ZONAL"

  machine_config {
    cpu_count = var.cpu_count
  }

  # Public IP, reached only through the AlloyDB Python Connector with IAM auth.
  # IAM gates access and the connector carries mTLS, so there are deliberately NO
  # authorized networks. Any Colab runtime can connect; no VPC attachment needed.
  network_config {
    enable_public_ip = true
  }

  # ⚠️ Every name verified against GET .../supportedDatabaseFlags. One unknown name
  # and AlloyDB rejects the WHOLE instance create: every student gets a cluster
  # with no instance. Never add an unchecked name. Flags are per-instance: a read
  # pool would need every one repeated.
  database_flags = merge({
    "password.enforce_complexity"                       = "on" # mandatory with public IP
    "alloydb.iam_authentication"                        = "on" # lets the IAM user log in
    "google_ml_integration.enable_preview_ai_functions" = "on" # ai.* preview functions (PG 17/18)
    # Already on by default, so not set: google_ml_integration.enable_model_support,
    # enable_ai_query_engine, enable_faster_embedding_generation.
    }, var.enable_columnar_engine ? {
    "google_columnar_engine.enabled" = "on"
    # ⚠️ Not yet checked against supportedDatabaseFlags: verify before the first
    # apply with enable_columnar_engine = true (see variables.tf for why it's here).
    "google_columnar_engine.enable_auto_columnarization" = "off"
  } : {})

  depends_on = [google_service_networking_connection.psa]
}

# -----------------------------------------------------------------------------
# ⚠️ Service-agent bindings. The agent does not exist until the cluster does, so
# each needs depends_on. Without the first, every ai.embedding() call fails: the
# cluster builds, the load succeeds, and the first semantic query dies.
# -----------------------------------------------------------------------------
resource "google_project_iam_member" "alloydb_vertex" {
  project    = var.gcp_project_id
  role       = "roles/aiplatform.user"
  member     = local.alloydb_service_agent
  depends_on = [google_alloydb_cluster.main]
}

# ai.rank()'s reranker. ⚠️ The role name is counterintuitive and was verified:
# discoveryengine.viewer HAS rankingConfigs.rank; discoveryengine.user does NOT.
resource "google_project_iam_member" "alloydb_discoveryengine" {
  project    = var.gcp_project_id
  role       = "roles/discoveryengine.viewer"
  member     = local.alloydb_service_agent
  depends_on = [google_alloydb_cluster.main]
}

# -----------------------------------------------------------------------------
# Staging bucket for server-side imports
# -----------------------------------------------------------------------------
# `gcloud alloydb clusters import` reads the file AS THE ALLOYDB SERVICE AGENT,
# not as the student, so the agent needs objectViewer on the source bucket. The
# course bucket belongs to another project, and a student project cannot grant
# itself access there. This in-project bucket is the place a notebook copies the
# staged corpus to (a fast server-side copy) and imports it from.
resource "google_storage_bucket" "staging" {
  name                        = local.staging_bucket
  location                    = upper(var.gcp_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "alloydb_reads_staging" {
  bucket     = google_storage_bucket.staging.name
  role       = "roles/storage.objectViewer"
  member     = local.alloydb_service_agent
  depends_on = [google_alloydb_cluster.main]
}

# -----------------------------------------------------------------------------
# Student database user
# -----------------------------------------------------------------------------
# alloydbsuperuser is needed for some extensions and index types.
resource "google_alloydb_user" "student" {
  cluster   = google_alloydb_cluster.main.id
  user_id   = local.student_email
  user_type = "ALLOYDB_IAM_USER"

  # ⚠️ Two traps that only appear on the SECOND apply:
  # 1. alloydbiamuser is granted automatically and this list is the COMPLETE set,
  #    so omitting it reads as a revoke: "Error 400: cannot revoke IAM roles".
  # 2. It is a LIST and the API returns it sorted. Keep it alphabetical, or every
  #    plan shows a permanent diff.
  database_roles = ["alloydbiamuser", "alloydbsuperuser"]

  depends_on = [google_alloydb_instance.primary]
}

# No read pool, deliberately: nothing needs one and it costs provisioning time.
