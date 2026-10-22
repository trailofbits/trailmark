typedef void (^Completion)(int);
static void callback_sink(void) {}

void build_callback(void) {
    Completion later = ^(int code) {
        if (code) { callback_sink(); }
    };
    (void)later;
}

void invoke_callback(Completion callback) { callback(1); }
