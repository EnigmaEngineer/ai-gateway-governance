# The gateway sits on its own network rather than in the default one. The
# default network opens 22 and 3389 to the whole internet on creation, and a
# cluster that inherits those rules has already lost the argument about being
# internal.

resource "google_compute_network" "gateway" {
  name                    = "${var.name_prefix}-net"
  auto_create_subnetworks = false
  # Regional routing keeps a route advertised in one region out of the others.
  routing_mode = "REGIONAL"
}

resource "google_compute_subnetwork" "nodes" {
  name          = "${var.name_prefix}-nodes"
  network       = google_compute_network.gateway.id
  region        = var.region
  ip_cidr_range = var.node_cidr

  # Pods and services get their own ranges so the cluster is VPC native. The
  # alternative is route based networking, where every node burns a route in
  # the VPC and the internal load balancer cannot target pods directly.
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pod_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.service_cidr
  }

  # Without this a private node cannot reach Artifact Registry or Cloud
  # Logging at all, because both are Google APIs behind public addresses and
  # the node has no public address.
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Private nodes have no external address, so image pulls and vendor API calls
# leave through NAT. This is the one piece of this file that costs money while
# it is up, and it is the first thing the teardown removes.
resource "google_compute_router" "gateway" {
  name    = "${var.name_prefix}-router"
  network = google_compute_network.gateway.id
  region  = var.region
}

resource "google_compute_router_nat" "gateway" {
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.gateway.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name = google_compute_subnetwork.nodes.id
    # Pods call the LLM provider, so the pod range needs NAT too. Leaving it
    # out is the quiet version of this mistake, because the nodes still reach
    # the registry and only the workload fails.
    source_ip_ranges_to_nat = ["PRIMARY_IP_RANGE", "LIST_OF_SECONDARY_IP_RANGES"]
    secondary_ip_range_names = [
      google_compute_subnetwork.nodes.secondary_ip_range[0].range_name,
    ]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# The internal load balancer health checks arrive from Google's prober ranges
# and not from inside the VPC, so they need their own rule. A missing one
# looks like an application fault, because the backend is healthy and the
# load balancer reports it down.
resource "google_compute_firewall" "health_checks" {
  name      = "${var.name_prefix}-allow-health-checks"
  network   = google_compute_network.gateway.id
  direction = "INGRESS"

  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = [var.node_tag]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.guardrail_port)]
  }
}

# The control plane reaches into the cluster for admission webhooks and for
# metrics scraping. Its range is the one in private_cluster_config and nothing
# else, which is the difference between this and a rule that trusts the VPC.
resource "google_compute_firewall" "control_plane_to_nodes" {
  name      = "${var.name_prefix}-allow-control-plane"
  network   = google_compute_network.gateway.id
  direction = "INGRESS"

  source_ranges = [var.master_cidr]
  target_tags   = [var.node_tag]

  allow {
    protocol = "tcp"
    ports    = ["8443", "9443", "15017"]
  }
}

# Callers reach the gateway from inside the VPC and from whatever is peered to
# it. This is the rule that decides who can talk to the front door, so it
# takes an explicit list rather than the subnet it happens to sit in.
#
# The front door is the guardrail port. The proxy's port is not opened to
# callers at all, because a caller that reaches the proxy directly has gone
# round the detector.
resource "google_compute_firewall" "callers_to_gateway" {
  name      = "${var.name_prefix}-allow-callers"
  network   = google_compute_network.gateway.id
  direction = "INGRESS"

  source_ranges = var.caller_cidrs
  target_tags   = [var.node_tag]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.guardrail_port)]
  }
}
