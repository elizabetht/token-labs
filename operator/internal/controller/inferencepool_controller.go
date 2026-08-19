// Package controller implements the InferencePool controller.
package controller

import (
	"context"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"

	inferencev1alpha1 "github.com/elizabetht/token-labs/operator/api/v1alpha1"
)

const (
	// vllmPort is the port vLLM's HTTP server listens on.
	vllmPort = 8000
	// vllmImage is the default container image used to run vLLM workers.
	vllmImage = "ghcr.io/elizabetht/token-labs/vllm-serve:v0.4.0"
	// poolFinalizer is the finalizer added to InferencePool objects.
	poolFinalizer = "inference.tokenlabs.run/finalizer"
)

// InferencePoolReconciler reconciles an InferencePool object.
type InferencePoolReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=inference.tokenlabs.run,resources=inferencepools,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=inference.tokenlabs.run,resources=inferencepools/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=inference.tokenlabs.run,resources=inferencepools/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=services,verbs=get;list;watch;create;update;patch;delete

// Reconcile reads the desired state of the InferencePool CR and reconciles the
// cluster state to match. It manages a vLLM Deployment and Service as child
// resources, maintaining OwnerReferences for automatic garbage collection.
func (r *InferencePoolReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Fetch the InferencePool CR.
	pool := &inferencev1alpha1.InferencePool{}
	if err := r.Get(ctx, req.NamespacedName, pool); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, fmt.Errorf("get InferencePool: %w", err)
	}

	// Add finalizer on first reconcile.
	if !controllerutil.ContainsFinalizer(pool, poolFinalizer) {
		controllerutil.AddFinalizer(pool, poolFinalizer)
		if err := r.Update(ctx, pool); err != nil {
			return ctrl.Result{}, fmt.Errorf("add finalizer: %w", err)
		}
		return ctrl.Result{}, nil
	}

	// Handle deletion.
	if !pool.DeletionTimestamp.IsZero() {
		controllerutil.RemoveFinalizer(pool, poolFinalizer)
		if err := r.Update(ctx, pool); err != nil {
			return ctrl.Result{}, fmt.Errorf("remove finalizer: %w", err)
		}
		return ctrl.Result{}, nil
	}

	// Reconcile child Deployment and Service.
	deploy, err := r.reconcileDeployment(ctx, pool)
	if err != nil {
		return ctrl.Result{}, fmt.Errorf("reconcile Deployment: %w", err)
	}

	if err := r.reconcileService(ctx, pool); err != nil {
		return ctrl.Result{}, fmt.Errorf("reconcile Service: %w", err)
	}

	// Update status based on the Deployment's ready replica count.
	if err := r.updateStatus(ctx, pool, deploy); err != nil {
		return ctrl.Result{}, fmt.Errorf("update status: %w", err)
	}

	logger.Info("reconciled InferencePool",
		"name", pool.Name,
		"replicas", pool.Spec.Resources.Replicas,
		"readyReplicas", deploy.Status.ReadyReplicas)

	return ctrl.Result{}, nil
}

// reconcileDeployment ensures a vLLM Deployment exists and matches the desired state.
// It returns the current Deployment so the caller can inspect its status.
func (r *InferencePoolReconciler) reconcileDeployment(ctx context.Context, pool *inferencev1alpha1.InferencePool) (*appsv1.Deployment, error) {
	desired := r.buildDeployment(pool)

	// Attempt to create or fetch the existing deployment.
	existing := &appsv1.Deployment{}
	err := r.Get(ctx, client.ObjectKeyFromObject(desired), existing)
	if apierrors.IsNotFound(err) {
		if err := r.Create(ctx, desired); err != nil {
			return nil, fmt.Errorf("create Deployment: %w", err)
		}
		return desired, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get Deployment: %w", err)
	}

	// Sync replicas if they diverge.
	if !equality.Semantic.DeepEqual(existing.Spec.Replicas, desired.Spec.Replicas) {
		existing.Spec.Replicas = desired.Spec.Replicas
		if err := r.Update(ctx, existing); err != nil {
			return nil, fmt.Errorf("update Deployment replicas: %w", err)
		}
	}

	return existing, nil
}

// reconcileService ensures a headless Service exists for the vLLM pods.
func (r *InferencePoolReconciler) reconcileService(ctx context.Context, pool *inferencev1alpha1.InferencePool) error {
	desired := r.buildService(pool)

	existing := &corev1.Service{}
	err := r.Get(ctx, client.ObjectKeyFromObject(desired), existing)
	if apierrors.IsNotFound(err) {
		return r.Create(ctx, desired)
	}
	return err
}

// updateStatus patches the InferencePool status to reflect the current Deployment state.
func (r *InferencePoolReconciler) updateStatus(ctx context.Context, pool *inferencev1alpha1.InferencePool, deploy *appsv1.Deployment) error {
	readyReplicas := deploy.Status.ReadyReplicas
	isReady := readyReplicas >= pool.Spec.Resources.Replicas

	deployAvailable := metav1.ConditionFalse
	if deploy.Status.AvailableReplicas > 0 {
		deployAvailable = metav1.ConditionTrue
	}

	readyStatus := metav1.ConditionFalse
	if isReady {
		readyStatus = metav1.ConditionTrue
	}

	now := metav1.Now()
	conditions := []metav1.Condition{
		{
			Type:               string(inferencev1alpha1.ConditionDeploymentAvailable),
			Status:             deployAvailable,
			ObservedGeneration: pool.Generation,
			LastTransitionTime: now,
			Reason:             "DeploymentCheck",
			Message:            fmt.Sprintf("%d/%d replicas available", deploy.Status.AvailableReplicas, pool.Spec.Resources.Replicas),
		},
		{
			Type:               string(inferencev1alpha1.ConditionReady),
			Status:             readyStatus,
			ObservedGeneration: pool.Generation,
			LastTransitionTime: now,
			Reason:             "ReadinessCheck",
			Message:            fmt.Sprintf("%d/%d replicas ready", readyReplicas, pool.Spec.Resources.Replicas),
		},
	}

	// Only patch if something changed.
	patch := client.MergeFrom(pool.DeepCopy())
	pool.Status.Conditions = conditions
	pool.Status.ReadyReplicas = readyReplicas
	pool.Status.ObservedGeneration = pool.Generation

	return r.Status().Patch(ctx, pool, patch)
}

// buildDeployment constructs the desired Deployment for the given InferencePool.
func (r *InferencePoolReconciler) buildDeployment(pool *inferencev1alpha1.InferencePool) *appsv1.Deployment {
	labels := labelsForPool(pool.Name)
	replicas := pool.Spec.Resources.Replicas

	gpuLimit := resource.MustParse("1")
	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deploymentName(pool.Name),
			Namespace: pool.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name:  "vllm",
							Image: vllmImage,
							Ports: []corev1.ContainerPort{
								{Name: "http", ContainerPort: vllmPort, Protocol: corev1.ProtocolTCP},
							},
							Env: []corev1.EnvVar{
								{Name: "MODEL_NAME", Value: pool.Spec.Model.Repository},
								{Name: "QUANTIZATION", Value: pool.Spec.Model.Quantization},
							},
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("4"),
									corev1.ResourceMemory: resource.MustParse("32Gi"),
									"nvidia.com/gpu":      gpuLimit,
								},
								Limits: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("8"),
									corev1.ResourceMemory: resource.MustParse("64Gi"),
									"nvidia.com/gpu":      gpuLimit,
								},
							},
							// vLLM exposes /v1/models when the model is loaded and ready.
							StartupProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path: "/v1/models",
										Port: intstr.FromInt(vllmPort),
									},
								},
								InitialDelaySeconds: 30,
								PeriodSeconds:       10,
								FailureThreshold:    60,
								TimeoutSeconds:      10,
							},
							ReadinessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path: "/v1/models",
										Port: intstr.FromInt(vllmPort),
									},
								},
								PeriodSeconds:    15,
								FailureThreshold: 3,
								TimeoutSeconds:   5,
							},
							LivenessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path: "/health",
										Port: intstr.FromInt(vllmPort),
									},
								},
								PeriodSeconds:    30,
								FailureThreshold: 5,
								TimeoutSeconds:   10,
							},
						},
					},
					Tolerations: []corev1.Toleration{
						{Key: "nvidia.com/gpu", Operator: corev1.TolerationOpExists, Effect: corev1.TaintEffectNoSchedule},
					},
				},
			},
		},
	}

	// Pin the GPU node selector when gpuType is provided.
	if pool.Spec.Resources.GPUType != "" {
		deploy.Spec.Template.Spec.NodeSelector = map[string]string{
			"nvidia.com/gpu.product": pool.Spec.Resources.GPUType,
		}
	}

	// Set InferencePool as owner so child resources are garbage-collected.
	_ = controllerutil.SetControllerReference(pool, deploy, r.Scheme)
	return deploy
}

// buildService constructs the desired Service for the given InferencePool.
func (r *InferencePoolReconciler) buildService(pool *inferencev1alpha1.InferencePool) *corev1.Service {
	labels := labelsForPool(pool.Name)
	svc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deploymentName(pool.Name),
			Namespace: pool.Namespace,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			Selector: labels,
			Ports: []corev1.ServicePort{
				{
					Name:       "http",
					Port:       int32(vllmPort),
					TargetPort: intstr.FromInt(vllmPort),
					Protocol:   corev1.ProtocolTCP,
				},
			},
		},
	}
	_ = controllerutil.SetControllerReference(pool, svc, r.Scheme)
	return svc
}

// SetupWithManager registers the controller with the manager.
func (r *InferencePoolReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&inferencev1alpha1.InferencePool{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Service{}).
		Complete(r)
}

// labelsForPool returns a standard label set for child resources of an InferencePool.
func labelsForPool(poolName string) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":       "inferencepool",
		"app.kubernetes.io/instance":   poolName,
		"app.kubernetes.io/managed-by": "inferencepool-controller",
	}
}

// deploymentName derives the child Deployment/Service name from the pool name.
func deploymentName(poolName string) string {
	return poolName + "-vllm"
}
