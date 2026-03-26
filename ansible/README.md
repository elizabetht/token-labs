# Ansible Bootstrap for DGX Spark Cluster

This directory contains Ansible playbooks and roles for automating the complete bootstrap of DGX Spark nodes for Kubernetes cluster setup.

## Overview

The Ansible automation handles:
- OS-level prerequisites (kernel modules, swap, sysctl)
- Container runtime installation (containerd)
- Kubernetes components (kubeadm, kubelet, kubectl)
- NVIDIA Container Toolkit installation and configuration
- Kubernetes cluster initialization
- Worker node joining
- CNI installation (Flannel or Cilium)

## Directory Structure

```
ansible/
├── inventory/
│   └── hosts.yml           # Inventory with DGX Spark nodes
├── roles/
│   ├── bootstrap/          # OS and Kubernetes base setup
│   ├── nvidia/             # NVIDIA Container Toolkit
│   ├── kubeadm-init/       # Control plane initialization
│   └── kubeadm-join/       # Worker node joining
└── site.yml                # Main playbook
```

## Prerequisites

### On the Ansible Controller

1. Install Ansible:
   ```bash
   pip install ansible
   ```

2. Ensure SSH access to all nodes:
   ```bash
   ssh-copy-id ubuntu@spark-01
   ssh-copy-id ubuntu@spark-02
   ssh-copy-id ubuntu@spark-03
   ```

### On Target Nodes

- Ubuntu 22.04 LTS
- SSH access with sudo privileges
- NVIDIA GPUs installed (for GPU nodes)
- NVIDIA drivers installed (for GPU nodes)

## Configuration

Edit `inventory/hosts.yml` to match your environment:

```yaml
control_plane:
  hosts:
    spark-01:
      ansible_host: 192.168.1.101  # Update with actual IP
      ansible_user: ubuntu

workers:
  hosts:
    spark-02:
      ansible_host: 192.168.1.102  # Update with actual IP
    spark-03:
      ansible_host: 192.168.1.103  # Update with actual IP
```

### Variables

Key variables in `inventory/hosts.yml`:

- `k8s_version`: Kubernetes version (e.g., "1.31")
- `k8s_package_version`: Specific package version (e.g., "1.31.0-1.1")
- `containerd_version`: Containerd version (e.g., "1.7.22")
- `nvidia_container_toolkit_version`: NVIDIA toolkit version (e.g., "1.16.2")
- `k8s_pod_network_cidr`: Pod network CIDR (default: "10.244.0.0/16")
- `k8s_service_cidr`: Service CIDR (default: "10.96.0.0/12")
- `k8s_cni`: CNI to install ("flannel" or "cilium")
- `kubelet_max_pods`: Maximum pods per node (default: 110)

## Usage

### Full Cluster Setup

Run the complete playbook to bootstrap all nodes and initialize the cluster:

```bash
cd ansible
ansible-playbook -i inventory/hosts.yml site.yml
```

### Dry Run

Test the playbook without making changes:

```bash
ansible-playbook -i inventory/hosts.yml site.yml --check --diff
```

### Run Specific Roles

Bootstrap only (without cluster init):

```bash
ansible-playbook -i inventory/hosts.yml site.yml --tags bootstrap
```

### Run on Specific Hosts

Run on control plane only:

```bash
ansible-playbook -i inventory/hosts.yml site.yml --limit control_plane
```

## Playbook Steps

The `site.yml` playbook executes in this order:

1. **Bootstrap all nodes** (`bootstrap` role)
   - Set hostname
   - Disable swap
   - Configure kernel modules (overlay, br_netfilter)
   - Install containerd
   - Install kubeadm, kubelet, kubectl
   - Configure kubelet extra args

2. **Install NVIDIA support** (`nvidia` role)
   - Install NVIDIA Container Toolkit
   - Configure containerd to use nvidia runtime
   - Validate GPU access

3. **Initialize control plane** (`kubeadm-init` role, control_plane only)
   - Run `kubeadm init`
   - Install CNI (Flannel or Cilium)
   - Generate join command for workers

4. **Join worker nodes** (`kubeadm-join` role, workers only)
   - Join each worker to the cluster

5. **Verify cluster**
   - Wait for all nodes to be Ready
   - Display cluster status

## Post-Bootstrap

After successful bootstrap, the kubeconfig is available at:
- `/etc/kubernetes/admin.conf` (root)
- `/home/ubuntu/.kube/config` (ansible user)

Verify the cluster:

```bash
# On control plane node
kubectl get nodes -o wide
kubectl get pods -A
```

## Idempotency

All roles are designed to be idempotent. Re-running the playbook will:
- Skip tasks that are already completed
- Only make changes when configuration drifts
- Be safe to run multiple times

## Troubleshooting

### Check connectivity
```bash
ansible all -i inventory/hosts.yml -m ping
```

### Gather facts
```bash
ansible all -i inventory/hosts.yml -m setup
```

### Verbose output
```bash
ansible-playbook -i inventory/hosts.yml site.yml -vvv
```

### Reset a node (if needed)
```bash
# On the node
sudo kubeadm reset -f
sudo rm -rf /etc/cni/net.d
sudo rm -rf /var/lib/kubelet/*
sudo systemctl restart containerd kubelet
```

## Next Steps

After cluster bootstrap:
1. Install Flux for GitOps (see `../deploy/flux-system/`)
2. Deploy operators via Flux HelmReleases (see `../deploy/infrastructure/`)
3. Deploy applications and workloads

## References

- [Kubernetes kubeadm documentation](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)
- [Ansible documentation](https://docs.ansible.com/)
