# Same injected-variable contract as terraform/: only gcp_project_id, gcp_region,
# gcp_zone and username are supplied; everything else has a default.

variable "gcp_project_id" {
  description = "Lab project."
  type        = string
}

variable "gcp_region" {
  description = "Region for AlloyDB, Cloud SQL and DMS. All three must match."
  type        = string
  default     = "us-central1"

  validation {
    condition     = contains(["us-central1", "us-east1"], var.gcp_region)
    error_message = "Region must be us-central1 or us-east1 (the DMS allowlist below covers only these)."
  }
}

variable "gcp_zone" {
  description = "Injected by the platform; unused."
  type        = string
  default     = "us-central1-a"
}

variable "username" {
  description = "Student lab username: local part (student-03-abc123) or full address."
  type        = string
}

variable "student_email_domain" {
  description = "Appended to username when it has no @."
  type        = string
  default     = "qwiklabs.net"
}

# ---- AlloyDB --------------------------------------------------------------------

variable "cpu_count" {
  description = "Primary instance vCPUs (the workshop standard)."
  type        = number
  default     = 8
}

variable "read_pool_cpu_count" {
  description = "vCPUs per read-pool node. 2 is the smallest N2 shape; keeps per-student capacity low."
  type        = number
  default     = 2
}

variable "read_pool_node_count" {
  description = "Nodes at Start Lab. Task 6 has the student add one."
  type        = number
  default     = 1
}

# ---- Cloud SQL for SQL Server ---------------------------------------------------

variable "sqlserver_version" {
  description = <<-EOT
    CDC (needed for a DMS continuous job) exists only on Standard and Enterprise,
    2017-2022. Standard licenses at $0.13 per core-hour against Enterprise's $0.47.
    The .bak is built on 2022 and cannot be restored onto an older version.
  EOT
  type        = string
  default     = "SQLSERVER_2022_STANDARD"
}

variable "sqlserver_tier" {
  description = "Licenses bill a 4-core minimum whatever the shape, so 2 vCPU costs the same license as 4."
  type        = string
  default     = "db-custom-2-8192"
}
