// Comprehensive C++ feature taxonomy fixture.
// Exercises functions, classes, inheritance, templates, branches, throws,
// and call edges.

#include <stdexcept>
#include <string>

constexpr int CONSTANT = 42;

template <typename A, typename B>
struct Pair {
    A first;
    B second;
};

class Greeter {
public:
    virtual ~Greeter() = default;
    virtual std::string greet(const std::string &name) const = 0;
};

int add(int a, int b) {
    return a + b;
}

int branchy(int value, const std::string &mode) {
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
    try {
        return std::stoi(mode) + total;
    } catch (const std::invalid_argument &) {
        throw std::runtime_error("bad mode");
    }
}

class Animal {
protected:
    std::string name_;
    std::string species_;

public:
    Animal(std::string name) : name_(std::move(name)), species_("unknown") {}
    virtual ~Animal() = default;

    std::string describe() const {
        return name_ + " the " + species_;
    }
};

class Dog : public Animal, public Greeter {
private:
    std::string breed_;

public:
    Dog(std::string name, std::string breed)
        : Animal(std::move(name)), breed_(std::move(breed)) {
        species_ = "dog";
    }

    std::string bark(bool loud) const {
        if (loud) {
            throw std::runtime_error("too loud");
        }
        return name_ + ": woof";
    }

    std::string greet(const std::string &name) const override {
        return name_ + " greets " + name;
    }
};

std::string use_animal(const Dog &d) {
    return d.bark(false);
}
