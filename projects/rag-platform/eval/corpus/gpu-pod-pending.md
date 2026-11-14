# Runbook: GPU pod stuck in Pending

## Symptoms

A model server pod stays Pending. The scheduler event reads "0/N nodes are
available: Insufficient nvidia.com/gpu" or mentions an untolerated taint.

## Diagnosis

Check allocatable GPUs with `kubectl describe node` and look for the
nvidia.com/gpu resource. If the resource is absent, the NVIDIA device plugin
is not running on that node. If it is present but fully allocated, every GPU
is already claimed by another pod. If the event mentions a taint, the pod is
missing the nvidia.com/gpu toleration.

## Fix

Install or restart the NVIDIA device plugin (or the GPU Operator) when the
resource is missing. Add a node to the GPU node pool when capacity is
exhausted; a new GPU node typically takes several minutes to join. Add the
toleration to the pod spec when the taint is the cause.
