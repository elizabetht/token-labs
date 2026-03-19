// Package controller implements the TenantReconciler for the tokenlabs.io/v1alpha1 Tenant CRD.
package controller

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"

	tokenlabsv1alpha1 "github.com/elizabetht/token-labs/operator/api/v1alpha1"
)

const (
	// tenantFinalizer is the finalizer added to Tenant objects to ensure cleanup.
	tenantFinalizer = "tokenlabs.io/tenant-finalizer"

	// secretNamespace is the namespace where Authorino looks for API-key Secrets.
	secretNamespace = "kuadrant-system"

	// conditionReady is the Ready condition type.
	conditionReady = "Ready"
)

// TenantReconciler reconciles Tenant objects.
//
// +kubebuilder:rbac:groups=tokenlabs.io,resources=tenants,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=tokenlabs.io,resources=tenants/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=tokenlabs.io,resources=tenants/finalizers,verbs=update
// +kubebuilder:rbac:groups="",resources=secrets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
type TenantReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// SetupWithManager registers the TenantReconciler with the controller-runtime manager.
func (r *TenantReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&tokenlabsv1alpha1.Tenant{}).
		Owns(&corev1.Secret{}).
		Complete(r)
}

// Reconcile is the main reconciliation loop for the Tenant CRD.
func (r *TenantReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	tenant := &tokenlabsv1alpha1.Tenant{}
	if err := r.Get(ctx, req.NamespacedName, tenant); err != nil {
		if errors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	// Handle deletion via finalizer.
	if !tenant.DeletionTimestamp.IsZero() {
		return r.handleDeletion(ctx, tenant)
	}

	// Ensure finalizer is present.
	if !controllerutil.ContainsFinalizer(tenant, tenantFinalizer) {
		controllerutil.AddFinalizer(tenant, tenantFinalizer)
		if err := r.Update(ctx, tenant); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{Requeue: true}, nil
	}

	// Reconcile the API-key Secret.
	result, err := r.reconcileSecret(ctx, tenant)
	if err != nil {
		logger.Error(err, "failed to reconcile Secret")
		if statusErr := r.setReadyCondition(ctx, tenant, metav1.ConditionFalse, "SecretReconcileError", err.Error()); statusErr != nil {
			logger.Error(statusErr, "failed to update status")
		}
		return ctrl.Result{}, err
	}

	// Check key rotation.
	rotationResult, err := r.reconcileKeyRotation(ctx, tenant)
	if err != nil {
		logger.Error(err, "failed to reconcile key rotation")
		return ctrl.Result{}, err
	}
	if rotationResult.RequeueAfter > 0 {
		result = rotationResult
	}

	// Update status.
	if err := r.setReadyCondition(ctx, tenant, metav1.ConditionTrue, "Reconciled", "Tenant reconciled successfully"); err != nil {
		return ctrl.Result{}, err
	}

	return result, nil
}

// reconcileSecret ensures the Authorino API-key Secret exists and is up-to-date.
func (r *TenantReconciler) reconcileSecret(ctx context.Context, tenant *tokenlabsv1alpha1.Tenant) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	secretName := secretNameForTenant(tenant.Name)

	existing := &corev1.Secret{}
	err := r.Get(ctx, types.NamespacedName{Name: secretName, Namespace: secretNamespace}, existing)

	if errors.IsNotFound(err) {
		// Create a new API key and Secret.
		apiKey, genErr := generateAPIKey()
		if genErr != nil {
			return ctrl.Result{}, fmt.Errorf("generating API key: %w", genErr)
		}

		secret := r.buildSecret(tenant, secretName, apiKey)
		if err := controllerutil.SetControllerReference(tenant, secret, r.Scheme); err != nil {
			return ctrl.Result{}, fmt.Errorf("setting owner reference: %w", err)
		}
		if err := r.Create(ctx, secret); err != nil {
			return ctrl.Result{}, fmt.Errorf("creating Secret: %w", err)
		}
		logger.Info("created API-key Secret", "secret", secretName)

		// Update status with secret reference.
		patch := client.MergeFrom(tenant.DeepCopy())
		tenant.Status.APIKeySecretRef = secretName
		if err := r.Status().Patch(ctx, tenant, patch); err != nil {
			return ctrl.Result{}, fmt.Errorf("updating status: %w", err)
		}
		return ctrl.Result{}, nil
	}
	if err != nil {
		return ctrl.Result{}, fmt.Errorf("getting Secret: %w", err)
	}

	// Secret exists — ensure tier annotation is up-to-date.
	desired := string(tenant.Spec.Tier)
	if existing.Annotations[annotationGroups] != desired {
		patch := client.MergeFrom(existing.DeepCopy())
		if existing.Annotations == nil {
			existing.Annotations = map[string]string{}
		}
		existing.Annotations[annotationGroups] = desired
		if err := r.Patch(ctx, existing, patch); err != nil {
			return ctrl.Result{}, fmt.Errorf("updating Secret tier annotation: %w", err)
		}
		logger.Info("updated tier annotation on Secret", "secret", secretName, "tier", desired)
	}

	// Ensure status.apiKeySecretRef is populated (e.g. after operator restart).
	if tenant.Status.APIKeySecretRef != secretName {
		patch := client.MergeFrom(tenant.DeepCopy())
		tenant.Status.APIKeySecretRef = secretName
		if err := r.Status().Patch(ctx, tenant, patch); err != nil {
			return ctrl.Result{}, fmt.Errorf("updating status: %w", err)
		}
	}

	return ctrl.Result{}, nil
}

// reconcileKeyRotation checks whether a key rotation is due and performs it if so.
func (r *TenantReconciler) reconcileKeyRotation(ctx context.Context, tenant *tokenlabsv1alpha1.Tenant) (ctrl.Result, error) {
	krp := tenant.Spec.KeyRotationPolicy
	if krp == nil || !krp.Enabled || krp.IntervalDays <= 0 {
		return ctrl.Result{}, nil
	}

	interval := time.Duration(krp.IntervalDays) * 24 * time.Hour
	lastRotation := tenant.Status.LastKeyRotation
	now := time.Now()

	if lastRotation != nil && now.Before(lastRotation.Add(interval)) {
		// Not due yet — requeue when it is.
		nextRotation := lastRotation.Add(interval)
		return ctrl.Result{RequeueAfter: time.Until(nextRotation)}, nil
	}

	// Rotation is due (or has never happened).
	if err := r.rotateKey(ctx, tenant); err != nil {
		return ctrl.Result{}, err
	}
	return ctrl.Result{RequeueAfter: interval}, nil
}

// rotateKey generates a new API key and atomically updates the Secret.
func (r *TenantReconciler) rotateKey(ctx context.Context, tenant *tokenlabsv1alpha1.Tenant) error {
	logger := log.FromContext(ctx)
	secretName := secretNameForTenant(tenant.Name)

	newKey, err := generateAPIKey()
	if err != nil {
		return fmt.Errorf("generating new API key: %w", err)
	}

	secret := &corev1.Secret{}
	if err := r.Get(ctx, types.NamespacedName{Name: secretName, Namespace: secretNamespace}, secret); err != nil {
		return fmt.Errorf("getting Secret for rotation: %w", err)
	}

	if secret.Data == nil {
		secret.Data = map[string][]byte{}
	}
	secret.Data["api_key"] = []byte(newKey)
	// Clear StringData so only Data is used after rotation.
	secret.StringData = nil
	if err := r.Update(ctx, secret); err != nil {
		return fmt.Errorf("updating Secret with new key: %w", err)
	}

	now := metav1.Now()
	tenantPatch := client.MergeFrom(tenant.DeepCopy())
	tenant.Status.LastKeyRotation = &now
	if err := r.Status().Patch(ctx, tenant, tenantPatch); err != nil {
		return fmt.Errorf("updating LastKeyRotation in status: %w", err)
	}

	logger.Info("rotated API key", "tenant", tenant.Name, "secret", secretName)
	return nil
}

// handleDeletion removes the finalizer (the Secret is garbage-collected via ownerReference).
func (r *TenantReconciler) handleDeletion(ctx context.Context, tenant *tokenlabsv1alpha1.Tenant) (ctrl.Result, error) {
	if !controllerutil.ContainsFinalizer(tenant, tenantFinalizer) {
		return ctrl.Result{}, nil
	}

	// The Secret is in a different namespace (kuadrant-system) so cross-namespace
	// owner references are not supported. Delete it explicitly.
	secretName := secretNameForTenant(tenant.Name)
	secret := &corev1.Secret{}
	err := r.Get(ctx, types.NamespacedName{Name: secretName, Namespace: secretNamespace}, secret)
	if err == nil {
		if delErr := r.Delete(ctx, secret); delErr != nil && !errors.IsNotFound(delErr) {
			return ctrl.Result{}, fmt.Errorf("deleting Secret: %w", delErr)
		}
		log.FromContext(ctx).Info("deleted API-key Secret", "secret", secretName)
	} else if !errors.IsNotFound(err) {
		return ctrl.Result{}, fmt.Errorf("getting Secret for deletion: %w", err)
	}

	controllerutil.RemoveFinalizer(tenant, tenantFinalizer)
	if err := r.Update(ctx, tenant); err != nil {
		return ctrl.Result{}, err
	}
	return ctrl.Result{}, nil
}

// setReadyCondition patches the Ready condition in the Tenant status.
func (r *TenantReconciler) setReadyCondition(ctx context.Context, tenant *tokenlabsv1alpha1.Tenant, status metav1.ConditionStatus, reason, message string) error {
	patch := client.MergeFrom(tenant.DeepCopy())

	now := metav1.Now()
	cond := metav1.Condition{
		Type:               conditionReady,
		Status:             status,
		Reason:             reason,
		Message:            message,
		LastTransitionTime: now,
		ObservedGeneration: tenant.Generation,
	}

	// Replace existing condition of same type if present.
	found := false
	for i, c := range tenant.Status.Conditions {
		if c.Type == conditionReady {
			if c.Status == status {
				// Preserve LastTransitionTime if status hasn't changed.
				cond.LastTransitionTime = c.LastTransitionTime
			}
			tenant.Status.Conditions[i] = cond
			found = true
			break
		}
	}
	if !found {
		tenant.Status.Conditions = append(tenant.Status.Conditions, cond)
	}

	return r.Status().Patch(ctx, tenant, patch)
}

// buildSecret constructs the Authorino API-key Secret for a Tenant.
func (r *TenantReconciler) buildSecret(tenant *tokenlabsv1alpha1.Tenant, name, apiKey string) *corev1.Secret {
	return &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: secretNamespace,
			Labels: map[string]string{
				"authorino.kuadrant.io/managed-by": "authorino",
				"app":                              "token-labs",
			},
			Annotations: map[string]string{
				annotationGroups: string(tenant.Spec.Tier),
				annotationUserID: tenant.Name,
			},
		},
		StringData: map[string]string{
			"api_key": apiKey,
		},
		Type: corev1.SecretTypeOpaque,
	}
}

// generateAPIKey returns a cryptographically random 32-byte hex string prefixed with "tlabs_".
func generateAPIKey() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return "tlabs_" + hex.EncodeToString(b), nil
}

// secretNameForTenant returns the canonical name of the API-key Secret for a tenant.
func secretNameForTenant(tenantName string) string {
	return "tenant-" + tenantName
}

const (
	annotationGroups = "kuadrant.io/groups"
	annotationUserID = "secret.kuadrant.io/user-id"
)
