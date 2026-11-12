exception Bad { 1: string message }
service App { void f() throws (1: Bad bad) }
