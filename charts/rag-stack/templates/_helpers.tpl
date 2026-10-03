{{- define "rag-stack.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/part-of: rag-stack
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "rag-stack.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/name: {{ .name }}
{{- end }}
