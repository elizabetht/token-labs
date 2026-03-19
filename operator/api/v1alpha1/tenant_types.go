package v1alpha1

import metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

// TenantTier defines the service tier for a tenant.
// +kubebuilder:validation:Enum=free;pro;enterprise
type TenantTier string

const (
	TierFree       TenantTier = "free"
	TierPro        TenantTier = "pro"
	TierEnterprise TenantTier = "enterprise"
)

// KeyRotationPolicy controls automatic API key rotation for a Tenant.
type KeyRotationPolicy struct {
	// Enabled controls whether automatic key rotation is active.
	// +kubebuilder:default=false
	Enabled bool `json:"enabled"`

	// IntervalDays is the number of days between automatic key rotations.
	// Only used when Enabled is true.
	// +kubebuilder:validation:Minimum=1
	// +optional
	IntervalDays int `json:"intervalDays,omitempty"`
}

// TenantSpec defines the desired state of a Tenant.
type TenantSpec struct {
	// Tier is the service tier for this tenant (free, pro, or enterprise).
	// +kubebuilder:default=free
	Tier TenantTier `json:"tier"`

	// Company is the display name of the tenant's organisation.
	// +optional
	Company string `json:"company,omitempty"`

	// Email is the primary contact email address for this tenant.
	// +optional
	Email string `json:"email,omitempty"`

	// KeyRotationPolicy controls automatic API key rotation.
	// +optional
	KeyRotationPolicy *KeyRotationPolicy `json:"keyRotationPolicy,omitempty"`
}

// TenantStatus defines the observed state of a Tenant.
type TenantStatus struct {
	// APIKeySecretRef is the name of the Secret that holds the tenant's API key.
	// +optional
	APIKeySecretRef string `json:"apiKeySecretRef,omitempty"`

	// TokensConsumedToday is the number of LLM tokens consumed today by this tenant.
	// +optional
	TokensConsumedToday int64 `json:"tokensConsumedToday,omitempty"`

	// RateLimitHits is the number of rate limit rejections recorded for this tenant today.
	// +optional
	RateLimitHits int64 `json:"rateLimitHits,omitempty"`

	// LastKeyRotation is the timestamp of the most recent API key rotation.
	// +optional
	LastKeyRotation *metav1.Time `json:"lastKeyRotation,omitempty"`

	// Conditions reflect the current state of the Tenant.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Cluster,shortName=tn
// +kubebuilder:printcolumn:name="Tier",type=string,JSONPath=`.spec.tier`
// +kubebuilder:printcolumn:name="Secret",type=string,JSONPath=`.status.apiKeySecretRef`
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// Tenant is the Schema for the tenants API.
// It represents a first-class tenant whose lifecycle (API key creation,
// tier changes, and optional key rotation) is managed by the tenant-operator.
type Tenant struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   TenantSpec   `json:"spec,omitempty"`
	Status TenantStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// TenantList contains a list of Tenant.
type TenantList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Tenant `json:"items"`
}
