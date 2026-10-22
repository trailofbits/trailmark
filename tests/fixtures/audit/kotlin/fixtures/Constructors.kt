package audit

fun constructorSink() {}
class Config(val key: String) {
    init { constructorSink() }
    constructor() : this("default") { constructorSink() }
    val callback = { constructorSink() }
    fun method(block: (String) -> Unit = {}) { block(key) }
}
fun main() { Config() }
