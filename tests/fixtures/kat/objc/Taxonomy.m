// Comprehensive Objective-C feature taxonomy fixture.

#import <Foundation/Foundation.h>

static const NSInteger kTaxonomyConstant = 42;

@protocol Greeter <NSObject>
- (NSString *)greet:(NSString *)name;
@end

@interface Animal : NSObject
@property (nonatomic, copy) NSString *name;
@property (nonatomic, copy) NSString *species;
- (instancetype)initWithName:(NSString *)name;
- (NSString *)describe;
@end

@implementation Animal
- (instancetype)initWithName:(NSString *)name {
    self = [super init];
    if (self) {
        _name = [name copy];
        _species = @"unknown";
    }
    return self;
}

- (NSString *)describe {
    return [NSString stringWithFormat:@"%@ the %@", self.name, self.species];
}
@end

@interface Dog : Animal <Greeter>
@property (nonatomic, copy) NSString *breed;
- (instancetype)initWithName:(NSString *)name breed:(NSString *)breed;
- (NSString *)bark:(BOOL)loud error:(NSError **)error;
@end

@implementation Dog
- (instancetype)initWithName:(NSString *)name breed:(NSString *)breed {
    self = [super initWithName:name];
    if (self) {
        self.species = @"dog";
        _breed = [breed copy];
    }
    return self;
}

- (NSString *)bark:(BOOL)loud error:(NSError **)error {
    if (loud) {
        if (error) {
            *error = [NSError errorWithDomain:@"taxonomy" code:1 userInfo:nil];
        }
        return nil;
    }
    return [NSString stringWithFormat:@"%@: woof", self.name];
}

- (NSString *)greet:(NSString *)name {
    return [NSString stringWithFormat:@"%@ greets %@", self.name, name];
}
@end

NSInteger add(NSInteger a, NSInteger b) {
    return a + b;
}

NSInteger branchy(NSInteger value, NSString *mode) {
    NSInteger total = 0;
    if (value > 0) {
        total += value;
    } else if (value < 0) {
        total -= value;
    }
    for (NSInteger i = 0; i < value; i++) {
        if (i % 2 == 0) {
            total += i;
        }
    }
    while (total > 100) {
        total /= 2;
    }
    NSInteger parsed = [mode integerValue];
    return parsed + total;
}

NSString *use_animal(Dog *d) {
    NSError *err = nil;
    return [d bark:NO error:&err];
}
