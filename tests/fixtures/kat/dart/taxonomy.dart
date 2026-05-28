// Comprehensive Dart feature taxonomy fixture.

const int kConstant = 42;

abstract class Greeter {
  String greet(String name);
}

enum Status { pending, done }

int add(int a, int b) => a + b;

int branchy(int value, String mode) {
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
    total ~/= 2;
  }
  try {
    return int.parse(mode) + total;
  } on FormatException {
    throw StateError("bad mode");
  }
}

Future<String> fetchAsync(String url) async {
  return url;
}

class Animal {
  String name;
  String species = "unknown";

  Animal(this.name);

  String describe() => "$name the $species";
}

class Dog extends Animal implements Greeter {
  String? breed;

  Dog(String name, [this.breed]) : super(name) {
    species = "dog";
  }

  String bark({bool loud = false}) {
    if (loud) {
      throw StateError("too loud");
    }
    return "$name: woof";
  }

  @override
  String greet(String other) => "$name greets $other";
}

String useAnimal(Dog d) => d.bark(loud: false);
