@protocol UIApplicationDelegate
- (int)application:(id)application openURL:(id)url options:(id)options;
@end
@protocol Runnable
- (void)run;
@end

@interface App <UIApplicationDelegate, Runnable>
@property(nonatomic, copy) void (^completion)(int);
- (void)run;
@end

@interface App (Extras)
- (void)extra;
@end
