# Apigee X, the managed gateway in front of everything else in this directory.
# The proxy bundle it serves is in apiproxy/ and it is graded by
# scripts/policy_check.py, because terraform cannot see inside a bundle and
# neither can anything else here.
#
# TODO: nothing in this file has been applied. There is no GCP project and no
# billing account, so there is no organisation for an instance to attach to. An Apigee organisation is one per project and takes the better
# part of an hour to create, so this is not a file to find a mistake in by
# applying it.
#
# Three of the values below came from the provider schema rather than from
# memory, after a first version of this file set deployment_type and wrote a
# comment about the environment type, which is a different field. The schema
# is readable with no credentials:
#
#   terraform providers schema -json | jq '.provider_schemas
#     | .["registry.terraform.io/hashicorp/google"].resource_schemas
#     | .google_apigee_environment.block.attributes'

# Apigee peers its own network into the VPC and takes a /22 from a range the
# customer has reserved for service networking. Without this allocation the
# organisation creates and the instance fails, which is a failure terraform
# validate cannot see because nothing here is syntactically wrong. The first
# version of this file had the instance and not the allocation.
resource "google_compute_global_address" "apigee_peering" {
  name          = "${var.name_prefix}-apigee-peering"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  network       = google_compute_network.gateway.id
  address       = cidrhost(var.apigee_peering_cidr, 0)
  prefix_length = tonumber(split("/", var.apigee_peering_cidr)[1])
}

resource "google_service_networking_connection" "apigee" {
  network                 = google_compute_network.gateway.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.apigee_peering.name]
}

resource "google_apigee_organization" "gateway" {
  analytics_region   = var.region
  project_id         = var.project_id
  authorized_network = google_compute_network.gateway.id

  # CLOUD rather than HYBRID, from the schema's own value list. HYBRID is the
  # runtime you operate, which would put the message processors on the GKE
  # cluster next door. That is a tempting shape and it is the wrong one here,
  # because the whole question on this layer is what a product somebody else
  # runs does by default.
  runtime_type = "CLOUD"

  # NOT schema verified. The provider gives no value list for billing_type,
  # only a link to the pricing page, so EVALUATION is the one value in this
  # file that an apply could reject. It is written this way on purpose: an
  # evaluation organisation cannot be converted to paid, and a paid one is a
  # commitment the teardown has to destroy.
  billing_type = "EVALUATION"

  # The peering has to exist before the organisation asks for a range inside
  # it. Terraform infers the network dependency and not this one.
  depends_on = [google_service_networking_connection.apigee]
}

# The runtime. This is the resource that costs money and the one the teardown
# has to destroy.
resource "google_apigee_instance" "gateway" {
  name     = "${var.name_prefix}-instance"
  location = var.region
  org_id   = google_apigee_organization.gateway.id

  # The schema says a customer provided block of length 22, and that leaving
  # it out makes Apigee request any available /22 from service networking.
  # Named rather than automatic, so the firewall rules can be written against
  # a range somebody chose.
  ip_range = var.apigee_peering_cidr
}

resource "google_apigee_environment" "prod" {
  org_id       = google_apigee_organization.gateway.id
  name         = "prod"
  display_name = "prod"

  # Three separate fields, which the first version of this file confused.
  #
  # type is the capability and capacity tier, BASE through COMPREHENSIVE.
  # deployment_type is PROXY or ARCHIVE, and ARCHIVE blocks deploying proxy
  # revisions at all. api_proxy_type is PROGRAMMABLE or CONFIGURABLE, and
  # CONFIGURABLE is the one that does not take arbitrary policy XML.
  #
  # All three are optional, so all three have an API side default this file
  # would otherwise be silent about. That is the same hazard as the Quota
  # element nobody writes. Each one is named.
  #
  # BASE because the organisation is torn down at the end of the week. Which
  # policies each tier carries is NOT verified here. The bundle uses VerifyJWT
  # and Quota and SpikeArrest and ResponseCache and OAuthV2 and ServiceCallout,
  # and if BASE refuses one of those it refuses it at deploy time rather than
  # here.
  type            = "BASE"
  deployment_type = "PROXY"
  api_proxy_type  = "PROGRAMMABLE"
}

# The hostname a caller resolves. An environment with no group attached is
# deployed and unreachable, and it looks like a networking fault.
resource "google_apigee_envgroup" "public" {
  org_id = google_apigee_organization.gateway.id
  name   = "${var.name_prefix}-group"

  # TODO: not a real hostname. A deployment fails here rather than answering
  # on something unintended.
  hostnames = var.apigee_hostnames
}

resource "google_apigee_envgroup_attachment" "prod" {
  envgroup_id = google_apigee_envgroup.public.id
  environment = google_apigee_environment.prod.name
}

resource "google_apigee_instance_attachment" "prod" {
  instance_id = google_apigee_instance.gateway.id
  environment = google_apigee_environment.prod.name
}
