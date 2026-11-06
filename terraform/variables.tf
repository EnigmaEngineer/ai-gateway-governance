variable "project_id" {
  description = "Project the gateway and its budget alert live in."
  type        = string
}

variable "region" {
  description = "Region for the cluster and the internal load balancer."
  type        = string
  default     = "us-central1"
}

variable "billing_account" {
  description = "Billing account id the budget is attached to."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Hard number rather than a percentage of last month, so a quiet month cannot raise the ceiling for the next one."
  type        = number
  default     = 40
}

variable "alert_emails" {
  description = "Addresses that receive the threshold notifications."
  type        = list(string)
}

variable "name_prefix" {
  description = "Prefix on every resource name, so two deployments can share a project."
  type        = string
  default     = "ai-gateway"
}

variable "node_cidr" {
  description = "Primary range for the node subnet."
  type        = string
  default     = "10.20.0.0/24"
}

variable "pod_cidr" {
  description = "Secondary range for pods. A /20 is 4096 addresses and the pool is three nodes."
  type        = string
  default     = "10.21.0.0/20"
}

variable "service_cidr" {
  description = "Secondary range for cluster services."
  type        = string
  default     = "10.22.0.0/24"
}

variable "master_cidr" {
  description = "Range for the control plane. Must not overlap any other range here."
  type        = string
  default     = "172.16.0.0/28"
}

variable "caller_cidrs" {
  description = "Ranges allowed to reach the gateway port. No default, because a default here is a guess about somebody's network."
  type        = list(string)
}

variable "control_plane_cidrs" {
  description = "Networks allowed to reach the Kubernetes API, each with a name."
  type = list(object({
    cidr = string
    name = string
  }))
}

variable "private_endpoint" {
  description = "Whether the Kubernetes API is reachable only from inside the VPC. False needs the authorised network list to be the real control."
  type        = bool
  default     = false
}

variable "node_tag" {
  description = "Network tag the firewall rules target."
  type        = string
  default     = "ai-gateway-node"
}

variable "release_channel" {
  description = "GKE release channel. REGULAR rather than RAPID, because an unattended cluster should not be taking a minor version on its own schedule."
  type        = string
  default     = "REGULAR"
}

variable "machine_type" {
  description = "Node machine type."
  type        = string
  default     = "e2-standard-2"
}

variable "nodes_per_zone" {
  description = "Nodes in each zone of the region. Three zones, so one here is three nodes."
  type        = number
  default     = 1
}

variable "namespace" {
  description = "Kubernetes namespace the gateway runs in."
  type        = string
  default     = "ai-gateway"
}

variable "kubernetes_service_account" {
  description = "Name of the Kubernetes service account bound to the workload identity."
  type        = string
  default     = "ai-gateway"
}

variable "guardrail_port" {
  description = "Port the guardrail listens on. This is the front door and the only one callers reach."
  type        = number
  default     = 8080
}

variable "apigee_peering_cidr" {
  description = "Range Apigee peers into the VPC for its runtime. Must not overlap the node, pod, service or master ranges."
  type        = string
  default     = "10.30.0.0/22"

  # The instance schema says the block is length 22. This block refuses
  # anything else, and `terraform validate` DOES NOT RUN IT. Measured: a
  # variable carrying an always false validation passes validate with
  # Success and only plan reports it. So on a day with no credentials, when
  # validate is the only terraform command available, this guard grades
  # nothing. tests/test_apigee.py asserts the /22 instead, because the suite
  # runs on a clone with no cloud at all.
  validation {
    condition     = can(cidrhost(var.apigee_peering_cidr, 0)) && tonumber(split("/", var.apigee_peering_cidr)[1]) == 22
    error_message = "Apigee takes a block of length 22. Give a.b.c.d/22."
  }
}

variable "apigee_hostnames" {
  description = "Hostnames the environment group answers on. No default, because a default here is a guess about somebody's domain and an environment group with the wrong hostname is deployed and unreachable."
  type        = list(string)
}
