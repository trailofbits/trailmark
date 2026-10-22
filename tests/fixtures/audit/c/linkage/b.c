int via_parameter(int (*hidden)(void)) { return hidden(); }
int external_callback(void);
int visible(void) { return external_callback(); }
