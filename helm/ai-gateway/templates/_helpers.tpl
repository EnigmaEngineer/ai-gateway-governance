{{- define "ai-gateway.name" -}}
{{- default .Chart.Name .Values.nameOverride -}}
{{- end -}}

{{- define "ai-gateway.labels" -}}
app.kubernetes.io/name: {{ include "ai-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "ai-gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Refuse to render rather than render something that works and is wrong. Every
one of these has a failure that is invisible once the thing is up. An empty
gcpServiceAccount silently falls back to the node identity. An empty guardrail
image renders a Deployment that pulls a nonexistent tag. An enabled network
policy with no caller ranges admits the whole cluster.

The last two were added after the render sweep. The first three conditions
caught every empty value and caught nothing about the one value that decides
whether the gateway has a public address, because that value has a plausible
default rather than an empty one. Exposing the service and switching off the
policy are both allowed and both now take a second value saying so.
*/}}
{{- define "ai-gateway.require" -}}
{{- if not .Values.serviceAccount.gcpServiceAccount -}}
{{- fail "serviceAccount.gcpServiceAccount is empty. Set it from the terraform output workload_service_account, or the pods authenticate as the node service account." -}}
{{- end -}}
{{- if not .Values.guardrail.image -}}
{{- fail "guardrail.image is empty. No image has been built or pushed for the guardrail yet, because there is no registry to push it to." -}}
{{- end -}}
{{- if and .Values.networkPolicy.enabled (not .Values.networkPolicy.callerCidrs) -}}
{{- fail "networkPolicy.enabled is true and networkPolicy.callerCidrs is empty, which admits every pod in the cluster." -}}
{{- end -}}
{{- if and (not .Values.service.internal) (not .Values.service.acknowledgeInternetExposure) -}}
{{- fail "service.internal is false, which gives this gateway a public address. Set service.acknowledgeInternetExposure to true if that is the intent." -}}
{{- end -}}
{{- if and (not .Values.networkPolicy.enabled) (not .Values.networkPolicy.acknowledgeClusterWideAccess) -}}
{{- fail "networkPolicy.enabled is false, so every pod in the cluster can reach the gateway. Set networkPolicy.acknowledgeClusterWideAccess to true if that is the intent." -}}
{{- end -}}
{{- if and (not .Values.upstreamTls.enabled) (not .Values.upstreamTls.acknowledgePlaintextBackendHop) -}}
{{- fail "upstreamTls.enabled is false, so the guardrail reaches the model backend with no client identity and the backend cannot tell it from anything else in the cluster. Set upstreamTls.acknowledgePlaintextBackendHop to true if that is the intent." -}}
{{- end -}}
{{- if and .Values.upstreamTls.enabled (not .Values.upstreamTls.secretName) -}}
{{- fail "upstreamTls.enabled is true and upstreamTls.secretName is empty, so the pod would mount nothing and the guardrail would be handed a certificate path with no file behind it." -}}
{{- end -}}
{{- if and .Values.upstreamTls.enabled (or (not .Values.upstreamTls.caKey) (or (not .Values.upstreamTls.certKey) (not .Values.upstreamTls.keyKey))) -}}
{{- fail "upstreamTls needs caKey and certKey and keyKey together. gateway/app.py refuses --upstream-cert without --upstream-ca, so a chart that renders two of the three renders a pod that will not start." -}}
{{- end -}}
{{- end -}}
