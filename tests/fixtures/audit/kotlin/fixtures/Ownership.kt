package audit

fun hiddenSink() {}
fun caller() {
    fun neverCalled() {
        if (true) hiddenSink()
    }
    val deferred = { hiddenSink() }
}

class Outer {
    class Nested {
        fun nestedMethod() = hiddenSink()
    }
    companion object {
        @JvmStatic
        fun main(args: Array<String>) = hiddenSink()
        fun factory() = Outer()
    }
}
object Worker {
    fun run() = hiddenSink()
}
