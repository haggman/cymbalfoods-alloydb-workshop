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
