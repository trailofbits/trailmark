package probe

import stdstrings "strings"

func sensitive() {}
func benign() {}

func shadowed(sensitive func()) {
	sensitive()
}

func localShadow() {
	sensitive := benign
	sensitive()
}

func invokeOther(localOnly func()) {
	localOnly()
}

func closureFactory() func() {
	return func() {
		if true {
			sensitive()
		}
	}
}

func grouped(a, b string, c, d int, cb func(int) string, ch <-chan int, rest ...string) (n int, err error) {
	return
}

func unnamed(string, ...int) {}

func identity[T any](value T) T { return value }
func genericCaller() { _ = identity[int](1) }

type Box[T any] struct{ value T }
func (b *Box[T]) Get() T { return b.value }

type Worker struct{}
func (w *Worker) Run() {}
func (w *Worker) Cycle() { w.Run() }

type Runner interface { Run() }
func viaInterface(r Runner) { r.Run() }

func deferredAndSpawned() {
	defer benign()
	go benign()
}

func useImportedAlias(s string) { _ = stdstrings.TrimSpace(s) }

func inferredCaller() { _ = identity(1) }
func pair[A, B any](a A, b B) A { return a }
func genericTwoCaller() { _ = pair[int, string](1, "x") }
type Slice[T any] []T
func conversionCaller() { _ = Slice[int]([]int{1}) }
