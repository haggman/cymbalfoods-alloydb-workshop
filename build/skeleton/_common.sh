# Sourced by the skeleton run scripts. Installs the Terraform version the lab
# platform runs (runtime.yaml) and sets the values every script needs.
TF_VERSION=1.12.1
export PATH="$HOME/bin:$PATH"
if ! terraform version 2>/dev/null | head -1 | grep -q "v${TF_VERSION}$"; then
  echo "Installing Terraform ${TF_VERSION}..."
  mkdir -p "$HOME/bin"
  curl -fsSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" -o /tmp/tf.zip
  unzip -oq /tmp/tf.zip -d "$HOME/bin" && rm -f /tmp/tf.zip
fi
REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
export PROJECT="$(gcloud config get-value project 2>/dev/null)"
ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
if [ -z "${PROJECT}" ] || [ "${PROJECT}" = "(unset)" ]; then
  echo "No project set. Run: gcloud config set project <lab project id>"; exit 1
fi

# A clean copy of the module in a work folder, with a tfvars for this project.
# $1 = work folder, $2 = lab_module
stage_module() {
  rm -rf "$1"; mkdir -p "$1"
  cp -r "${REPO}/terraform/." "$1/"
  rm -rf "$1/.terraform" "$1/.terraform.lock.hcl" "$1"/terraform.tfstate* "$1"/bootstrap/workload/out
  printf 'gcp_project_id = "%s"\ngcp_region     = "%s"\ngcp_zone       = "%s"\nusername       = "%s"\nlab_module     = "%s"\n' \
    "${PROJECT}" "${REGION}" "${ZONE}" "${ACCOUNT}" "$2" > "$1/terraform.tfvars"
}
