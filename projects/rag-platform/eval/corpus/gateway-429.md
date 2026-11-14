# Runbook: Gateway returning HTTP 429

## Symptoms

Clients of the LLM gateway receive HTTP 429 responses. The response detail is
either "rate limit exceeded" or "gateway at capacity".

## Diagnosis

The two messages have different causes. "rate limit exceeded" means one tenant
exhausted its per-minute token bucket; check `gateway_rejected_total` with
reason rate_limited. "gateway at capacity" means the gateway is shedding load
because in-flight requests reached GATEWAY_MAX_INFLIGHT; check the reason
overloaded and the `gateway_inflight_requests` gauge.

## Fix

For a single noisy tenant, raise GATEWAY_RATE_LIMIT_RPM only if the backend
has headroom. For load shedding, add model server replicas or raise the KEDA
maxReplicaCount. Do not simply raise GATEWAY_MAX_INFLIGHT: that converts
rejections into long queues and worse time to first token for everyone.
