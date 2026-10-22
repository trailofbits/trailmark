package probe

import "net/http"

func directHandler(w http.ResponseWriter, r *http.Request) {}
func multilineHandler(w http.ResponseWriter, r *http.Request) {}
func muxHandler(w http.ResponseWriter, r *http.Request) {}
func crossFileHandler(w http.ResponseWriter, r *http.Request) {}

func registerHandlers() {
	http.HandleFunc("/direct", directHandler)
	http.HandleFunc(
		"/multiline",
		multilineHandler,
	)
	mux := http.NewServeMux()
	mux.HandleFunc("/mux", muxHandler)
}
