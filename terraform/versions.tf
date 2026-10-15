# Version pins. Terraform 1.12.1 is what the lab platform's Terraform runtime runs
# (runtime.yaml); google provider 7.x is needed for google_alloydb_user. Test by
# hand on the SAME Terraform version, not latest.
#
# google-beta carries exactly the AlloyDB instances (primary and read pool):
# observability_config exists only there at 7.35.0 (read from the provider
# source for mkt015). Everything else stays on GA.

terraform {
  required_version = ">= 1.12.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.35.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "7.35.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.7.2"
    }
  }
}
