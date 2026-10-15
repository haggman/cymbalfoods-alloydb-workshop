# Terraform — the workshop cluster

Provisions what every module starts from:

- an AlloyDB **PG 18** cluster with one **8 vCPU, zonal** primary instance
- **public IP with no authorized networks**: notebooks connect through the AlloyDB Python
  Connector with IAM auth, so nothing is opened to the internet
- the student's **IAM database user** with `alloydbsuperuser`
- the AlloyDB service agent's roles for **model calls** (`ai.embedding()`) and the
  **reranker** (`ai.rank()`)
- an in-project **staging bucket** the service agent can read, the source for
  `gcloud alloydb clusters import`

That is the whole of `lab_module = "aldb002"`, the default: no database, schema, data or
indexes, because that lab's notebook builds them.

Cluster and instance creation takes about **9 minutes** and is almost all of the apply.

## One module, five labs

Each Track A lab folder in gcp-ce-content has a thin `terraform/` root that calls this module
from GitHub and sets `lab_module`. The switch picks a column of the feature map in
`modules.tf`:

| | aldb001 | aldb002 | aldb003 | aldb004 | aldb005 |
| :-- | :-- | :-- | :-- | :-- | :-- |
| Setup VM | yes (stub) | no | yes | yes | yes, stays up |
| Data loaded at arrival | none | none | core, profiles, scout reports | core | core, profiles |
| Indexes | none | none | `indexes.sql` + scout-report B-tree and ScaNN | mkt015 baseline | mkt015 baseline |
| Read pool | 1 node | | | | |
| HA (REGIONAL) | | | | | yes |
| Columnar engine | | | | | on, auto-columnarization off |
| Enhanced query insights | | | | | yes (google-beta) |
| Data API | | | ENABLED (VM PATCH) | | |
| Deadline-day workload | | | | | VM service |
| Stubbed | SQL Server, DMS | | | BigQuery, Iceberg, Datastream | |

`track` is `cloud` only; `omni` is reserved for Track B.

### The setup VM (`setup_vm.tf`, `bootstrap/`)

Terraform uploads `bootstrap/` to the staging bucket, so the VM always runs the code of the
module version that was applied. The VM connects over the private IP as its own service
account, joins the **student's** database role, and creates the database and everything in it
as the student, so the student owns all of it. It writes one status line to
`gs://<project>-cymbalgoal-staging/setup/status`, which is what Task 0 prints:

```bash
gcloud storage cat gs://$(gcloud config get-value project 2>/dev/null)-cymbalgoal-staging/setup/status 2>/dev/null || echo "starting: the setup VM has not reported yet"
```

It then powers itself off, except in aldb005, where it runs the deadline-day workload. A
failed run leaves the VM up; the log is `setup/bootstrap.log` in the same bucket, and
`gcloud compute instances reset cymbalgoal-setup --zone=<zone>` re-runs it from where it stopped.

### Testing

`build/skeleton/` holds the run scripts: plan parity for aldb002, a measured cold start for a
setup-VM module, validate and plan for the stub modules, and a check to run after Start Lab.

## Applying by hand (development)

Terraform is no longer pre-installed in Cloud Shell. Install the version `runtime.yaml` pins,
so you test what Start Lab runs:

```bash
TF_VERSION=1.12.1
mkdir -p ~/bin
curl -fsSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" -o /tmp/tf.zip
unzip -oq /tmp/tf.zip -d ~/bin && rm /tmp/tf.zip
export PATH="$HOME/bin:$PATH"
terraform version
```

Then copy `terraform.tfvars.example` to `terraform.tfvars`, fill it in, and:

```bash
terraform init && terraform apply
terraform plan -detailed-exitcode   # must exit 0: anything else is drift
```
