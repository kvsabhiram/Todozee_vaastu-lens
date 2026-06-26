###############################################################################
# Alerting — detect application errors in the logs and host failures, and
# notify via SNS.
#
#   log group  ──metric filter──▶  VaastuLens/App ErrorCount  ──alarm──▶ SNS
#   EC2 StatusCheckFailed         ─────────────────────────────alarm──▶ SNS
###############################################################################

# Where alarm notifications go. Subscribe an email (optional) + any future
# integrations (Slack/PagerDuty via a Lambda/chatbot) to this topic.
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

# Optional email subscription. AWS sends a confirmation email that must be
# clicked before notifications start flowing.
resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# Count error/exception lines in the container logs. default_value=0 means the
# metric reports 0 (not "missing") on quiet periods, so the alarm evaluates
# reliably. The pattern is high-signal: log-level ERROR/CRITICAL, Python
# tracebacks, and uvicorn's unhandled-exception marker.
resource "aws_cloudwatch_log_metric_filter" "app_errors" {
  name           = "${local.name}-errors"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "?\"ERROR\" ?\"CRITICAL\" ?\"Traceback\" ?\"Exception in ASGI\""

  metric_transformation {
    name          = "ErrorCount"
    namespace     = "VaastuLens/App"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

# Alarm: one or more error lines within a 5-minute window.
resource "aws_cloudwatch_metric_alarm" "app_errors" {
  alarm_name          = "${local.name}-app-errors"
  alarm_description   = "Application error/exception lines detected in the CloudWatch logs."
  namespace           = "VaastuLens/App"
  metric_name         = "ErrorCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.error_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# Count HTTP 5xx responses in the uvicorn access log. The access line looks
# like:  ... "GET /x HTTP/1.1" 500 Internal Server Error  — so a quoted term of
# " 5xx" (space + status code) matches the status field without touching paths,
# ports or query strings. Kept as its own metric so 5xx is distinguishable from
# unhandled exceptions.
resource "aws_cloudwatch_log_metric_filter" "http_5xx" {
  name           = "${local.name}-5xx"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "?\" 500\" ?\" 501\" ?\" 502\" ?\" 503\" ?\" 504\""

  metric_transformation {
    name          = "HttpServerErrorCount"
    namespace     = "VaastuLens/App"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

# Alarm: one or more HTTP 5xx responses within a 5-minute window.
resource "aws_cloudwatch_metric_alarm" "http_5xx" {
  alarm_name          = "${local.name}-5xx-errors"
  alarm_description   = "HTTP 5xx responses detected in the access logs."
  namespace           = "VaastuLens/App"
  metric_name         = "HttpServerErrorCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.error_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# Alarm: EC2 instance/system status check failing for 3 consecutive minutes —
# i.e. the host (and thus the app) is effectively down. treat_missing_data is
# "breaching" so a vanished instance also fires.
resource "aws_cloudwatch_metric_alarm" "instance_health" {
  alarm_name          = "${local.name}-instance-status-check"
  alarm_description   = "EC2 instance/system status check failing (host or app down)."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
