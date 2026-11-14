# Runbook: ImagePullBackOff

## Symptoms

A pod is stuck in ImagePullBackOff or ErrImagePull and never starts. Events
show "Failed to pull image" with either "not found" or "unauthorized".

## Diagnosis

Read the pod events. "manifest unknown" or "not found" means the tag does not
exist in the registry, usually a typo or a CI build that never pushed.
"unauthorized" or "denied" means the registry credentials are missing or
expired.

## Fix

For a missing tag, check the CI run that should have published the image and
correct the tag in the Kustomize overlay. For an auth failure, recreate the
registry pull secret and confirm the pod's service account references it under
imagePullSecrets. On kind clusters, load local images with `kind load
docker-image` and set imagePullPolicy to Never.
