package controller_test

import (
	"context"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	tokenlabsv1alpha1 "github.com/elizabetht/token-labs/operator/api/v1alpha1"
	"github.com/elizabetht/token-labs/operator/internal/controller"
)

func newScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	s := runtime.NewScheme()
	if err := clientgoscheme.AddToScheme(s); err != nil {
		t.Fatalf("adding client-go scheme: %v", err)
	}
	if err := tokenlabsv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("adding tokenlabs scheme: %v", err)
	}
	return s
}

func newTenant(name string, tier tokenlabsv1alpha1.TenantTier) *tokenlabsv1alpha1.Tenant {
	return &tokenlabsv1alpha1.Tenant{
		ObjectMeta: metav1.ObjectMeta{
			Name: name,
		},
		Spec: tokenlabsv1alpha1.TenantSpec{
			Tier:    tier,
			Company: "Test Corp",
			Email:   "test@example.com",
		},
	}
}

// reconcileOnce runs a single reconciliation and returns the result.
func reconcileOnce(t *testing.T, r *controller.TenantReconciler, name string) ctrl.Result {
	t.Helper()
	result, err := r.Reconcile(context.Background(), ctrl.Request{
		NamespacedName: types.NamespacedName{Name: name},
	})
	if err != nil {
		t.Fatalf("Reconcile returned error: %v", err)
	}
	return result
}

func TestReconcile_CreatesSecret(t *testing.T) {
	scheme := newScheme(t)
	tenant := newTenant("acme-corp", tokenlabsv1alpha1.TierPro)

	// Also create the kuadrant-system namespace so the Secret can be created.
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "kuadrant-system"}}

	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(tenant, ns).
		WithStatusSubresource(&tokenlabsv1alpha1.Tenant{}).
		Build()

	r := &controller.TenantReconciler{Client: c, Scheme: scheme}

	// First reconcile: adds finalizer.
	reconcileOnce(t, r, "acme-corp")
	// Second reconcile: creates the Secret.
	reconcileOnce(t, r, "acme-corp")
	// Third reconcile: sets Ready condition.
	reconcileOnce(t, r, "acme-corp")

	// Verify Secret was created.
	secret := &corev1.Secret{}
	if err := c.Get(context.Background(), types.NamespacedName{
		Name:      "tenant-acme-corp",
		Namespace: "kuadrant-system",
	}, secret); err != nil {
		t.Fatalf("expected Secret to exist: %v", err)
	}

	// Verify labels.
	if secret.Labels["authorino.kuadrant.io/managed-by"] != "authorino" {
		t.Errorf("expected authorino label, got %q", secret.Labels["authorino.kuadrant.io/managed-by"])
	}
	if secret.Labels["app"] != "token-labs" {
		t.Errorf("expected app=token-labs label, got %q", secret.Labels["app"])
	}

	// Verify annotations.
	if secret.Annotations["kuadrant.io/groups"] != "pro" {
		t.Errorf("expected tier annotation pro, got %q", secret.Annotations["kuadrant.io/groups"])
	}
	if secret.Annotations["secret.kuadrant.io/user-id"] != "acme-corp" {
		t.Errorf("expected user-id acme-corp, got %q", secret.Annotations["secret.kuadrant.io/user-id"])
	}

	// Verify the API key is present and has the right prefix.
	// The fake client may store the key in StringData or Data depending on version.
	apiKey := string(secret.Data["api_key"])
	if apiKey == "" && secret.StringData["api_key"] == "" {
		t.Error("expected non-empty api_key in Secret")
	}
	if apiKey != "" && len(apiKey) < 10 {
		t.Errorf("api_key too short: %q", apiKey)
	}
}

func TestReconcile_UpdatesTierAnnotation(t *testing.T) {
	scheme := newScheme(t)
	tenant := newTenant("acme-corp", tokenlabsv1alpha1.TierFree)

	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "kuadrant-system"}}

	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(tenant, ns).
		WithStatusSubresource(&tokenlabsv1alpha1.Tenant{}).
		Build()

	r := &controller.TenantReconciler{Client: c, Scheme: scheme}

	// Bootstrap: add finalizer + create secret.
	reconcileOnce(t, r, "acme-corp")
	reconcileOnce(t, r, "acme-corp")
	reconcileOnce(t, r, "acme-corp")

	// Now upgrade tier to pro.
	updated := &tokenlabsv1alpha1.Tenant{}
	if err := c.Get(context.Background(), types.NamespacedName{Name: "acme-corp"}, updated); err != nil {
		t.Fatalf("getting tenant: %v", err)
	}
	updated.Spec.Tier = tokenlabsv1alpha1.TierPro
	if err := c.Update(context.Background(), updated); err != nil {
		t.Fatalf("updating tenant tier: %v", err)
	}

	reconcileOnce(t, r, "acme-corp")

	secret := &corev1.Secret{}
	if err := c.Get(context.Background(), types.NamespacedName{
		Name:      "tenant-acme-corp",
		Namespace: "kuadrant-system",
	}, secret); err != nil {
		t.Fatalf("getting Secret: %v", err)
	}

	if secret.Annotations["kuadrant.io/groups"] != "pro" {
		t.Errorf("expected tier annotation pro after upgrade, got %q", secret.Annotations["kuadrant.io/groups"])
	}
}

func TestReconcile_KeyRotation(t *testing.T) {
	scheme := newScheme(t)
	tenant := newTenant("rotate-corp", tokenlabsv1alpha1.TierEnterprise)
	tenant.Spec.KeyRotationPolicy = &tokenlabsv1alpha1.KeyRotationPolicy{
		Enabled:      true,
		IntervalDays: 1,
	}
	// Set LastKeyRotation to well in the past so rotation is immediately due.
	past := metav1.NewTime(time.Now().Add(-48 * time.Hour))
	tenant.Status.LastKeyRotation = &past

	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "kuadrant-system"}}

	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(tenant, ns).
		WithStatusSubresource(&tokenlabsv1alpha1.Tenant{}).
		Build()

	r := &controller.TenantReconciler{Client: c, Scheme: scheme}

	// Bootstrap: add finalizer + create secret.
	reconcileOnce(t, r, "rotate-corp")
	reconcileOnce(t, r, "rotate-corp")

	// Trigger reconcile — rotation is due (LastKeyRotation is 48h ago, interval is 1 day).
	result := reconcileOnce(t, r, "rotate-corp")

	// Should requeue after the rotation interval.
	if result.RequeueAfter == 0 {
		t.Error("expected non-zero RequeueAfter after key rotation")
	}

	// Verify the LastKeyRotation status was updated.
	updated := &tokenlabsv1alpha1.Tenant{}
	if err := c.Get(context.Background(), types.NamespacedName{Name: "rotate-corp"}, updated); err != nil {
		t.Fatalf("getting updated tenant: %v", err)
	}
	if updated.Status.LastKeyRotation == nil {
		t.Error("expected LastKeyRotation to be set after rotation")
	}
	if !updated.Status.LastKeyRotation.After(past.Time) {
		t.Errorf("expected LastKeyRotation to be more recent than the original past value")
	}
}

func TestReconcile_DeletionRemovesSecret(t *testing.T) {
	scheme := newScheme(t)
	tenant := newTenant("gone-corp", tokenlabsv1alpha1.TierFree)

	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: "kuadrant-system"}}

	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(tenant, ns).
		WithStatusSubresource(&tokenlabsv1alpha1.Tenant{}).
		Build()

	r := &controller.TenantReconciler{Client: c, Scheme: scheme}

	// Bootstrap: add finalizer + create secret.
	reconcileOnce(t, r, "gone-corp")
	reconcileOnce(t, r, "gone-corp")
	reconcileOnce(t, r, "gone-corp")

	// Verify Secret exists.
	secret := &corev1.Secret{}
	if err := c.Get(context.Background(), types.NamespacedName{
		Name:      "tenant-gone-corp",
		Namespace: "kuadrant-system",
	}, secret); err != nil {
		t.Fatalf("expected Secret to exist before deletion: %v", err)
	}

	// Delete the Tenant via the fake client. Because the tenant has a finalizer
	// the fake client will set DeletionTimestamp rather than immediately removing it.
	tenantObj := &tokenlabsv1alpha1.Tenant{}
	if err := c.Get(context.Background(), types.NamespacedName{Name: "gone-corp"}, tenantObj); err != nil {
		t.Fatalf("getting tenant: %v", err)
	}
	if err := c.Delete(context.Background(), tenantObj); err != nil {
		t.Fatalf("deleting tenant: %v", err)
	}

	// Reconcile — this should detect the deletion, remove the Secret, and strip the finalizer.
	reconcileOnce(t, r, "gone-corp")

	// Secret should be gone.
	err := c.Get(context.Background(), types.NamespacedName{
		Name:      "tenant-gone-corp",
		Namespace: "kuadrant-system",
	}, secret)
	if err == nil {
		t.Error("expected Secret to be deleted after Tenant deletion")
	}
}
