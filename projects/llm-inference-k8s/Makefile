CLUSTER := llm
IMAGE   := llm-gateway:dev

.PHONY: test up down smoke loadtest observability

test:            ## Unit tests for the gateway
	cd gateway && python -m pytest -q

up:              ## Create a kind cluster and deploy gateway + CPU model server
	kind create cluster --config deploy/kind/cluster.yaml
	docker build -t $(IMAGE) gateway
	kind load docker-image $(IMAGE) --name $(CLUSTER)
	kubectl apply -k deploy/overlays/local
	kubectl -n llm rollout status deploy/model-server --timeout=10m
	kubectl -n llm rollout status deploy/llm-gateway --timeout=3m

smoke:           ## One chat completion through the gateway
	curl -s http://localhost:8000/v1/chat/completions \
	  -H 'Content-Type: application/json' \
	  -d '{"model":"default","messages":[{"role":"user","content":"Say hello in five words."}]}'

loadtest:        ## Measure TTFT and tokens/s
	python loadtest/loadtest.py --url http://localhost:8000 --concurrency 4 --requests 20

observability:   ## Install kube-prometheus-stack, then the ServiceMonitor, alerts and dashboard
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
	  --namespace monitoring --create-namespace --wait
	kubectl apply -k deploy/observability

down:            ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)
