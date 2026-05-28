// Comprehensive Go feature taxonomy fixture.
// Exercises funcs, methods, structs, interfaces, branches, panics, and calls.

package taxonomy

import (
	"errors"
	"fmt"
	"strconv"
)

const Constant = 42

type Greeter interface {
	Greet(name string) string
}

type Status int

const (
	StatusPending Status = iota
	StatusDone
)

func Add(a, b int) int {
	return a + b
}

func Branchy(value int, mode string) (int, error) {
	total := 0
	if value > 0 {
		total += value
	} else if value < 0 {
		total -= value
	}
	for i := 0; i < value; i++ {
		if i%2 == 0 {
			total += i
		}
	}
	for total > 100 {
		total /= 2
	}
	parsed, err := strconv.Atoi(mode)
	if err != nil {
		return 0, errors.New("bad mode")
	}
	return parsed + total, nil
}

type Animal struct {
	Name    string
	Species string
}

func NewAnimal(name string) *Animal {
	return &Animal{Name: name, Species: "unknown"}
}

func (a *Animal) Describe() string {
	return fmt.Sprintf("%s the %s", a.Name, a.Species)
}

type Dog struct {
	*Animal
	Breed string
}

func NewDog(name, breed string) *Dog {
	base := NewAnimal(name)
	base.Species = "dog"
	return &Dog{Animal: base, Breed: breed}
}

func (d *Dog) Bark(loud bool) (string, error) {
	if loud {
		return "", errors.New("too loud")
	}
	return fmt.Sprintf("%s: woof", d.Name), nil
}

func (d *Dog) Greet(name string) string {
	return fmt.Sprintf("%s greets %s", d.Name, name)
}

func UseAnimal(d *Dog) (string, error) {
	return d.Bark(false)
}
