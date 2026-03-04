package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ModelDeploymentPhase represents the lifecycle phase of a ModelDeployment.
type ModelDeploymentPhase string

const (
	// ModelDeploymentPhasePending means the deployment has been accepted but not yet started.
	ModelDeploymentPhasePending ModelDeploymentPhase = "Pending"
	// ModelDeploymentPhaseDeploying means underlying resources are being created.
	ModelDeploymentPhaseDeploying ModelDeploymentPhase = "Deploying"
	// ModelDeploymentPhaseRunning means the model is fully ready to serve requests.
	ModelDeploymentPhaseRunning ModelDeploymentPhase = "Running"
	// ModelDeploymentPhaseFailed means one or more resources failed to become ready.
	ModelDeploymentPhaseFailed ModelDeploymentPhase = "Failed"
)

// ResourcesSpec specifies GPU resource configuration for the model.
type ResourcesSpec struct {
	// GPUMemoryFraction is the fraction of GPU memory to allocate (e.g. "0.9").
	// Passed to vLLM as --gpu-memory-utilization.
	// +optional
	GPUMemoryFraction string `json:"gpuMemoryFraction,omitempty"`
}

// RoutingSpec defines how the model is exposed via AIGatewayRoute.
type RoutingSpec struct {
	// ModelAliases lists the model name values that route to this deployment.
	// Each alias is matched against the x-ai-eg-model header set by Envoy AI Gateway.
	// +kubebuilder:validation:MinItems=1
	ModelAliases []string `json:"modelAliases"`
}

// ModelDeploymentSpec defines the desired state of a ModelDeployment.
type ModelDeploymentSpec struct {
	// ModelID is the Hugging Face model identifier (e.g. "nvidia/Qwen3-14B-NVFP4").
	// +kubebuilder:validation:MinLength=1
	ModelID string `json:"modelId"`

	// NodeSelector pins the vLLM pods to a specific node or set of nodes.
	// +optional
	NodeSelector map[string]string `json:"nodeSelector,omitempty"`

	// Resources configures GPU memory allocation for the model.
	// +optional
	Resources ResourcesSpec `json:"resources,omitempty"`

	// Quantization specifies the quantization format (e.g. "nvfp4", "fp8", "bfloat16").
	// Passed as --quantization to vLLM when not empty (non-bfloat16 native dtypes only).
	// +optional
	Quantization string `json:"quantization,omitempty"`

	// Replicas is the desired number of vLLM decode worker pods.
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	Replicas int32 `json:"replicas"`

	// Routing describes how requests are routed to this deployment via AIGatewayRoute.
	Routing RoutingSpec `json:"routing"`
}

// ModelDeploymentStatus defines the observed state of a ModelDeployment.
type ModelDeploymentStatus struct {
	// Phase summarises the overall lifecycle state of the deployment.
	// One of: Pending, Deploying, Running, Failed.
	// +optional
	Phase ModelDeploymentPhase `json:"phase,omitempty"`

	// InferencepoolRef is the name of the InferencePool created for this deployment.
	// +optional
	InferencepoolRef string `json:"inferencepoolRef,omitempty"`

	// Conditions holds detailed status conditions following the standard Kubernetes conventions.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:subresource:scale:specpath=.spec.replicas,statuspath=.status.replicas
// +kubebuilder:printcolumn:name="Model",type=string,JSONPath=".spec.modelId"
// +kubebuilder:printcolumn:name="Replicas",type=integer,JSONPath=".spec.replicas"
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=".status.phase"
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=".metadata.creationTimestamp"
// +kubebuilder:resource:scope=Namespaced,shortName=md

// ModelDeployment is the schema for the modeldeployments API.
// It bundles a vLLM ModelService, an InferencePool, and an AIGatewayRoute rule
// into a single declarative unit so that onboarding a new model requires only
// one `kubectl apply`.
type ModelDeployment struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ModelDeploymentSpec   `json:"spec,omitempty"`
	Status ModelDeploymentStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ModelDeploymentList contains a list of ModelDeployment resources.
type ModelDeploymentList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ModelDeployment `json:"items"`
}

func init() {
	SchemeBuilder.Register(&ModelDeployment{}, &ModelDeploymentList{})
}
