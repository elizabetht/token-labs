package controllers_test

import (
	"testing"

	tokenlabsv1alpha1 "github.com/elizabetht/token-labs/operator/api/v1alpha1"
	"github.com/elizabetht/token-labs/operator/controllers"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func newReconciler(objs ...runtime.Object) *controllers.ModelDeploymentReconciler {
	scheme := runtime.NewScheme()
	_ = tokenlabsv1alpha1.AddToScheme(scheme)

	builder := fake.NewClientBuilder().WithScheme(scheme)
	return &controllers.ModelDeploymentReconciler{
		Client: builder.Build(),
		Scheme: scheme,
	}
}

func newModelDeployment(name, namespace string) *tokenlabsv1alpha1.ModelDeployment {
	return &tokenlabsv1alpha1.ModelDeployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
		},
		Spec: tokenlabsv1alpha1.ModelDeploymentSpec{
			ModelID:  "nvidia/Qwen3-14B-NVFP4",
			Replicas: 1,
			NodeSelector: map[string]string{
				"kubernetes.io/hostname": "spark-02",
			},
			Resources: tokenlabsv1alpha1.ResourcesSpec{
				GPUMemoryFraction: "0.9",
			},
			Quantization: "nvfp4",
			Routing: tokenlabsv1alpha1.RoutingSpec{
				ModelAliases: []string{"qwen3-14b", "qwen3"},
			},
		},
	}
}

func TestInferencePoolName(t *testing.T) {
	tests := []struct {
		mdName   string
		expected string
	}{
		{"qwen3-14b", "llm-d-inferencepool-qwen3-14b"},
		{"nemotron-vl", "llm-d-inferencepool-nemotron-vl"},
		{"my-model", "llm-d-inferencepool-my-model"},
	}
	for _, tt := range tests {
		got := controllers.InferencePoolName(tt.mdName)
		if got != tt.expected {
			t.Errorf("InferencePoolName(%q) = %q, want %q", tt.mdName, got, tt.expected)
		}
	}
}

func TestDeploymentName(t *testing.T) {
	tests := []struct {
		mdName   string
		expected string
	}{
		{"qwen3-14b", "modeldeployment-qwen3-14b"},
		{"nemotron-vl", "modeldeployment-nemotron-vl"},
	}
	for _, tt := range tests {
		got := controllers.DeploymentName(tt.mdName)
		if got != tt.expected {
			t.Errorf("DeploymentName(%q) = %q, want %q", tt.mdName, got, tt.expected)
		}
	}
}

func TestPodLabels(t *testing.T) {
	md := newModelDeployment("qwen3-14b", "token-labs")
	labels := controllers.PodLabels(md)

	required := map[string]string{
		"llm-d.ai/inference-serving":  "true",
		"llm-d.ai/model":              "qwen3-14b",
		"app.kubernetes.io/name":      "modeldeployment",
		"app.kubernetes.io/instance":  "qwen3-14b",
		"app.kubernetes.io/component": "vllm",
	}
	for k, v := range required {
		if labels[k] != v {
			t.Errorf("podLabels[%q] = %q, want %q", k, labels[k], v)
		}
	}
}

func TestBuildDeployment_Args(t *testing.T) {
	md := newModelDeployment("qwen3-14b", "token-labs")
	r := newReconciler()
	dep := r.BuildDeployment(md)

	if dep.Name != "modeldeployment-qwen3-14b" {
		t.Errorf("Deployment name = %q, want modeldeployment-qwen3-14b", dep.Name)
	}
	if dep.Namespace != "token-labs" {
		t.Errorf("Deployment namespace = %q, want token-labs", dep.Namespace)
	}
	if *dep.Spec.Replicas != 1 {
		t.Errorf("Replicas = %d, want 1", *dep.Spec.Replicas)
	}

	container := dep.Spec.Template.Spec.Containers[0]
	if container.Name != "vllm" {
		t.Errorf("container name = %q, want vllm", container.Name)
	}

	// Should contain --model and --gpu-memory-utilization args.
	argSet := make(map[string]bool)
	for _, a := range container.Args {
		argSet[a] = true
	}
	if !argSet["--model=nvidia/Qwen3-14B-NVFP4"] {
		t.Errorf("expected --model=nvidia/Qwen3-14B-NVFP4 in args %v", container.Args)
	}
	if !argSet["--gpu-memory-utilization=0.9"] {
		t.Errorf("expected --gpu-memory-utilization=0.9 in args %v", container.Args)
	}
	if !argSet["--quantization=nvfp4"] {
		t.Errorf("expected --quantization=nvfp4 in args %v", container.Args)
	}
}

func TestBuildDeployment_NodeSelector(t *testing.T) {
	md := newModelDeployment("qwen3-14b", "token-labs")
	r := newReconciler()
	dep := r.BuildDeployment(md)

	ns := dep.Spec.Template.Spec.NodeSelector
	if ns["kubernetes.io/hostname"] != "spark-02" {
		t.Errorf("NodeSelector[kubernetes.io/hostname] = %q, want spark-02", ns["kubernetes.io/hostname"])
	}
}

func TestBuildInferencePool(t *testing.T) {
	md := newModelDeployment("qwen3-14b", "token-labs")
	r := newReconciler()
	pool := r.BuildInferencePool(md)

	if pool.GetName() != "llm-d-inferencepool-qwen3-14b" {
		t.Errorf("InferencePool name = %q, want llm-d-inferencepool-qwen3-14b", pool.GetName())
	}
	if pool.GetNamespace() != "token-labs" {
		t.Errorf("InferencePool namespace = %q, want token-labs", pool.GetNamespace())
	}

	port, _, _ := unstructured.NestedInt64(pool.Object, "spec", "targetPortNumber")
	if port != 8000 {
		t.Errorf("targetPortNumber = %d, want 8000", port)
	}
}

func TestSetCondition_Upsert(t *testing.T) {
	cond1 := metav1.Condition{Type: "Ready", Status: metav1.ConditionFalse, Reason: "Deploying"}
	conditions := controllers.SetCondition(nil, cond1)
	if len(conditions) != 1 {
		t.Fatalf("expected 1 condition, got %d", len(conditions))
	}

	cond2 := metav1.Condition{Type: "Ready", Status: metav1.ConditionTrue, Reason: "Running"}
	conditions = controllers.SetCondition(conditions, cond2)
	if len(conditions) != 1 {
		t.Fatalf("expected 1 condition after upsert, got %d", len(conditions))
	}
	if conditions[0].Status != metav1.ConditionTrue {
		t.Errorf("condition status = %q, want True", conditions[0].Status)
	}
}

func TestModelDeploymentPhases(t *testing.T) {
	if tokenlabsv1alpha1.ModelDeploymentPhasePending != "Pending" {
		t.Error("phase Pending mismatch")
	}
	if tokenlabsv1alpha1.ModelDeploymentPhaseDeploying != "Deploying" {
		t.Error("phase Deploying mismatch")
	}
	if tokenlabsv1alpha1.ModelDeploymentPhaseRunning != "Running" {
		t.Error("phase Running mismatch")
	}
	if tokenlabsv1alpha1.ModelDeploymentPhaseFailed != "Failed" {
		t.Error("phase Failed mismatch")
	}
}
