output "cluster_id" {
  value       = oci_containerengine_cluster.this.id
  description = "OKE cluster OCID."
}

output "kubeconfig_command" {
  value       = "oci ce cluster create-kubeconfig --cluster-id ${oci_containerengine_cluster.this.id} --region ${var.region} --file $HOME/.kube/config --token-version 2.0.0 --kube-endpoint PUBLIC_ENDPOINT"
  description = "Run this to fetch a kubeconfig for the new cluster."
}
