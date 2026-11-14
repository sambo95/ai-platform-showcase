# Runbook: High time to first token

## Symptoms

The LLMTimeToFirstTokenHigh alert fires: p95 time to first token is above two
seconds for ten minutes. Users see a long pause before streaming begins.

## Diagnosis

Time to first token is dominated by queueing and prompt processing. Check
whether in-flight requests per replica exceed the autoscaling threshold, and
whether the autoscaler is pinned at maxReplicaCount. Very long prompts also
increase prefill time, so compare prompt token rates before and after the
regression.

## Fix

Scale out the model server when queueing is the cause. If replicas are at the
maximum, raise the ceiling or add GPU nodes. When long prompts are the cause,
enable prefix caching on the model server or trim retrieved context in the
RAG pipeline by lowering top_k.
