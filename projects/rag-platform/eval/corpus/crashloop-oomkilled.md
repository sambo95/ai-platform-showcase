# Runbook: CrashLoopBackOff caused by OOMKilled

## Symptoms

A pod restarts repeatedly and `kubectl get pods` shows CrashLoopBackOff. The
last state in `kubectl describe pod` reports reason OOMKilled with exit code 137.

## Diagnosis

Exit code 137 means the kernel killed the container for exceeding its memory
limit. Compare the container memory limit with actual usage by querying
`container_memory_working_set_bytes` in Prometheus. For model servers, memory
grows with context size and the number of parallel slots.

## Fix

Raise the memory limit in the Deployment, or reduce memory demand: lower
`--ctx-size`, lower `--parallel`, or use a smaller quantization. Keep the
memory request equal to the limit for inference pods so the scheduler places
them on nodes that can actually hold them.
