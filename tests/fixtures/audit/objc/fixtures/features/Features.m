#import "Features.h"
static void feature_sink(void) {}

@implementation App
- (void)run {
    @autoreleasepool { [self extra]; }
}
- (int)application:(id)application openURL:(id)url options:(id)options {
    return 1;
}
@end

@implementation App (Extras)
- (void)extra { if (1) { feature_sink(); } }
@end

int main(void) { return 0; }
