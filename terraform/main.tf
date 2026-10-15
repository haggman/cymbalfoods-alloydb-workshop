# =============================================================================
# CymbalGoal AlloyDB Workshop — shared provisioning for Track A (aldb001–aldb005)
# =============================================================================
# One module, five labs. `lab_module` picks a column of the feature map in
# modules.tf, and every resource that only some labs need reads its switch from
# it. With lab_module = "aldb002" (the default) this provisions exactly what it
# did before the switches existed: the cluster, its primary instance, the
# student's database login, and a staging bucket AlloyDB can import from.
#
# Terraform cannot run SQL: there is no google_alloydb_database resource and the
# runner is outside the VPC. Labs whose data must be there when the student
# arrives get the setup VM (setup_vm.tf), which loads it from inside the VPC.
# aldb002 has no VM: its notebook builds the database in Task 1.
#
# ⚠️ Whoever creates a database owns it, and alloydbsuperuser is not a true
# superuser, so a database owned by `postgres` or a service account cannot be
# fully managed by the student. The setup VM therefore creates everything AS the
# student (SET ROLE), never as itself. See bootstrap/bootstrap.py.
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
# The base list every module gets, plus the module's extra_apis (modules.tf).
resource "google_project_service" "apis" {
  for_each = toset(concat([
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
  ], local.f.extra_apis))
  service            = each.value
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# Network: AlloyDB is VPC-native and needs Private Service Access even when it is
# reached over its public IP. Notebooks and Cloud Shell need no subnet, NAT or
# firewall rules; the setup VM's subnet and SSH rule are in setup_vm.tf.
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

  # The setup subnet (when there is one) exists first, so the automatically
  # allocated peering range can never overlap it.
  depends_on = [google_project_service.apis, google_compute_subnetwork.setup]
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
locals {
  # On when the module teaches it (aldb005), or when the variable says so for a
  # hand apply. variables.tf explains why it is off everywhere else.
  columnar = var.enable_columnar_engine || local.f.columnar

  # ⚠️ Every name verified against GET .../supportedDatabaseFlags. One unknown name
  # and AlloyDB rejects the WHOLE instance create: every student gets a cluster
  # with no instance. Never add an unchecked name. Flags are per-instance, so the
  # read pool below uses this same map.
  instance_flags = merge({
    "password.enforce_complexity"                       = "on" # mandatory with public IP
    "alloydb.iam_authentication"                        = "on" # lets the IAM users log in
    "google_ml_integration.enable_preview_ai_functions" = "on" # ai.* preview functions (PG 17/18)
    # Already on by default, so not set: google_ml_integration.enable_model_support,
    # enable_ai_query_engine, enable_faster_embedding_generation.
    }, local.columnar ? {
    "google_columnar_engine.enabled" = "on"
    # Shipped in mkt015's instance flags, so the name is proven. Off means the
    # engine is ready but nothing is columnarized behind the lab's back: the
    # planner keeps using real indexes until a task adds tables to the column
    # store. Turning the engine itself on needs a restart; this one does not.
    "google_columnar_engine.enable_auto_columnarization" = "off"
  } : {})
}

resource "google_alloydb_instance" "primary" {
  # google-beta in every module: observability_config (aldb005) exists only in
  # the beta provider at 7.35.0, and a resource cannot switch providers per
  # module. For the other modules only the provider address differs.
  provider = google-beta

  cluster       = google_alloydb_cluster.main.name
  instance_id   = local.instance_id
  instance_type = "PRIMARY"

  # ⚠️ ZONAL unless the module teaches failover. A PRIMARY defaults to REGIONAL:
  # an HA pair, which doubles the capacity every student needs scheduled at the
  # same moment. Measured in us-central1: "does not have enough resources
  # available to fulfill the request". aldb005's failover task needs the pair
  # and takes that risk; no other module does.
  # ⚠️ Do NOT add gce_zone: pinning a zone narrows where capacity can be found.
  availability_type = local.f.ha ? "REGIONAL" : "ZONAL"

  machine_config {
    cpu_count = var.cpu_count
  }

  # Public IP, reached only through the AlloyDB Python Connector with IAM auth.
  # IAM gates access and the connector carries mTLS, so there are deliberately NO
  # authorized networks. Any Colab runtime can connect; no VPC attachment needed.
  # The setup VM uses the private IP from inside the VPC.
  network_config {
    enable_public_ip = true
  }

  database_flags = local.instance_flags

  # Standard Query Insights, explicit where a module reads it (aldb005). Values
  # carried from mkt015: long statements keep their sqlcommenter tags.
  dynamic "query_insights_config" {
    for_each = local.f.observability ? [1] : []
    content {
      record_application_tags = true
      record_client_address   = true
      query_plans_per_minute  = 20
      query_string_length     = 4500
    }
  }

  # Enhanced query insights (aldb005), carried from mkt015. ⚠️ track_active_queries
  # defaults OFF; without it the Active Queries view has no data.
  dynamic "observability_config" {
    for_each = local.f.observability ? [1] : []
    content {
      enabled                 = true
      track_wait_events       = true
      track_wait_event_types  = true
      track_active_queries    = true
      preserve_comments       = true # keeps the workload's sqlcommenter tags
      record_application_tags = true
      track_client_address    = true
      query_plans_per_minute  = 20
      max_query_string_length = 20000
      # ⚠️ true FAILS the instance create without Gemini Cloud Assist (mkt015,
      # measured), and it is not what gates the console's AI troubleshooting.
      assistive_experiences_enabled = false
    }
  }

  depends_on = [google_service_networking_connection.psa]
}

# -----------------------------------------------------------------------------
# Read pool (aldb001): one node at arrival; the lab scales it
# -----------------------------------------------------------------------------
# Flags are per-instance, so it gets the primary's map verbatim. Public IP for
# the same reason as the primary: the lab routes reads to it from Cloud Shell.
resource "google_alloydb_instance" "read_pool" {
  count    = local.f.read_pool ? 1 : 0
  provider = google-beta

  cluster       = google_alloydb_cluster.main.name
  instance_id   = "cymbalgoal-read-pool"
  instance_type = "READ_POOL"

  read_pool_config {
    node_count = 1
  }

  machine_config {
    cpu_count = var.cpu_count
  }

  network_config {
    enable_public_ip = true
  }

  database_flags = local.instance_flags

  depends_on = [google_alloydb_instance.primary]
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

# -----------------------------------------------------------------------------
# Student project roles a module adds (modules.tf: student_roles)
# -----------------------------------------------------------------------------
# The student already has roles/owner. These are the permissions Google has been
# minting outside the basic roles (QueryData in aldb003), granted explicitly
# because finding out at Task 3 that owner was not enough costs the room 25
# minutes. Same three mkt014 grants, proven at Start Lab there.
resource "google_project_iam_member" "student_roles" {
  for_each   = toset(local.f.student_roles)
  project    = var.gcp_project_id
  role       = each.value
  member     = "user:${local.student_email}"
  depends_on = [google_alloydb_cluster.main]
}
