// Package v1alpha1 contains API Schema definitions for the tokenlabs.io v1alpha1 API group.
// +groupName=tokenlabs.io
package v1alpha1

import (
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

var (
	// GroupVersion is group version used to register these objects.
	GroupVersion = schema.GroupVersion{Group: "tokenlabs.io", Version: "v1alpha1"}

	// SchemeBuilder is used to add functions to this group's scheme.
	SchemeBuilder = &scheme.Builder{GroupVersion: GroupVersion}

	// AddToScheme adds the types in this group-version to the given scheme.
	AddToScheme = SchemeBuilder.AddToScheme
)
