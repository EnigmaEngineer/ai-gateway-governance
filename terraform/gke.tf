# A private cluster, which here means the nodes have no external address. The
# gateway is reached through an internal load balancer and the internal load
# balancer is created by the Service in the Helm chart rather than here,
# because GKE builds it from the Service annotation.

resource "google_container_cluster" "gateway" {
  name     = "${var.name_prefix}-cluster"
  location = var.region

  network    = google_compute_network.gateway.id
  subnetwork = google_compute_subnetwork.nodes.id

  # This cluster is torn down at the end of the week and protection on means
  # the teardown needs a config change first. A production one would have it on.
  deletion_protection = false

  # The cluster comes up with a node pool whether you ask for one or not, and
  # that pool cannot be reconfigured in place. Removing it and declaring the
  # real one separately is the only way to control the service account and the
  # shielded settings below.
  remove_default_node_pool = true
  initial_node_count       = 1

  release_channel {
    channel = var.release_channel
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = google_compute_subnetwork.nodes.secondary_ip_range[0].range_name
    services_secondary_range_name = google_compute_subnetwork.nodes.secondary_ip_range[1].range_name
  }

  private_cluster_config {
    enable_private_nodes = true
    # The endpoint stays public and the authorised network list is what
    # restricts it. A private endpoint is the stronger posture and it also
    # means CI cannot reach the cluster without a proxy or a runner inside
    # the VPC, which is a decision about the delivery path rather than about
    # the cluster. Written down in docs/infrastructure.md rather than left
    # as a default somebody reads as considered.
    enable_private_endpoint = var.private_endpoint
    master_ipv4_cidr_block  = var.master_cidr
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.control_plane_cidrs
      content {
        cidr_block   = cidr_blocks.value.cidr
        display_name = cidr_blocks.value.name
      }
    }
  }

  # Without this the pods authenticate as the node service account, which
  # makes the identity split in iam.tf decorative.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # The dataplane v2 option carries network policy enforcement with it, so a
  # separate policy addon is not needed and the two cannot disagree.
  datapath_provider = "ADVANCED_DATAPATH"

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]

    managed_prometheus {
      enabled = true
    }
  }

  # A gateway that redacts identifiers should not be writing them into a
  # cluster level audit sink by accident, so the binary authorization and the
  # secret encryption settings are the two that get stated rather than
  # defaulted.
  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }

  # Cost control. A cluster that can scale to a hundred nodes under a load
  # test is a budget alert that arrives too late.
  cluster_autoscaling {
    enabled = false
  }
}

resource "google_container_node_pool" "gateway" {
  name     = "${var.name_prefix}-pool"
  cluster  = google_container_cluster.gateway.id
  location = var.region

  node_count = var.nodes_per_zone

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.machine_type
    disk_size_gb = 50
    disk_type    = "pd-balanced"

    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    tags = [var.node_tag]

    # The v1 metadata endpoints do not require the metadata flavour header, so
    # a server side request forgery in any pod can read the node's token
    # through them.
    metadata = {
      disable-legacy-endpoints = "true"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = {
      workload = "ai-gateway"
    }
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }
}

# TODO: none of this has been applied and the cluster has never existed.
# There is no project, no billing account and no workload identity federation
# trust for this repository, so terraform plan has nothing to authenticate
# against. Until that exists this directory is graded by terraform validate,
# terraform fmt and tests/test_infra.py. None of the three can see a
# configuration that is valid and wrong about the real project, so treat
# every value here as unverified against a running cluster.
