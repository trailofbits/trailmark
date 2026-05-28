/* Comprehensive C feature taxonomy fixture.
 * Exercises functions, structs, branches, returns, and calls. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CONSTANT 42

struct Animal {
    char name[64];
    char species[32];
};

struct Dog {
    struct Animal base;
    char breed[32];
};

int add(int a, int b) {
    return a + b;
}

int branchy(int value, const char *mode) {
    int total = 0;
    if (value > 0) {
        total += value;
    } else if (value < 0) {
        total -= value;
    }
    for (int i = 0; i < value; i++) {
        if (i % 2 == 0) {
            total += i;
        }
    }
    while (total > 100) {
        total /= 2;
    }
    char *end;
    long parsed = strtol(mode, &end, 10);
    if (end == mode) {
        return -1;
    }
    return (int)parsed + total;
}

void animal_init(struct Animal *a, const char *name) {
    strncpy(a->name, name, sizeof(a->name) - 1);
    a->name[sizeof(a->name) - 1] = '\0';
    strncpy(a->species, "unknown", sizeof(a->species) - 1);
}

void animal_describe(const struct Animal *a, char *out, size_t out_len) {
    snprintf(out, out_len, "%s the %s", a->name, a->species);
}

void dog_init(struct Dog *d, const char *name, const char *breed) {
    animal_init(&d->base, name);
    strncpy(d->base.species, "dog", sizeof(d->base.species) - 1);
    strncpy(d->breed, breed, sizeof(d->breed) - 1);
}

int dog_bark(const struct Dog *d, int loud, char *out, size_t out_len) {
    if (loud) {
        return -1;
    }
    snprintf(out, out_len, "%s: woof", d->base.name);
    return 0;
}

int use_animal(const struct Dog *d, char *out, size_t out_len) {
    return dog_bark(d, 0, out, out_len);
}
