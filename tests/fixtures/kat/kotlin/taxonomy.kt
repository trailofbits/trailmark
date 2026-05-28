// Comprehensive Kotlin feature taxonomy fixture.

package taxonomy

const val CONSTANT: Int = 42

interface Greeter {
    fun greet(name: String): String
}

enum class Status {
    PENDING, DONE
}

fun add(a: Int, b: Int): Int {
    return a + b
}

fun branchy(value: Int, mode: String): Int {
    var total = 0
    if (value > 0) {
        total += value
    } else if (value < 0) {
        total -= value
    }
    for (i in 0 until value) {
        if (i % 2 == 0) {
            total += i
        }
    }
    while (total > 100) {
        total /= 2
    }
    return try {
        mode.toInt() + total
    } catch (e: NumberFormatException) {
        throw RuntimeException("bad mode")
    }
}

suspend fun fetchAsync(url: String): String {
    return url
}

open class Animal(val name: String) {
    open var species: String = "unknown"

    fun describe(): String {
        return "$name the $species"
    }
}

class Dog(name: String, val breed: String? = null) : Animal(name), Greeter {
    override var species: String = "dog"

    fun bark(loud: Boolean = false): String {
        if (loud) {
            throw RuntimeException("too loud")
        }
        return "$name: woof"
    }

    override fun greet(name: String): String {
        return "${this.name} greets $name"
    }
}

fun useAnimal(d: Dog): String {
    return d.bark(false)
}
