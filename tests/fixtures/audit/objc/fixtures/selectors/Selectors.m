@interface Worker
+ (void)touch;
- (void)touch;
- (void)perform:(int)value other:(int)second;
- (void)run;
@end

static void class_sink(void) {}
static void instance_sink(void) {}
static void perform_sink(void) {}

@implementation Worker
+ (void)touch { class_sink(); }
- (void)touch { instance_sink(); }
- (void)perform:(int)value other:(int)second {
    if (value > second) { perform_sink(); }
}
- (void)run {
    [self touch];
    [Worker touch];
    [self perform:1 other:2];
}
@end
