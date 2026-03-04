package controllers

import (
	"context"
	"fmt"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"

	tokenlabsv1alpha1 "github.com/elizabetht/token-labs/operator/api/v1alpha1"
)

const (
	// finalizerName is added to ModelDeployment to ensure cleanup of routing rules.
	finalizerName = "tokenlabs.io/modeldeployment-finalizer"

	// aigwRouteName is the AIGatewayRoute that the operator patches for routing rules.
	aigwRouteName = "llm-inference"

	// aigwRouteNamespace is the namespace where the AIGatewayRoute lives.
	aigwRouteNamespace = "token-labs"

	// vllmImage is the default vLLM serving image.
	vllmImage = "ghcr.io/elizabetht/token-labs/vllm-serve:v0.4.0"

	// vllmPort is the port vLLM listens on.
	vllmPort = 8000

	// conditionTypeReady is the ready condition type.
	conditionTypeReady = "Ready"
)

// GroupVersionResource definitions for external CRDs.
var (
	inferencePoolGVR = schema.GroupVersionResource{
		Group:    "inference.networking.k8s.io",
		Version:  "v1alpha2",
		Resource: "inferencepools",
	}
	aigwRouteGVK = schema.GroupVersionKind{
		Group:   "aigateway.envoyproxy.io",
		Version: "v1alpha1",
		Kind:    "AIGatewayRoute",
	}
)

// ModelDeploymentReconciler reconciles ModelDeployment objects.
//
// +kubebuilder:rbac:groups=tokenlabs.io,resources=modeldeployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=tokenlabs.io,resources=modeldeployments/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=tokenlabs.io,resources=modeldeployments/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=services,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=pods,verbs=get;list;watch
// +kubebuilder:rbac:groups=inference.networking.k8s.io,resources=inferencepools,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=aigateway.envoyproxy.io,resources=aigwroutes,verbs=get;list;watch;patch;update
type ModelDeploymentReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// Reconcile is the main reconciliation loop for ModelDeployment resources.
func (r *ModelDeploymentReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	md := &tokenlabsv1alpha1.ModelDeployment{}
	if err := r.Get(ctx, req.NamespacedName, md); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	// Handle deletion.
	if !md.DeletionTimestamp.IsZero() {
		return r.reconcileDelete(ctx, md)
	}

	// Ensure the finalizer is present so we can clean up on deletion.
	if !controllerutil.ContainsFinalizer(md, finalizerName) {
		controllerutil.AddFinalizer(md, finalizerName)
		if err := r.Update(ctx, md); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	// Reconcile child resources.
	if err := r.reconcileDeployment(ctx, md); err != nil {
		logger.Error(err, "failed to reconcile Deployment")
		return ctrl.Result{}, err
	}
	if err := r.reconcileService(ctx, md); err != nil {
		logger.Error(err, "failed to reconcile Service")
		return ctrl.Result{}, err
	}
	if err := r.reconcileInferencePool(ctx, md); err != nil {
		logger.Error(err, "failed to reconcile InferencePool")
		return ctrl.Result{}, err
	}
	if err := r.reconcileAIGatewayRoute(ctx, md); err != nil {
		logger.Error(err, "failed to reconcile AIGatewayRoute")
		return ctrl.Result{}, err
	}

	// Update status phase based on Deployment readiness.
	return ctrl.Result{}, r.updateStatus(ctx, md)
}

// reconcileDeployment creates or updates the vLLM Deployment for the ModelDeployment.
func (r *ModelDeploymentReconciler) reconcileDeployment(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) error {
	desired := r.BuildDeployment(md)
	if err := controllerutil.SetControllerReference(md, desired, r.Scheme); err != nil {
		return err
	}

	existing := &appsv1.Deployment{}
	err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, existing)
	if errors.IsNotFound(err) {
		return r.Create(ctx, desired)
	}
	if err != nil {
		return err
	}

	// Update replicas and template if spec changed.
	existing.Spec.Replicas = desired.Spec.Replicas
	existing.Spec.Template = desired.Spec.Template
	existing.Spec.Selector = desired.Spec.Selector
	return r.Update(ctx, existing)
}

// reconcileService creates or updates the headless Service for the ModelDeployment.
func (r *ModelDeploymentReconciler) reconcileService(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) error {
	desired := r.buildService(md)
	if err := controllerutil.SetControllerReference(md, desired, r.Scheme); err != nil {
		return err
	}

	existing := &corev1.Service{}
	err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, existing)
	if errors.IsNotFound(err) {
		return r.Create(ctx, desired)
	}
	return err
}

// reconcileInferencePool creates or updates the InferencePool for the ModelDeployment.
func (r *ModelDeploymentReconciler) reconcileInferencePool(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) error {
	desired := r.BuildInferencePool(md)

	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(schema.GroupVersionKind{
		Group:   inferencePoolGVR.Group,
		Version: inferencePoolGVR.Version,
		Kind:    "InferencePool",
	})
	err := r.Get(ctx, types.NamespacedName{Name: desired.GetName(), Namespace: desired.GetNamespace()}, existing)
	if errors.IsNotFound(err) {
		return r.Create(ctx, desired)
	}
	if err != nil {
		return err
	}

	// Preserve resourceVersion for the update.
	desired.SetResourceVersion(existing.GetResourceVersion())
	return r.Update(ctx, desired)
}

// reconcileAIGatewayRoute ensures the routing rule for this ModelDeployment exists in the AIGatewayRoute.
// It uses a strategic-merge-style patch via Server-Side Apply.
func (r *ModelDeploymentReconciler) reconcileAIGatewayRoute(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) error {
	route := &unstructured.Unstructured{}
	route.SetGroupVersionKind(aigwRouteGVK)
	if err := r.Get(ctx, types.NamespacedName{Name: aigwRouteName, Namespace: aigwRouteNamespace}, route); err != nil {
		// If the AIGatewayRoute doesn't exist yet, skip and requeue.
		if errors.IsNotFound(err) {
			return nil
		}
		return err
	}

	rules, _, _ := unstructured.NestedSlice(route.Object, "spec", "rules")
	poolName := InferencePoolName(md.Name)

	// Check if the rules for all model aliases already exist.
	existingAliases := map[string]bool{}
	for _, rule := range rules {
		rm, ok := rule.(map[string]interface{})
		if !ok {
			continue
		}
		matches, _, _ := unstructured.NestedSlice(rm, "matches")
		for _, match := range matches {
			mm, ok := match.(map[string]interface{})
			if !ok {
				continue
			}
			headers, _, _ := unstructured.NestedSlice(mm, "headers")
			for _, header := range headers {
				hm, ok := header.(map[string]interface{})
				if !ok {
					continue
				}
				if hm["name"] == "x-ai-eg-model" {
					existingAliases[fmt.Sprint(hm["value"])] = true
				}
			}
		}
	}

	// Add rules for any alias that is not already routed.
	changed := false
	for _, alias := range md.Spec.Routing.ModelAliases {
		if existingAliases[alias] {
			continue
		}
		rule := map[string]interface{}{
			"matches": []interface{}{
				map[string]interface{}{
					"headers": []interface{}{
						map[string]interface{}{
							"type":  "Exact",
							"name":  "x-ai-eg-model",
							"value": alias,
						},
					},
				},
			},
			"backendRefs": []interface{}{
				map[string]interface{}{
					"group": "inference.networking.k8s.io",
					"kind":  "InferencePool",
					"name":  poolName,
				},
			},
			"timeouts": map[string]interface{}{
				"request": "300s",
			},
		}
		rules = append(rules, rule)
		changed = true
	}

	if !changed {
		return nil
	}

	if err := unstructured.SetNestedSlice(route.Object, rules, "spec", "rules"); err != nil {
		return err
	}
	return r.Update(ctx, route)
}

// reconcileDelete removes the AIGatewayRoute rules and removes the finalizer.
// The Deployment, Service, and InferencePool are deleted automatically via owner references.
func (r *ModelDeploymentReconciler) reconcileDelete(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) (ctrl.Result, error) {
	if err := r.removeAIGatewayRouteRules(ctx, md); err != nil {
		return ctrl.Result{}, err
	}

	controllerutil.RemoveFinalizer(md, finalizerName)
	return ctrl.Result{}, r.Update(ctx, md)
}

// removeAIGatewayRouteRules removes all routing rules for this ModelDeployment's aliases.
func (r *ModelDeploymentReconciler) removeAIGatewayRouteRules(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) error {
	route := &unstructured.Unstructured{}
	route.SetGroupVersionKind(aigwRouteGVK)
	if err := r.Get(ctx, types.NamespacedName{Name: aigwRouteName, Namespace: aigwRouteNamespace}, route); err != nil {
		return client.IgnoreNotFound(err)
	}

	aliasSet := make(map[string]bool, len(md.Spec.Routing.ModelAliases))
	for _, a := range md.Spec.Routing.ModelAliases {
		aliasSet[a] = true
	}

	rules, _, _ := unstructured.NestedSlice(route.Object, "spec", "rules")
	filtered := make([]interface{}, 0, len(rules))
	for _, rule := range rules {
		rm, ok := rule.(map[string]interface{})
		if !ok {
			filtered = append(filtered, rule)
			continue
		}
		matches, _, _ := unstructured.NestedSlice(rm, "matches")
		keep := true
		for _, match := range matches {
			mm, ok := match.(map[string]interface{})
			if !ok {
				continue
			}
			headers, _, _ := unstructured.NestedSlice(mm, "headers")
			for _, header := range headers {
				hm, ok := header.(map[string]interface{})
				if !ok {
					continue
				}
				if hm["name"] == "x-ai-eg-model" && aliasSet[fmt.Sprint(hm["value"])] {
					keep = false
				}
			}
		}
		if keep {
			filtered = append(filtered, rule)
		}
	}

	if len(filtered) == len(rules) {
		return nil
	}
	if err := unstructured.SetNestedSlice(route.Object, filtered, "spec", "rules"); err != nil {
		return err
	}
	return r.Update(ctx, route)
}

// updateStatus reflects vLLM pod readiness in ModelDeployment.status.
func (r *ModelDeploymentReconciler) updateStatus(ctx context.Context, md *tokenlabsv1alpha1.ModelDeployment) error {
	dep := &appsv1.Deployment{}
	if err := r.Get(ctx, types.NamespacedName{Name: DeploymentName(md.Name), Namespace: md.Namespace}, dep); err != nil {
		return client.IgnoreNotFound(err)
	}

	phase := tokenlabsv1alpha1.ModelDeploymentPhaseDeploying
	if dep.Status.ReadyReplicas >= md.Spec.Replicas && md.Spec.Replicas > 0 {
		phase = tokenlabsv1alpha1.ModelDeploymentPhaseRunning
	} else if dep.Status.UnavailableReplicas > 0 && dep.Status.ReadyReplicas == 0 {
		phase = tokenlabsv1alpha1.ModelDeploymentPhaseFailed
	}

	ready := metav1.ConditionFalse
	readyMsg := fmt.Sprintf("%d/%d replicas ready", dep.Status.ReadyReplicas, md.Spec.Replicas)
	if phase == tokenlabsv1alpha1.ModelDeploymentPhaseRunning {
		ready = metav1.ConditionTrue
		readyMsg = "All replicas are ready"
	}

	condition := metav1.Condition{
		Type:               conditionTypeReady,
		Status:             ready,
		ObservedGeneration: md.Generation,
		LastTransitionTime: metav1.Now(),
		Reason:             string(phase),
		Message:            readyMsg,
	}

	md.Status.Phase = phase
	md.Status.InferencepoolRef = InferencePoolName(md.Name)
	md.Status.Conditions = SetCondition(md.Status.Conditions, condition)

	return r.Status().Update(ctx, md)
}

// BuildDeployment constructs the vLLM Deployment for a ModelDeployment.
func (r *ModelDeploymentReconciler) BuildDeployment(md *tokenlabsv1alpha1.ModelDeployment) *appsv1.Deployment {
	labels := PodLabels(md)
	replicas := md.Spec.Replicas

	args := []string{
		fmt.Sprintf("--model=%s", md.Spec.ModelID),
	}
	if md.Spec.Resources.GPUMemoryFraction != "" {
		args = append(args, fmt.Sprintf("--gpu-memory-utilization=%s", md.Spec.Resources.GPUMemoryFraction))
	}
	if md.Spec.Quantization != "" && md.Spec.Quantization != "bfloat16" {
		args = append(args, fmt.Sprintf("--quantization=%s", md.Spec.Quantization))
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      DeploymentName(md.Name),
			Namespace: md.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Strategy: appsv1.DeploymentStrategy{
				Type: appsv1.RollingUpdateDeploymentStrategyType,
				RollingUpdate: &appsv1.RollingUpdateDeployment{
					MaxUnavailable: intOrStrPtr(1),
					MaxSurge:       intOrStrPtr(0),
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					RuntimeClassName: strPtr("nvidia"),
					NodeSelector:     md.Spec.NodeSelector,
					Tolerations: []corev1.Toleration{
						{Key: "nvidia.com/gpu", Operator: corev1.TolerationOpExists, Effect: corev1.TaintEffectNoSchedule},
					},
					Containers: []corev1.Container{
						{
							Name:    "vllm",
							Image:   vllmImage,
							Command: []string{"python", "-m", "vllm.entrypoints.openai.api_server"},
							Args:    args,
							Ports: []corev1.ContainerPort{
								{Name: "http", ContainerPort: vllmPort, Protocol: corev1.ProtocolTCP},
							},
							Env: []corev1.EnvVar{
								{Name: "FLASHINFER_DISABLE_VERSION_CHECK", Value: "1"},
							},
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("4"),
									corev1.ResourceMemory: resource.MustParse("32Gi"),
									"nvidia.com/gpu":      resource.MustParse("1"),
								},
								Limits: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("8"),
									corev1.ResourceMemory: resource.MustParse("64Gi"),
									"nvidia.com/gpu":      resource.MustParse("1"),
								},
							},
							StartupProbe: &corev1.Probe{
								ProbeHandler:        corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/v1/models", Port: intOrStr(vllmPort)}},
								InitialDelaySeconds: 30,
								PeriodSeconds:       10,
								FailureThreshold:    60,
								TimeoutSeconds:      10,
							},
							ReadinessProbe: &corev1.Probe{
								ProbeHandler:        corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/v1/models", Port: intOrStr(vllmPort)}},
								InitialDelaySeconds: 5,
								PeriodSeconds:       15,
								FailureThreshold:    3,
								TimeoutSeconds:      5,
							},
							LivenessProbe: &corev1.Probe{
								ProbeHandler:     corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/v1/models", Port: intOrStr(vllmPort)}},
								PeriodSeconds:    30,
								FailureThreshold: 5,
								TimeoutSeconds:   10,
							},
						},
					},
				},
			},
		},
	}
}

// buildService constructs the Service that exposes the vLLM pods.
func (r *ModelDeploymentReconciler) buildService(md *tokenlabsv1alpha1.ModelDeployment) *corev1.Service {
	labels := PodLabels(md)
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      DeploymentName(md.Name),
			Namespace: md.Namespace,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			Selector: labels,
			Ports: []corev1.ServicePort{
				{Name: "http", Port: vllmPort, Protocol: corev1.ProtocolTCP},
			},
		},
	}
}

// BuildInferencePool constructs the InferencePool unstructured object for the ModelDeployment.
func (r *ModelDeploymentReconciler) BuildInferencePool(md *tokenlabsv1alpha1.ModelDeployment) *unstructured.Unstructured {
	labels := PodLabels(md)
	selectorMap := make(map[string]interface{}, len(labels))
	for k, v := range labels {
		selectorMap[k] = v
	}

	pool := &unstructured.Unstructured{
		Object: map[string]interface{}{
			"apiVersion": "inference.networking.k8s.io/v1alpha2",
			"kind":       "InferencePool",
			"metadata": map[string]interface{}{
				"name":      InferencePoolName(md.Name),
				"namespace": md.Namespace,
				"labels":    selectorMap,
			},
			"spec": map[string]interface{}{
				"targetPortNumber": int64(vllmPort),
				"selector":         selectorMap,
				"extensionRef": map[string]interface{}{
					"name": InferencePoolName(md.Name) + "-epp",
				},
			},
		},
	}
	return pool
}

// SetupWithManager registers the reconciler with the controller manager.
func (r *ModelDeploymentReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&tokenlabsv1alpha1.ModelDeployment{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Service{}).
		Complete(r)
}

// InferencePoolName returns the name of the InferencePool for a ModelDeployment.
func InferencePoolName(mdName string) string {
	return "llm-d-inferencepool-" + mdName
}

// DeploymentName returns the name of the Deployment for a ModelDeployment.
func DeploymentName(mdName string) string {
	return "modeldeployment-" + mdName
}

// PodLabels returns the standard labels applied to pods created for a ModelDeployment.
func PodLabels(md *tokenlabsv1alpha1.ModelDeployment) map[string]string {
	// Use a sanitised model slug for the llm-d label.
	slug := strings.NewReplacer("/", "-", ".", "-", "_", "-").Replace(md.Name)
	return map[string]string{
		"llm-d.ai/inference-serving":  "true",
		"llm-d.ai/model":              slug,
		"app.kubernetes.io/name":      "modeldeployment",
		"app.kubernetes.io/instance":  md.Name,
		"app.kubernetes.io/component": "vllm",
	}
}

// SetCondition upserts cond into conditions, returning the updated slice.
func SetCondition(conditions []metav1.Condition, cond metav1.Condition) []metav1.Condition {
	for i, c := range conditions {
		if c.Type == cond.Type {
			if c.Status != cond.Status {
				conditions[i] = cond
			} else {
				// Preserve the original transition time.
				cond.LastTransitionTime = c.LastTransitionTime
				conditions[i] = cond
			}
			return conditions
		}
	}
	return append(conditions, cond)
}

// intOrStrPtr returns an IntOrString pointer for rolling update config.
func intOrStrPtr(i int) *intstr.IntOrString {
	v := intstr.FromInt(i)
	return &v
}

// intOrStr returns an IntOrString for probe port config.
func intOrStr(i int) intstr.IntOrString {
	return intstr.FromInt(i)
}

// strPtr returns a pointer to a string.
func strPtr(s string) *string { return &s }
