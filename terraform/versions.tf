# Version pins. Terraform 1.12.1 is what the lab platform's Terraform runtime runs
# (runtime.yaml); google provider 7.x is needed for observability_config and
# google_alloydb_user. Test by hand on the SAME Terraform version, not latest.

terraform {
  required_version = ">= 1.12.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.35.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.7.2"
    }
  }
}
