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

No database, schema, data or indexes: those are the notebooks' job.

Cluster and instance creation takes about **9 minutes** and is almost all of the apply.

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
