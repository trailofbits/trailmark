#ifndef API_H
#define API_H
struct Record;
typedef struct Record Record;
struct Record { int field; };
enum Mode { MODE_A, MODE_B };
int helper(int);
__attribute__((visibility("default"))) int exported(int);
#endif
