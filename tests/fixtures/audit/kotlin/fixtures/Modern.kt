package audit

data class User(val id: Long, val displayName: String?)
interface Repository {
    suspend fun find(id: Long): User?
}
fun <T> echo(value: T, fallback: T? = null): T = value
fun String.extension(): String = trim()
fun Int.extension(): String = toString()
fun typedCallback(block: (String) -> Unit, count: Int = 1) {
    if (count > 0) block("ok")
}
