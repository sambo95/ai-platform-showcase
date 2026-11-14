# Runbook: Qdrant volume running out of disk

## Symptoms

Ingestion requests to the RAG service fail with HTTP 500 and Qdrant logs
report "No space left on device". Search may keep working from memory.

## Diagnosis

Check volume usage with `kubectl exec` and `df -h /qdrant/storage`. Growth
usually comes from re-ingesting documents under new source names, which
leaves the old chunks in place, or from write-ahead log segments that have
not been compacted.

## Fix

Expand the PersistentVolumeClaim if the storage class allows volume
expansion. Delete collections that are no longer used. Re-ingest with stable
source names so the service replaces old chunks instead of accumulating
duplicates.
