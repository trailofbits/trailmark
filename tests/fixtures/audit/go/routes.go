package probe

import "net/http"

func registerOtherFile() { http.HandleFunc("/cross-file", crossFileHandler) }
