# Two identities, and the split is the point. The nodes hold one, which exists
# only to write logs and pull images. The workload holds another through
# workload identity, which is where any permission the gateway itself needs
# would go.

resource "google_service_account" "nodes" {
  account_id   = "${var.name_prefix}-nodes"
  display_name = "GKE nodes for the AI gateway"
  description  = "Attached to the node pool. Holds no data permission."
}

# The default compute service account is project editor. A node pool left on
# it can read every bucket and every secret in the project, so a container
# escape on any pod is a project compromise. This is the single most common
# finding in a GKE review and it is one argument away from happening.
resource "google_project_iam_member" "nodes_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_service_account" "gateway" {
  account_id   = "${var.name_prefix}-workload"
  display_name = "The gateway workload itself"
  description  = "Bound to the Kubernetes service account through workload identity."
}

# The binding is what makes the Kubernetes service account able to act as this
# one. The member string has to name the exact namespace and service account,
# because a wildcard here would let any pod in the cluster assume it.
resource "google_service_account_iam_member" "gateway_workload_identity" {
  service_account_id = google_service_account.gateway.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.namespace}/${var.kubernetes_service_account}]"
}

# The provider key for the vendor lives in Secret Manager and the workload
# reads it at startup. Only this identity gets the accessor role and only on
# the one secret, rather than the project wide role that would also grant
# every other secret.
resource "google_secret_manager_secret" "provider_key" {
  secret_id = "${var.name_prefix}-provider-key"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "gateway_reads_provider_key" {
  secret_id = google_secret_manager_secret.provider_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.gateway.email}"
}
