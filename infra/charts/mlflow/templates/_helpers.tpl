{{- define "mlflow.name" -}}{{ .Chart.Name }}{{- end -}}
{{- define "mlflow.fullname" -}}{{ .Release.Name }}{{- end -}}
{{- define "mlflow.labels" -}}
app.kubernetes.io/name: {{ include "mlflow.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "mlflow.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mlflow.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
