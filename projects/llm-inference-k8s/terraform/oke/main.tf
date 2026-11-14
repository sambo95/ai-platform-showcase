# OKE cluster with a CPU system pool and a GPU pool for model serving.
# Deliberately small: one VCN, a public API endpoint, private workers behind NAT.

terraform {
  required_version = ">= 1.6"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0"
    }
  }
}

provider "oci" {
  region = var.region
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_services" "all" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

locals {
  ad = data.oci_identity_availability_domains.ads.availability_domains[0].name
}

# ---------------------------------------------------------------- network ---

resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "llm"
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-igw"
}

resource "oci_core_nat_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-nat"
}

resource "oci_core_service_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-sgw"
  services {
    service_id = data.oci_core_services.all.services[0].id
  }
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-public"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.this.id
  }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-private"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.this.id
  }
  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.this.id
  }
}

# One permissive-inside-the-VCN list keeps the demo readable. For production,
# split this into per-subnet NSGs following the OKE network resource guide.
resource "oci_core_security_list" "cluster" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name}-cluster"

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
  ingress_security_rules {
    protocol = "all"
    source   = "10.0.0.0/16"
  }
  ingress_security_rules {
    description = "Kubernetes API"
    protocol    = "6"
    source      = var.api_allowed_cidr
    tcp_options {
      min = 6443
      max = 6443
    }
  }
  ingress_security_rules {
    description = "Load balancer HTTPS"
    protocol    = "6"
    source      = "0.0.0.0/0"
    tcp_options {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_subnet" "api" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.this.id
  display_name      = "${var.name}-api"
  cidr_block        = "10.0.0.0/28"
  dns_label         = "api"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.cluster.id]
}

resource "oci_core_subnet" "lb" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.this.id
  display_name      = "${var.name}-lb"
  cidr_block        = "10.0.1.0/24"
  dns_label         = "lb"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.cluster.id]
}

resource "oci_core_subnet" "workers" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  display_name               = "${var.name}-workers"
  cidr_block                 = "10.0.16.0/20"
  dns_label                  = "workers"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.cluster.id]
}

# ---------------------------------------------------------------- cluster ---

resource "oci_containerengine_cluster" "this" {
  compartment_id     = var.compartment_ocid
  name               = var.name
  kubernetes_version = var.kubernetes_version
  vcn_id             = oci_core_vcn.this.id
  type               = "ENHANCED_CLUSTER"

  endpoint_config {
    is_public_ip_enabled = true
    subnet_id            = oci_core_subnet.api.id
  }

  cluster_pod_network_options {
    cni_type = "FLANNEL_OVERLAY"
  }

  options {
    service_lb_subnet_ids = [oci_core_subnet.lb.id]
  }
}

resource "oci_containerengine_node_pool" "system" {
  cluster_id         = oci_containerengine_cluster.this.id
  compartment_id     = var.compartment_ocid
  name               = "system"
  kubernetes_version = var.kubernetes_version
  node_shape         = "VM.Standard.E5.Flex"

  node_shape_config {
    ocpus         = 2
    memory_in_gbs = 16
  }

  node_config_details {
    size = 2
    placement_configs {
      availability_domain = local.ad
      subnet_id           = oci_core_subnet.workers.id
    }
  }

  node_source_details {
    source_type = "IMAGE"
    image_id    = var.cpu_node_image_ocid
  }
}

resource "oci_containerengine_node_pool" "gpu" {
  cluster_id         = oci_containerengine_cluster.this.id
  compartment_id     = var.compartment_ocid
  name               = "gpu"
  kubernetes_version = var.kubernetes_version
  node_shape         = var.gpu_shape

  node_config_details {
    size = var.gpu_node_count
    placement_configs {
      availability_domain = local.ad
      subnet_id           = oci_core_subnet.workers.id
    }
  }

  node_source_details {
    source_type             = "IMAGE"
    image_id                = var.gpu_node_image_ocid
    boot_volume_size_in_gbs = 200
  }

  # Label for node selection. GPU nodes carry an nvidia.com/gpu taint, which the
  # vLLM deployment tolerates, so only GPU workloads land here.
  initial_node_labels {
    key   = "workload"
    value = "gpu-inference"
  }
}
