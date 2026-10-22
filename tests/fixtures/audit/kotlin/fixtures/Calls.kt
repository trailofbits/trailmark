package audit

fun target(): String = "global"
fun invokeParameter(target: () -> String): String = target()
fun invokeLocal(): String {
    val target = { "local" }
    return target()
}

fun sink(value: String) = println(value)
fun String.clean(): String = trim()
fun expressionBody(value: String): String = value.clean()
fun safeCall(value: String?) { value?.clean() }
fun higherOrder() {
    listOf("x").forEach { sink(it) }
}
