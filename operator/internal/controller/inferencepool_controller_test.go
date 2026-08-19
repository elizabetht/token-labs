package controller

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	inferencev1alpha1 "github.com/elizabetht/token-labs/operator/api/v1alpha1"
)

// newScheme returns a scheme with all required types registered.
func newScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	s := runtime.NewScheme()
	if err := clientgoscheme.AddToScheme(s); err != nil {
		t.Fatalf("add client-go scheme: %v", err)
	}
	if err := inferencev1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("add inference scheme: %v", err)
	}
	return s
}

// newPool creates a minimal InferencePool for use in tests.
func newPool(name, ns string, replicas int32) *inferencev1alpha1.InferencePool {
	return &inferencev1alpha1.InferencePool{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "inference.tokenlabs.run/v1alpha1",
			Kind:       "InferencePool",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
		},
		Spec: inferencev1alpha1.InferencePoolSpec{
			Model: inferencev1alpha1.ModelSpec{
				Repository:   "meta-llama/Meta-Llama-3-70B",
				Quantization: "fp8",
			},
			Resources: inferencev1alpha1.ResourceSpec{
				GPUType:  "nvidia-h100",
				Replicas: replicas,
			},
			Gateway: &inferencev1alpha1.GatewaySpec{
				Host: "api.tokenlabs.run",
				Path: "/v1/completions",
			},
		},
	}
}

// reconcile is a helper that calls the controller's Reconcile method and fails
// the test on error.
func reconcile(t *testing.T, r *InferencePoolReconciler, name, ns string) ctrl.Result {
	t.Helper()
	res, err := r.Reconcile(context.Background(), ctrl.Request{
		NamespacedName: types.NamespacedName{Name: name, Namespace: ns},
	})
	if err != nil {
		t.Fatalf("Reconcile returned error: %v", err)
	}
	return res
}

// TestReconcile_CreateChildResources verifies that reconciling an InferencePool
// creates the expected Deployment and Service child resources.
func TestReconcile_CreateChildResources(t *testing.T) {
	const (
		poolName = "test-pool"
		ns       = "default"
	)
	pool := newPool(poolName, ns, 2)
	scheme := newScheme(t)

	r := &InferencePoolReconciler{
		Client: fake.NewClientBuilder().
			WithScheme(scheme).
			WithObjects(pool).
			WithStatusSubresource(pool).
			Build(),
		Scheme: scheme,
	}

	// First reconcile: adds finalizer.
	reconcile(t, r, poolName, ns)
	// Second reconcile: creates child resources.
	reconcile(t, r, poolName, ns)

	// Deployment must exist.
	deploy := &appsv1.Deployment{}
	if err := r.Client.Get(context.Background(), types.NamespacedName{
		Name:      deploymentName(poolName),
		Namespace: ns,
	}, deploy); err != nil {
		t.Fatalf("expected Deployment to exist, got error: %v", err)
	}

	// Service must exist.
	svc := &corev1.Service{}
	if err := r.Client.Get(context.Background(), types.NamespacedName{
		Name:      deploymentName(poolName),
		Namespace: ns,
	}, svc); err != nil {
		t.Fatalf("expected Service to exist, got error: %v", err)
	}

	// Deployment replica count must match the spec.
	if deploy.Spec.Replicas == nil || *deploy.Spec.Replicas != 2 {
		t.Errorf("expected 2 replicas, got %v", deploy.Spec.Replicas)
	}
}

// TestReconcile_ReplicaSync verifies that updating spec.replicas is reflected in
// the child Deployment's replica count after reconciliation.
func TestReconcile_ReplicaSync(t *testing.T) {
	const (
		poolName = "replica-sync-pool"
		ns       = "default"
	)
	pool := newPool(poolName, ns, 1)
	scheme := newScheme(t)

	r := &InferencePoolReconciler{
		Client: fake.NewClientBuilder().
			WithScheme(scheme).
			WithObjects(pool).
			WithStatusSubresource(pool).
			Build(),
		Scheme: scheme,
	}

	// Bootstrap: add finalizer then create children.
	reconcile(t, r, poolName, ns)
	reconcile(t, r, poolName, ns)

	// Update the pool's replica count.
	updated := &inferencev1alpha1.InferencePool{}
	if err := r.Client.Get(context.Background(), types.NamespacedName{Name: poolName, Namespace: ns}, updated); err != nil {
		t.Fatalf("get pool: %v", err)
	}
	updated.Spec.Resources.Replicas = 3
	if err := r.Client.Update(context.Background(), updated); err != nil {
		t.Fatalf("update pool: %v", err)
	}

	// Reconcile should sync replicas.
	reconcile(t, r, poolName, ns)

	deploy := &appsv1.Deployment{}
	if err := r.Client.Get(context.Background(), types.NamespacedName{
		Name:      deploymentName(poolName),
		Namespace: ns,
	}, deploy); err != nil {
		t.Fatalf("get deployment: %v", err)
	}
	if deploy.Spec.Replicas == nil || *deploy.Spec.Replicas != 3 {
		t.Errorf("expected 3 replicas after update, got %v", deploy.Spec.Replicas)
	}
}

// TestReconcile_OwnerReference verifies that the child Deployment has an
// OwnerReference pointing to the InferencePool CR.
func TestReconcile_OwnerReference(t *testing.T) {
	const (
		poolName = "ownerref-pool"
		ns       = "default"
	)
	pool := newPool(poolName, ns, 1)
	scheme := newScheme(t)

	r := &InferencePoolReconciler{
		Client: fake.NewClientBuilder().
			WithScheme(scheme).
			WithObjects(pool).
			WithStatusSubresource(pool).
			Build(),
		Scheme: scheme,
	}

	reconcile(t, r, poolName, ns)
	reconcile(t, r, poolName, ns)

	deploy := &appsv1.Deployment{}
	if err := r.Client.Get(context.Background(), types.NamespacedName{
		Name:      deploymentName(poolName),
		Namespace: ns,
	}, deploy); err != nil {
		t.Fatalf("get deployment: %v", err)
	}

	if len(deploy.OwnerReferences) == 0 {
		t.Error("expected at least one OwnerReference on child Deployment, got none")
	}
	found := false
	for _, ref := range deploy.OwnerReferences {
		if ref.Kind == "InferencePool" && ref.Name == poolName {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("OwnerReference pointing to InferencePool %q not found in %+v", poolName, deploy.OwnerReferences)
	}
}

// TestReconcile_NodeSelectorGPU verifies that a non-empty gpuType is propagated
// to the Deployment's nodeSelector.
func TestReconcile_NodeSelectorGPU(t *testing.T) {
	const (
		poolName = "gpu-selector-pool"
		ns       = "default"
	)
	pool := newPool(poolName, ns, 1)
	pool.Spec.Resources.GPUType = "nvidia-h100"
	scheme := newScheme(t)

	r := &InferencePoolReconciler{
		Client: fake.NewClientBuilder().
			WithScheme(scheme).
			WithObjects(pool).
			WithStatusSubresource(pool).
			Build(),
		Scheme: scheme,
	}

	reconcile(t, r, poolName, ns)
	reconcile(t, r, poolName, ns)

	deploy := &appsv1.Deployment{}
	if err := r.Client.Get(context.Background(), types.NamespacedName{
		Name:      deploymentName(poolName),
		Namespace: ns,
	}, deploy); err != nil {
		t.Fatalf("get deployment: %v", err)
	}

	val, ok := deploy.Spec.Template.Spec.NodeSelector["nvidia.com/gpu.product"]
	if !ok || val != "nvidia-h100" {
		t.Errorf("expected nodeSelector nvidia.com/gpu.product=nvidia-h100, got %v", deploy.Spec.Template.Spec.NodeSelector)
	}
}
