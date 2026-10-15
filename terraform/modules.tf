# =============================================================================
# Module switches: one feature map, five labs
# =============================================================================
# `lab_module` picks one column of this map. Every resource that only some labs
# need reads its switch from `local.f`, so a lab's thin root sets one variable
# and nothing else. With lab_module = "aldb002" every switch is off and the plan
# is what this module provisioned before the switches existed.
#
# ⚠️ STUBS are marked. A stub switch is false here and nothing behind it exists
# yet; the feature checks for that module decide what goes in.
#
# ⚠️ extra_apis: a wrong service string halts the apply for every student at
# once. Add only strings already proven at Start Lab or read from
# `gcloud services list --available`.
#
# datasets  what the setup VM loads (bootstrap/bootstrap.py):
#   core           the eight relational tables (gs://class-demo/.../cymbalgoal)
#   profiles       player and club profile text and embeddings (needs core)
#   scout_reports  the 38,445-report corpus (needs core: it has foreign keys)
# indexes   what the setup VM builds after loading:
#   none | baseline (mkt015's six B-trees) | scann (indexes.sql + scout-report
#   B-trees and a ScaNN index on report_embedding)
# =============================================================================

locals {
  modules = {
    # Foundations: migrate, connect, scale
    aldb001 = {
      setup_vm       = true
      datasets       = []     # AlloyDB starts empty: DMS fills it in the lab
      indexes        = "none"
      load_method    = "import"
      data_api       = false
      read_pool      = true   # one node; the lab adds the second
      ha             = false
      columnar       = false
      observability  = false
      load_generator = false
      sqlserver      = false  # ⚠️ STUB: Cloud SQL for SQL Server + .bak + CDC, set by state/module1-checks-handback.md
      bq_history     = false
      extra_apis = [
        "iap.googleapis.com",
        "datamigration.googleapis.com", # Database Migration Service
        "sqladmin.googleapis.com",      # Cloud SQL (the SQL Server source)
      ]
      student_roles = []
    }

    # Search and AI. No setup VM: the notebook creates and loads the database
    # in Task 1, because loading is part of the lesson.
    aldb002 = {
      setup_vm       = false
      datasets       = []
      indexes        = "none"
      load_method    = "import"
      data_api       = false
      read_pool      = false
      ha             = false
      columnar       = false
      observability  = false
      load_generator = false
      sqlserver      = false
      bq_history     = false
      extra_apis     = []
      student_roles  = []
    }

    # Agents: QueryData, managed MCP, ADK, secure views
    aldb003 = {
      setup_vm       = true
      datasets       = ["core", "profiles", "scout_reports"]
      indexes        = "scann"
      load_method    = "import"
      data_api       = true # REST PATCH from the VM; no Terraform or gcloud surface
      read_pool      = false
      ha             = false
      columnar       = false
      observability  = false
      load_generator = false
      sqlserver      = false
      bq_history     = false
      extra_apis = [
        "iap.googleapis.com",
        "geminidataanalytics.googleapis.com", # QueryData and context sets (mkt014, proven)
        "cloudaicompanion.googleapis.com",    # Studio's Gemini panel (mkt014, proven)
        "dataplex.googleapis.com",            # Knowledge Catalog (mkt014, proven)
        "modelarmor.googleapis.com",          # the optional Model Armor task
      ]
      # mkt014's three, carried for the same reason: newer permissions are not
      # always inside roles/owner. Proven at Start Lab in mkt014.
      student_roles = [
        "roles/alloydb.databaseUser",
        "roles/geminidataanalytics.queryDataUser",
        "roles/serviceusage.serviceUsageConsumer",
      ]
    }

    # Data cloud: federation, Datastream, Knowledge Catalog, Gemini Enterprise
    aldb004 = {
      setup_vm       = true
      datasets       = ["core"]
      indexes        = "baseline"
      load_method    = "import"
      data_api       = false
      read_pool      = false
      ha             = false
      columnar       = false
      observability  = false
      load_generator = false
      sqlserver      = false
      bq_history     = false # ⚠️ STUB: BigQuery history datasets, Iceberg tables, Datastream prerequisites
      extra_apis = [
        "iap.googleapis.com",
        "bigquery.googleapis.com",
        "bigqueryconnection.googleapis.com",
        "datastream.googleapis.com",
        "dataplex.googleapis.com", # Knowledge Catalog
        # discoveryengine (Gemini Enterprise) is already in the base list
      ]
      student_roles = []
    }

    # Deadline day: operations under load
    aldb005 = {
      setup_vm       = true
      datasets       = ["core", "profiles"] # profiles make `players` a genuinely fat table (mkt015)
      indexes        = "baseline"
      load_method    = "import"
      data_api       = false # restarts the instance and resets pg_stat_statements (mkt015, measured)
      read_pool      = false
      ha             = true  # the failover task needs the standby
      columnar       = true  # engine on, auto-columnarization off: the lab populates it
      observability  = true  # enhanced query insights, google-beta
      load_generator = true  # the VM stays up and runs the deadline-day workload
      sqlserver      = false
      bq_history     = false
      extra_apis     = ["iap.googleapis.com"]
      student_roles  = []
    }
  }

  # The column this lab uses.
  f = local.modules[var.lab_module]
}
