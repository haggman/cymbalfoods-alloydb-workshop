# Variables the lab platform injects at Start Lab.
#
# ⚠️ RULE: every variable without a default must be one the platform actually
# supplies, under the exact name it uses. A required variable the runtime does
# not know fails the apply for every student in the room at once.
#
#   gcp_project_id, gcp_region, gcp_zone   injected automatically
#   username                                injected ONLY because qwiklabs.yaml maps it
#                                           (startup_script.custom_properties:
#                                            username -> user_0.local_username)

variable "gcp_project_id" {
  description = "Project the lab platform provisions for the student."
  type        = string
}

variable "gcp_region" {
  description = <<-EOT
    Deployment region. us-central1 or us-east1 only: ai.embedding() calls the model
    from the cluster's own region, so the region must serve gemini-embedding-001.
  EOT
  type        = string
  default     = "us-central1"

  validation {
    condition     = contains(["us-central1", "us-east1"], var.gcp_region)
    error_message = "Region must be us-central1 or us-east1 (embedding model availability)."
  }
}

variable "gcp_zone" {
  description = "Injected by the platform. Nothing uses it; declared so the injection never fails."
  type        = string
  default     = "us-central1-a"
}

# ⚠️ `username`: the platform's local_username is the LOCAL PART only
# ("student-03-abc123"); user_0.username is the full address. main.tf appends the
# domain only when it is missing, so either works. AlloyDB does not check that an
# IAM principal exists: a wrong value applies cleanly and the student simply has
# no database login. So the shape matters, but must not be fatal.
variable "username" {
  description = "Student lab username: local part (student-03-abc123) or full address."
  type        = string

  validation {
    condition     = length(regexall("@", var.username)) <= 1
    error_message = "username must be the local part (student-03-abc123) or one full address."
  }

  validation {
    condition     = length(trimspace(var.username)) > 0
    error_message = "username is empty, so the student would get no database login."
  }
}

# ---- Module switches: set by each lab's thin root ---------------------------

variable "lab_module" {
  description = "Which Track A lab this is. Picks the column of the feature map in modules.tf."
  type        = string
  default     = "aldb002" # the module's behavior before the switches existed

  validation {
    condition     = contains(["aldb001", "aldb002", "aldb003", "aldb004", "aldb005"], var.lab_module)
    error_message = "lab_module must be one of aldb001 through aldb005."
  }
}

variable "track" {
  description = "Workshop track. cloud is Track A; omni is reserved for Track B and not built."
  type        = string
  default     = "cloud"

  validation {
    condition     = var.track == "cloud"
    error_message = "Only track = \"cloud\" is built. omni is reserved for Track B."
  }
}

# ---- Tunables: not injected, defaults are the shipped values ----------------

variable "cpu_count" {
  description = <<-EOT
    Primary instance vCPUs. Cluster + instance creation is ~9 min at 8 vCPU and is
    almost all of provisioning time. Every load and index timing is measured at 8.
  EOT
  type        = number
  default     = 8
}

variable "student_email_domain" {
  description = "Domain appended to username. Override only when applying in a non-lab project."
  type        = string
  default     = "qwiklabs.net"
}

# ⚠️ Columnar engine: OFF unless a module teaches it. With it on, auto-columnarization
# (hourly by default) loads busy tables into the column store, and the planner then
# prices a full columnar scan below the ScaNN and HNSW indexes: vector searches drop
# to 0.6-2.2 s full scans with exact sorts (measured in pipeline/04, Oct 2026).
# Turning it on needs an instance restart, so a module that teaches it should set
# this at provisioning. When on, auto-columnarization stays off and the module adds
# its tables to the column store explicitly.
variable "enable_columnar_engine" {
  description = "Turn on the AlloyDB columnar engine (auto-columnarization stays off)."
  type        = bool
  default     = false
}

variable "setup_machine_type" {
  description = <<-EOT
    Setup VM shape (aldb001, 003, 004, 005). It streams files and runs SQL, so it
    needs little; in aldb005 it also runs the deadline-day workload, which mkt015
    ran from Cloud Shell. e2 keeps capacity risk low across a full room.
  EOT
  type        = string
  default     = "e2-standard-4"
}
