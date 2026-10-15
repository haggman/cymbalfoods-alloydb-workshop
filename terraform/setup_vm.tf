# =============================================================================
# Setup VM: the lab's data is loaded before the student arrives
# =============================================================================
# aldb001, 003, 004 and 005 (modules.tf: setup_vm). Start Lab only waits for the
# apply, not for this VM, so the student's Task 0 runs `task0_check` (an output)
# to see "ready", what is still loading, or what failed.
#
# HOW IT WORKS
#   1. Terraform uploads bootstrap/ from THIS module to the staging bucket, so
#      the code the VM runs is always the module version Start Lab applied. No
#      git clone at boot, and no second tag to keep in step.
#   2. The VM boots after the instance and both database users exist, installs
#      the Python connector, and runs bootstrap/bootstrap.py with the settings
#      in its `cg-config` metadata.
#   3. bootstrap.py connects over the private IP as the VM's service account,
#      becomes a member of the student's database role, and creates the
#      database and everything in it AS the student (SET ROLE). The student
#      owns all of it; see the ownership note at the top of main.tf.
#   4. It writes progress to gs://<staging bucket>/setup/status, then powers
#      the VM off. In aldb005 it stays up and runs the deadline-day workload.
#
# A failed run leaves the VM running so an instructor can look; the log is at
# gs://<staging bucket>/setup/bootstrap.log. `gcloud compute instances reset`
# re-runs it, and every step skips what is already done.
# =============================================================================

locals {
  setup_db_user = "cymbalgoal-setup@${var.gcp_project_id}.iam" # AlloyDB drops .gserviceaccount.com

  # Text files only (they are uploaded with `content`, so a changed file is a
  # changed object). Bytecode never ships.
  setup_code = local.f.setup_vm ? toset([
    for p in fileset("${path.module}/bootstrap", "**") : p
    if !strcontains(p, "__pycache__") && !endswith(p, ".pyc") && !endswith(p, ".DS_Store")
  ]) : toset([])

  setup_config = {
    module         = var.lab_module
    project        = var.gcp_project_id
    region         = var.gcp_region
    cluster        = local.cluster_id
    instance       = local.instance_id
    student        = local.student_email
    setup_db_user  = local.setup_db_user
    bucket         = local.staging_bucket
    database       = "cymbalgoal"
    core_src       = "gs://class-demo/alloydb-labs/cymbalgoal"                  # read-only, another team's labs use it
    reports_src    = "gs://class-demo/alloydb-labs/cymbalgoal-alloydb-workshop/scout-reports/staged"
    datasets       = local.f.datasets
    indexes        = local.f.indexes
    load_method    = local.f.load_method
    data_api       = local.f.data_api
    load_generator = local.f.load_generator
  }

  task0_check = "gcloud storage cat gs://$(gcloud config get-value project 2>/dev/null)-cymbalgoal-staging/setup/status 2>/dev/null || echo \"starting: the setup VM has not reported yet\""
}

# -----------------------------------------------------------------------------
# Identity: a service account, its project roles, and its database login
# -----------------------------------------------------------------------------
resource "google_service_account" "setup" {
  count        = local.f.setup_vm ? 1 : 0
  account_id   = "cymbalgoal-setup"
  display_name = "CymbalGoal setup VM"
  depends_on   = [google_project_service.apis]
}

# alloydb.admin: clusters.import, the dataApiAccess PATCH (aldb003), and adding
# the student's role to its own database user. client + databaseUser: connect
# and log in with IAM. serviceUsageConsumer: the connector's API calls.
resource "google_project_iam_member" "setup" {
  for_each = local.f.setup_vm ? toset([
    "roles/alloydb.admin",
    "roles/alloydb.client",
    "roles/alloydb.databaseUser",
    "roles/serviceusage.serviceUsageConsumer",
  ]) : toset([])
  project = var.gcp_project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.setup[0].email}"
}

# Writes the status marker and the copied source files. Reading class-demo needs
# no grant: that bucket is anonymous read.
resource "google_storage_bucket_iam_member" "setup_writes_staging" {
  count  = local.f.setup_vm ? 1 : 0
  bucket = google_storage_bucket.staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.setup[0].email}"
}

# The VM's database login. bootstrap.py adds the STUDENT's role to this user's
# database_roles through the Admin API at run time, so the VM can SET ROLE to
# the student. Doing it here instead would fail the whole apply if AlloyDB
# refused a non-built-in role name; at run time a refusal falls back to a SQL
# GRANT and is reported. ignore_changes keeps that run-time addition from
# reading as drift.
resource "google_alloydb_user" "setup" {
  count     = local.f.setup_vm ? 1 : 0
  cluster   = google_alloydb_cluster.main.id
  user_id   = local.setup_db_user
  user_type = "ALLOYDB_IAM_USER"

  # Same two re-apply traps as the student user: complete set, alphabetical.
  database_roles = ["alloydbiamuser", "alloydbsuperuser"]

  lifecycle {
    ignore_changes = [database_roles]
  }

  # One user operation at a time on the cluster.
  depends_on = [google_alloydb_user.student]
}

# -----------------------------------------------------------------------------
# Network: a subnet for the VM, and SSH through IAP only
# -----------------------------------------------------------------------------
# 192.168.10.0/24 sits outside 10.0.0.0/8, where the peering range is usually
# allocated, and main.tf makes that range wait for this subnet anyway.
resource "google_compute_subnetwork" "setup" {
  count                    = local.f.setup_vm ? 1 : 0
  name                     = "cymbalgoal-setup"
  region                   = var.gcp_region
  network                  = google_compute_network.main.id
  ip_cidr_range            = "192.168.10.0/24"
  private_ip_google_access = true
}

resource "google_compute_firewall" "setup_iap_ssh" {
  count         = local.f.setup_vm ? 1 : 0
  name          = "cymbalgoal-setup-iap-ssh"
  network       = google_compute_network.main.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"] # IAP TCP forwarding
  target_tags   = ["cymbalgoal-setup"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# -----------------------------------------------------------------------------
# The code the VM runs, uploaded from this module
# -----------------------------------------------------------------------------
resource "google_storage_bucket_object" "setup_code" {
  for_each = local.setup_code
  bucket   = google_storage_bucket.staging.name
  name     = "setup/code/${each.value}"
  content  = file("${path.module}/bootstrap/${each.value}")
}

# -----------------------------------------------------------------------------
# The VM
# -----------------------------------------------------------------------------
resource "google_compute_instance" "setup" {
  count        = local.f.setup_vm ? 1 : 0
  name         = "cymbalgoal-setup"
  machine_type = var.setup_machine_type
  zone         = var.gcp_zone
  tags         = ["cymbalgoal-setup"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  # External IP: apt and PyPI at boot. Nothing listens on it, and the only
  # ingress rule is IAP SSH.
  network_interface {
    subnetwork = google_compute_subnetwork.setup[0].id
    access_config {}
  }

  service_account {
    email  = google_service_account.setup[0].email
    scopes = ["cloud-platform"]
  }

  metadata = {
    startup-script = file("${path.module}/bootstrap/startup.sh")
    cg-config      = jsonencode(local.setup_config)
    enable-oslogin = "TRUE"
  }

  allow_stopping_for_update = true

  # The VM powers itself off when it finishes (all but aldb005). That is the
  # expected state, not drift.
  lifecycle {
    ignore_changes = [desired_status]
  }

  # Created after everything it uses exists: the instance and read pool (no
  # instance operation still running), both database users, its roles (IAM
  # takes a minute to propagate; bootstrap.py retries through that), and its code.
  depends_on = [
    google_alloydb_instance.primary,
    google_alloydb_instance.read_pool,
    google_alloydb_user.student,
    google_alloydb_user.setup,
    google_project_iam_member.setup,
    google_storage_bucket_iam_member.setup_writes_staging,
    google_storage_bucket_object.setup_code,
    google_compute_firewall.setup_iap_ssh,
  ]
}
