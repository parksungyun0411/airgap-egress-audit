#!/usr/bin/env bash
# 측정이 끝난 날 클러스터와 과금 자원을 지운다.
# 기본은 클러스터만 지우고, --all을 주면 ECR 저장소와 GitHub 배포 역할도 지운다.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
CLUSTER="${EKS_CLUSTER_NAME:-airgap-audit}"
ECR_REPOS="${ECR_REPOSITORIES:-airgap-llm airgap-embed airgap-rag-app}"
ROLE_NAME="${ROLE_NAME:-airgap-egress-audit-github-deploy}"
NAMESPACE="${NAMESPACE:-rag}"

if aws eks describe-cluster --region "$REGION" --name "$CLUSTER" >/dev/null 2>&1; then
  # LoadBalancer 서비스가 남아 있으면 ELB가 클러스터 삭제 뒤에도 남는다. 먼저 정리한다.
  helm uninstall rag -n "$NAMESPACE" 2>/dev/null || true
  kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace} {.metadata.name}{"\n"}{end}' \
    | while read -r ns name; do
        if [[ -n "$name" ]]; then kubectl -n "$ns" delete svc "$name" || true; fi
      done
  eksctl delete cluster --region "$REGION" --name "$CLUSTER" --wait
else
  echo "클러스터 ${CLUSTER}가 없다. 건너뛴다."
fi

if [[ "${1:-}" == "--all" ]]; then
  for repo in $ECR_REPOS; do
    aws ecr delete-repository --region "$REGION" --repository-name "$repo" --force >/dev/null 2>&1 || true
  done
  aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name deploy 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE_NAME" 2>/dev/null || true
fi

echo "남은 자원 확인(비어 있어야 한다):"
aws eks list-clusters --region "$REGION" --output text
aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:project,Values=airgap-egress-audit" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text
aws ec2 describe-volumes --region "$REGION" --filters "Name=status,Values=available" \
  --query 'Volumes[].VolumeId' --output text
aws cloudformation list-stacks --region "$REGION" \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED \
  --query "StackSummaries[?starts_with(StackName, 'eksctl-${CLUSTER}')].StackName" --output text
