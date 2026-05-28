// Comprehensive Java feature taxonomy fixture.

package taxonomy;

import java.util.List;

public class Taxonomy {

    public static final int CONSTANT = 42;

    public interface Greeter {
        String greet(String name);
    }

    public enum Status {
        PENDING, DONE
    }

    public static int add(int a, int b) {
        return a + b;
    }

    public static int branchy(int value, String mode) {
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
            return Integer.parseInt(mode) + total;
        } catch (NumberFormatException e) {
            throw new RuntimeException("bad mode");
        }
    }

    public static class Animal {
        protected String name;
        protected String species = "unknown";

        public Animal(String name) {
            this.name = name;
        }

        public String describe() {
            return name + " the " + species;
        }
    }

    public static class Dog extends Animal implements Greeter {
        private String breed;

        public Dog(String name, String breed) {
            super(name);
            this.species = "dog";
            this.breed = breed;
        }

        public String bark(boolean loud) {
            if (loud) {
                throw new RuntimeException("too loud");
            }
            return name + ": woof";
        }

        @Override
        public String greet(String other) {
            return name + " greets " + other;
        }
    }

    public static String useAnimal(Dog d) {
        return d.bark(false);
    }
}
