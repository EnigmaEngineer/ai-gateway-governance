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
