// Package v1alpha1 contains API Schema definitions for the inference.tokenlabs.run/v1alpha1 API group.
// +groupName=inference.tokenlabs.run
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

// SchemeGroupVersion is the group version used to register these objects.
var SchemeGroupVersion = schema.GroupVersion{Group: "inference.tokenlabs.run", Version: "v1alpha1"}

// SchemeBuilder is used to add functions to this group's scheme.
var SchemeBuilder = runtime.NewSchemeBuilder(addKnownTypes)

// AddToScheme adds the types in this group-version to the given scheme.
var AddToScheme = SchemeBuilder.AddToScheme

func addKnownTypes(scheme *runtime.Scheme) error {
	scheme.AddKnownTypes(SchemeGroupVersion,
		&InferencePool{},
		&InferencePoolList{},
	)
	metav1.AddToGroupVersion(scheme, SchemeGroupVersion)
	return nil
}

// ModelSpec defines the model to be served.
type ModelSpec struct {
	// Repository is the HuggingFace model repo identifier, e.g. "meta-llama/Meta-Llama-3-70B".
	// +kubebuilder:validation:Required
	Repository string `json:"repository"`

	// Quantization is the quantization format, e.g. "fp8", "bfloat16".
	// +kubebuilder:validation:Optional
	// +kubebuilder:default="bfloat16"
	Quantization string `json:"quantization,omitempty"`
}

// ResourceSpec defines compute resource requirements.
type ResourceSpec struct {
	// GPUType is the GPU product label selector, e.g. "nvidia-h100".
	// +kubebuilder:validation:Optional
	GPUType string `json:"gpuType,omitempty"`

	// Replicas is the number of vLLM worker pods.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:default=1
	Replicas int32 `json:"replicas"`
}

// GatewaySpec defines the ingress routing configuration.
type GatewaySpec struct {
	// Host is the public hostname, e.g. "api.tokenlabs.run".
	// +kubebuilder:validation:Required
	Host string `json:"host"`

	// Path is the URL path prefix, e.g. "/v1/completions".
	// +kubebuilder:validation:Required
	Path string `json:"path"`
}

// InferencePoolSpec defines the desired state of InferencePool.
type InferencePoolSpec struct {
	// Model describes the model artifact to serve.
	// +kubebuilder:validation:Required
	Model ModelSpec `json:"model"`

	// Resources describes GPU type and replica count.
	// +kubebuilder:validation:Required
	Resources ResourceSpec `json:"resources"`

	// Gateway describes the external routing configuration.
	// +kubebuilder:validation:Optional
	Gateway *GatewaySpec `json:"gateway,omitempty"`
}

// ConditionType is a type of InferencePool condition.
type ConditionType string

const (
	// ConditionReady indicates the InferencePool is fully operational.
	ConditionReady ConditionType = "Ready"
	// ConditionDeploymentAvailable indicates the vLLM Deployment is available.
	ConditionDeploymentAvailable ConditionType = "DeploymentAvailable"
)

// InferencePoolStatus defines the observed state of InferencePool.
type InferencePoolStatus struct {
	// Conditions store the status conditions of the InferencePool.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`

	// ReadyReplicas is the number of vLLM pods that are ready.
	// +optional
	ReadyReplicas int32 `json:"readyReplicas,omitempty"`

	// ObservedGeneration is the most recent generation observed for this InferencePool.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced,shortName=ipool
// +kubebuilder:printcolumn:name="Model",type=string,JSONPath=`.spec.model.repository`
// +kubebuilder:printcolumn:name="Replicas",type=integer,JSONPath=`.spec.resources.replicas`
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// InferencePool is the Schema for the inferencepools API.
// It manages the lifecycle of a vLLM model deployment, its llm-d proxy, and
// Gateway routing as a single declarative unit.
type InferencePool struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   InferencePoolSpec   `json:"spec,omitempty"`
	Status InferencePoolStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// InferencePoolList contains a list of InferencePool.
type InferencePoolList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []InferencePool `json:"items"`
}
