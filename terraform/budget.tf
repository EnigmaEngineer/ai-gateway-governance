# The budget goes in before the cluster does. A run that creates billable
# resources and then adds the alert has already had the window where nothing
# was watching, and that window is where a forgotten load balancer lives.

resource "google_monitoring_notification_channel" "budget_email" {
  for_each = toset(var.alert_emails)

  display_name = "gateway budget ${each.value}"
  type         = "email"

  labels = {
    email_address = each.value
  }
}

resource "google_billing_budget" "gateway" {
  billing_account = var.billing_account
  display_name    = "ai gateway governance"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  # Three thresholds rather than one. The first is a nudge, the second is a
  # look at what is running, and the third fires on forecast so it can arrive
  # before the money is spent instead of after.
  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.9
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [
      for c in google_monitoring_notification_channel.budget_email : c.id
    ]
    disable_default_iam_recipients = false
  }
}
