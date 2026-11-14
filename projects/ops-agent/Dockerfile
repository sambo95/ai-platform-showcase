FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# kubectl for the live-cluster backend. Pinned; bump alongside the cluster version.
ADD --chmod=755 https://dl.k8s.io/release/v1.31.2/bin/linux/amd64/kubectl /usr/local/bin/kubectl

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY opsagent ./opsagent

RUN useradd --uid 10001 --create-home agent
USER 10001
ENTRYPOINT ["python", "-m", "opsagent"]
