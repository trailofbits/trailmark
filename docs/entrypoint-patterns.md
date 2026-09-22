# Entrypoint Detection Patterns

Reference for implementing framework-aware entrypoint detectors in Trailmark. Each entry specifies the syntactic marker, scope (function/class/module), Trailmark enum defaults, a grep-ready pattern, and known gotchas.

**Enum legend** (from `src/trailmark/models/annotations.py`):

- `EntrypointKind`: `user_input`, `api`, `database`, `file_system`, `third_party`
- `TrustLevel`: `untrusted_external`, `semi_trusted_external`, `trusted_internal`
- `AssetValue`: `high`, `medium`, `low`

**Detection strategy.** Most markers live in the lines immediately preceding a function definition (decorators, attributes, annotations) or on the signature line itself (visibility modifiers). Detectors should open the file at `CodeUnit.location.file_path`, inspect the span ending at `start_line`, and backtrack through contiguous decorator/attribute lines.

---

## Python

### Flask

`@app.route(path, methods=[...])` and shortcut decorators `@app.get/post/put/patch/delete`. The receiver is conventionally named `app` but may be a blueprint (`@bp.route(...)`).

```python
from flask import Flask
app = Flask(__name__)

@app.route("/login", methods=["POST"])
def login():
    ...
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*@\s*([A-Za-z_][\w.]*)\.(route|get|post|put|patch|delete|head|options|websocket)\b`
- **Gotcha:** receiver name is not always `app`; match the attribute suffix.
- **Source:** <https://flask.palletsprojects.com/en/latest/quickstart/#routing>

### FastAPI

`@app.get/post/put/delete/patch/head/options(...)` and `@router.<method>(...)` for `APIRouter` instances. `@app.websocket(...)` is also a handler.

```python
from fastapi import FastAPI
app = FastAPI()

@app.post("/auth")
async def auth(body: AuthRequest):
    ...
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*@\s*([A-Za-z_][\w.]*)\.(get|post|put|patch|delete|head|options|websocket|api_route)\b`
- **Gotcha:** collides with Flask syntactically; disambiguate by looking for `FastAPI()` or `APIRouter()` in the same file.
- **Source:** <https://fastapi.tiangolo.com/tutorial/first-steps/>

### Django views

Functions in `views.py` + class-based views subclassing `View`/`TemplateView`/`ListView`/`APIView`. URL wiring happens in `urls.py` via `path("...", view_fn)` / `re_path(...)`.

```python
# views.py
def profile(request):
    ...

class LoginView(APIView):
    def post(self, request):
        ...
```

- **Scope:** function or class method (`get`/`post`/`put`/`delete`/`patch` inside a view class)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** file path ends in `views.py` and (a) the function takes `request` as its first parameter, or (b) the class inherits from a Django view base class.
- **Gotcha:** functions outside `views.py` can also be views if referenced from `urls.py`; a complete detector walks `urls.py` `path()` calls and resolves the callable.
- **Source:** <https://docs.djangoproject.com/en/5.0/topics/http/views/>

### aiohttp

`@routes.get/post/...` where `routes = web.RouteTableDef()`, or classes subclassing `web.View` with `async def get(self)`.

```python
from aiohttp import web
routes = web.RouteTableDef()

@routes.get("/status")
async def status(request):
    ...
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*@\s*([A-Za-z_][\w.]*)\.(get|post|put|patch|delete|head|options|view)\b` in a file importing `aiohttp`.
- **Gotcha:** shadowed by Flask/FastAPI patterns; disambiguate by imports.
- **Source:** <https://docs.aiohttp.org/en/stable/web_quickstart.html>

### Starlette

`routes = [Route("/path", endpoint)]` list passed to `Starlette(routes=routes)`. Endpoints are functions, not decorated.

```python
from starlette.routing import Route
routes = [Route("/", homepage), Route("/user/{id}", user_detail)]
```

- **Scope:** function (referenced from `Route(...)` calls)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `Route\(\s*"[^"]+"\s*,\s*([A-Za-z_]\w*)\b` — extract handler name.
- **Gotcha:** no decorator marker; must parse the `Route(...)` call list.
- **Source:** <https://www.starlette.io/routing/>

### Celery

`@app.task` or `@celery.task` on functions. Tasks are entrypoints for queue-borne messages.

```python
from celery import Celery
app = Celery()

@app.task
def send_email(to, body):
    ...
```

- **Scope:** function
- **Kind:** `third_party` · **Trust:** `semi_trusted_external` (queue may be internal) · **Asset:** `medium`
- **Pattern:** `^\s*@\s*([A-Za-z_][\w.]*)\.task\b`
- **Source:** <https://docs.celeryq.dev/en/stable/userguide/tasks.html>

### AWS Lambda (Python)

Function named per the handler string (e.g., `lambda_handler` by convention, or whatever the deployment manifest specifies in `Runtime.handler`). Signature is `def handler(event, context)`.

```python
def lambda_handler(event, context):
    ...
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** function named `lambda_handler`, `handler`, or match a handler string from `serverless.yml` / `template.yaml` / `sam.yaml` (`handler: app.module.function_name`).
- **Gotcha:** handler name is configurable; heuristic matches common conventions.
- **Source:** <https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html>

### Click / Typer

`@click.command()`, `@click.group()`, `@<group>.command(...)` (Click) or `@app.command()` on a `typer.Typer()` instance (Typer).

```python
import click
@click.command()
@click.argument("name")
def greet(name):
    click.echo(f"Hello {name}")
```

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `medium`
- **Pattern:** `^\s*@\s*click\.(command|group)\b` or `^\s*@\s*([A-Za-z_][\w.]*)\.command\b` (Typer).
- **Source:** <https://click.palletsprojects.com/en/stable/>, <https://typer.tiangolo.com/>

### gRPC servicer

Class inheriting from `<Service>Servicer` (generated from `.proto`). Methods correspond to RPC methods.

```python
class GreeterServicer(greeter_pb2_grpc.GreeterServicer):
    def SayHello(self, request, context):
        ...
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** class with a base name ending in `Servicer`; each method named in PascalCase is an RPC handler.
- **Source:** <https://grpc.io/docs/languages/python/basics/>

### `if __name__ == "__main__"` block

Any function called from an `if __name__ == "__main__":` block is a script entrypoint.

- **Scope:** function (resolved via the call graph)
- **Kind:** `user_input` · **Trust:** `trusted_internal` · **Asset:** `low`
- **Pattern:** locate the `if __name__ == "__main__":` node; mark every function it transitively calls at depth 1.
- **Gotcha:** module-level side effects also run; detector should attribute them to the module node.

---

## JavaScript / TypeScript

### Express

`app.get/post/put/delete/patch/all/use("/path", handler)` — handler is typically the callback. `app` is any variable of `Express` type.

```js
const app = express();
app.post("/login", (req, res) => { ... });
```

- **Scope:** function (the handler callback or named function referenced)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\b([A-Za-z_$][\w$]*)\.(get|post|put|patch|delete|all|use|options|head)\s*\(\s*["'`]` — but this shadows many libraries; narrow by import or a recent `express()` call on the receiver.
- **Gotcha:** `app` can be any name (`router`, `r`, `srv`, etc.); fully robust detection requires type flow or at least tracking which identifiers were assigned from `express()`/`Router()`.
- **Source:** <https://expressjs.com/en/guide/routing.html>

### Koa

`router.get/post/...(path, handler)` on `@koa/router`. Middleware via `app.use(middleware)`.

```js
const router = new Router();
router.get("/users", async ctx => { ... });
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** same as Express shape; disambiguate by `koa` / `@koa/router` import.
- **Source:** <https://koajs.com/>

### Fastify

`fastify.get/post/...(path, handler)` or `fastify.route({ method, url, handler })`.

```js
fastify.get("/ping", async (request, reply) => "pong");
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** same shape as Express; disambiguate by import.
- **Source:** <https://fastify.dev/docs/latest/Reference/Routes/>

### Next.js (Pages API)

Any default export in `pages/api/**`. The export itself is the handler.

```ts
// pages/api/auth.ts
export default function handler(req, res) { ... }
```

- **Scope:** function / module (the default export)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** file path matches `pages/api/**/*.{js,jsx,ts,tsx}`; identify the default-exported callable.
- **Source:** <https://nextjs.org/docs/pages/building-your-application/routing/api-routes>

### Next.js (App Router)

Named exports `GET`/`POST`/`PUT`/`DELETE`/`PATCH`/`HEAD`/`OPTIONS` in `app/**/route.{ts,js}`.

```ts
// app/api/users/route.ts
export async function GET(request: Request) { ... }
export async function POST(request: Request) { ... }
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** file path matches `app/**/route.{ts,tsx,js,jsx}`; named-export functions matching HTTP verb names.
- **Source:** <https://nextjs.org/docs/app/building-your-application/routing/route-handlers>

### NestJS

Controllers decorated `@Controller()`; methods decorated `@Get()`, `@Post()`, `@Put()`, `@Delete()`, `@Patch()`, `@Options()`, `@Head()`, `@All()`.

```ts
@Controller("users")
export class UsersController {
  @Get(":id")
  findOne(@Param("id") id: string) { ... }
}
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** method decorators `^\s*@\s*(Get|Post|Put|Delete|Patch|Options|Head|All)\s*\(`.
- **Source:** <https://docs.nestjs.com/controllers>

### AWS Lambda (Node.js)

`exports.handler = async (event, context) => { ... }` or `export const handler = ...`.

```js
exports.handler = async (event) => { ... };
```

- **Scope:** function (exported under a handler name)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** assignment to `exports.handler`, `exports.lambdaHandler`, or a named export matching the manifest.
- **Source:** <https://docs.aws.amazon.com/lambda/latest/dg/nodejs-handler.html>

### Cloudflare Workers

`export default { async fetch(request, env, ctx) { ... } }` (module-format workers) or `addEventListener("fetch", ...)` (service-worker format).

```ts
export default {
  async fetch(request: Request): Promise<Response> { ... }
};
```

- **Scope:** method (`fetch`/`scheduled`/`queue`/`email`) on the default-exported object
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** default export with methods named `fetch`, `scheduled`, `queue`, `email`, `tail`, `trace`.
- **Source:** <https://developers.cloudflare.com/workers/runtime-apis/handlers/fetch/>

### Deno.serve / Bun.serve

`Deno.serve((req) => ...)`, `Bun.serve({ fetch(req) { ... } })`.

- **Scope:** function (the callback)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\bDeno\.serve\s*\(` or `\bBun\.serve\s*\(\s*\{`.
- **Source:** <https://docs.deno.com/runtime/manual/runtime/http_server_apis/>, <https://bun.sh/docs/api/http>

---

## PHP

### Laravel

Routes wired in `routes/web.php` / `routes/api.php` via `Route::get('/path', [Controller::class, 'method'])`. Controllers inherit from `App\Http\Controllers\Controller`.

```php
Route::get('/users/{id}', [UserController::class, 'show']);
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `Route::(get|post|put|patch|delete|any|match)\s*\(` to extract method; controllers in `app/Http/Controllers/**`.
- **Source:** <https://laravel.com/docs/routing>

### Symfony

Controller classes with `#[Route('/path', methods: ['GET'])]` attributes (PHP 8+) or annotations (`@Route(...)`) on older versions.

```php
#[Route('/products', methods: ['GET'])]
public function list(): Response { ... }
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*#\[\s*Route\s*\(` (attributes) or `^\s*\*\s*@Route\s*\(` (annotations).
- **Source:** <https://symfony.com/doc/current/routing.html>

### WordPress hooks

`add_action('hook', callback)` and `add_filter('hook', callback)` register callbacks. Callbacks invoked from user input (form submission, AJAX) are entrypoints.

```php
add_action('wp_ajax_my_handler', 'my_callback');
add_action('wp_ajax_nopriv_my_handler', 'my_callback');  // unauth
```

- **Scope:** function (the callback)
- **Kind:** `api` · **Trust:** `untrusted_external` (especially `nopriv_`) · **Asset:** `medium`
- **Pattern:** `add_(action|filter)\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]?([A-Za-z_]\w*)`; extract callback name.
- **Gotcha:** callbacks can be closures or methods; detector should handle `[new Foo(), 'method']` array syntax.
- **Source:** <https://developer.wordpress.org/plugins/hooks/>

### Direct superglobal access

Any function that reads `$_GET`, `$_POST`, `$_REQUEST`, `$_COOKIE`, `$_SERVER`, `$_FILES` is effectively an entrypoint for the data in those arrays.

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** function bodies containing `\$_(GET|POST|REQUEST|COOKIE|SERVER|FILES)\b`.
- **Gotcha:** this is a taint *source* more than an attack surface — the function being the entrypoint depends on where it's called from.
- **Source:** <https://www.php.net/manual/en/language.variables.superglobals.php>

---

## Ruby

### Rails controllers

Classes inheriting from `ApplicationController` (or `ActionController::Base`). Public instance methods are actions. Routing in `config/routes.rb` via `get '/path' => 'controller#action'` or resource macros.

```ruby
class UsersController < ApplicationController
  def show
    @user = User.find(params[:id])
  end
end
```

- **Scope:** class method (public instance methods on controllers)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** classes matching `class \w+Controller\s*<\s*(ApplicationController|ActionController::\w+)`.
- **Source:** <https://guides.rubyonrails.org/action_controller_overview.html>

### Sinatra

Block-form route DSL: `get '/path' do ... end`, `post '/path' do ... end`.

```ruby
get '/hello' do
  "Hello, #{params[:name]}"
end
```

- **Scope:** block (tree-sitter may represent this as an anonymous function)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*(get|post|put|patch|delete|options|head)\s+['"][^'"]+['"]\s+do\b`.
- **Gotcha:** collides with RSpec `get` test helpers; restrict to files that require Sinatra.
- **Source:** <http://sinatrarb.com/intro.html>

### Sidekiq worker

Classes including `Sidekiq::Worker` / `Sidekiq::Job`. The `perform` method is the entrypoint.

```ruby
class SendEmailJob
  include Sidekiq::Job
  def perform(user_id, subject)
    ...
  end
end
```

- **Scope:** the `perform` method
- **Kind:** `third_party` · **Trust:** `semi_trusted_external` · **Asset:** `medium`
- **Pattern:** class body contains `include\s+Sidekiq::(Worker|Job)`.
- **Source:** <https://github.com/sidekiq/sidekiq/wiki/Getting-Started>

### Rack middleware

Classes with `call(env)` method. Any Rack app's top-level `call` is the request entrypoint.

- **Scope:** `call` method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** class defines `def call(env)`.
- **Source:** <https://github.com/rack/rack/blob/main/SPEC.rdoc>

---

## C / C++

### `main`

`int main(void)`, `int main(int argc, char **argv)`, `int main(int argc, char **argv, char **envp)`.

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` (argv is attacker-controlled in SUID / world-callable binaries) · **Asset:** `high`
- **Pattern:** `^\s*int\s+main\s*\(` at file scope.
- **Source:** C11 §5.1.2.2.1

### `extern "C"` / exported library functions

C linkage functions declared `extern "C"` (C++) or not declared `static` (C) are external API surface of a shared library.

```c
__attribute__((visibility("default")))
int my_api_call(const char *input, size_t len);
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` (callers can be any linked binary) · **Asset:** `high`
- **Pattern:** function definitions not marked `static`, at file scope. Stronger signal: `__attribute__((visibility("default")))` or Windows `__declspec(dllexport)`.
- **Gotcha:** in C, the *absence* of `static` is the marker — this is an inversion of the usual "look for a keyword" pattern. Match every public-linkage function.
- **Source:** C11 §6.2.2

### IOCTL handlers (kernel)

Functions of shape `long my_ioctl(struct file *, unsigned int cmd, unsigned long arg)` registered in a `file_operations` struct.

- **Scope:** function (referenced from a `file_operations` initializer)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\.unlocked_ioctl\s*=\s*(\w+)` in a `file_operations` struct literal.
- **Source:** <https://www.kernel.org/doc/html/latest/filesystems/vfs.html>

### Syscall handlers

`SYSCALL_DEFINE<N>(name, ...)` macros in Linux kernel.

- **Pattern:** `^\s*SYSCALL_DEFINE\d\s*\(`.
- **Source:** <https://www.kernel.org/doc/html/latest/process/adding-syscalls.html>

---

## C#

### ASP.NET Core controllers

`[ApiController]` + `[Route(...)]` on classes; `[HttpGet]`, `[HttpPost]`, `[HttpPut]`, `[HttpDelete]`, `[HttpPatch]`, `[Route]` on methods.

```csharp
[ApiController]
[Route("api/[controller]")]
public class UsersController : ControllerBase {
    [HttpGet("{id}")]
    public IActionResult Get(int id) { ... }
}
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** method attribute `^\s*\[\s*(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch|HttpHead|HttpOptions|Route)\b`.
- **Source:** <https://learn.microsoft.com/en-us/aspnet/core/mvc/controllers/actions>

### ASP.NET Core Minimal APIs

`app.MapGet`, `app.MapPost`, etc. called on a `WebApplication`.

```csharp
var app = WebApplication.Create();
app.MapGet("/hello", () => "Hello World!");
```

- **Scope:** lambda or method group referenced
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\b([A-Za-z_]\w*)\.Map(Get|Post|Put|Delete|Patch|Methods)\s*\(`.
- **Source:** <https://learn.microsoft.com/en-us/aspnet/core/fundamentals/minimal-apis>

### Azure Functions

`[FunctionName("X")]` attribute or `[Function("X")]` (isolated model) on a method.

```csharp
public class HttpTrigger {
    [Function("GetUser")]
    public HttpResponseData Run([HttpTrigger(...)] HttpRequestData req) { ... }
}
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*\[\s*Function(Name)?\s*\(`.
- **Source:** <https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-csharp>

---

## Java

### Spring MVC / WebFlux

Classes annotated `@Controller` / `@RestController`; methods annotated `@RequestMapping`, `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping`.

```java
@RestController
@RequestMapping("/api/users")
public class UserController {
    @GetMapping("/{id}")
    public User get(@PathVariable long id) { ... }
}
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** method annotation `^\s*@\s*(Request|Get|Post|Put|Delete|Patch)Mapping\b`.
- **Source:** <https://docs.spring.io/spring-framework/reference/web/webmvc/mvc-controller.html>

### JAX-RS

Classes annotated `@Path(...)`; methods annotated `@GET`, `@POST`, `@PUT`, `@DELETE`, `@PATCH`, `@HEAD`, `@OPTIONS`.

```java
@Path("/users")
public class UserResource {
    @GET
    @Path("/{id}")
    public User get(@PathParam("id") long id) { ... }
}
```

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** method annotation `^\s*@\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b`.
- **Source:** <https://eclipse-ee4j.github.io/jakartaee-tutorial/jaxrs.html>

### Servlets

Classes extending `javax.servlet.http.HttpServlet` (or `jakarta.servlet.http.HttpServlet`). Methods `doGet`, `doPost`, `doPut`, `doDelete`, `doHead`, `doOptions`, `doTrace`.

- **Scope:** class method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** class inherits `HttpServlet`; method names match `do(Get|Post|Put|Delete|Head|Options|Trace)`.
- **Source:** <https://jakarta.ee/specifications/servlet/>

### Kafka consumer

`@KafkaListener` on a method (Spring Kafka) or a class implementing `ConsumerRebalanceListener`.

- **Scope:** method
- **Kind:** `third_party` · **Trust:** `semi_trusted_external` · **Asset:** `medium`
- **Pattern:** `^\s*@\s*KafkaListener\b`.
- **Source:** <https://docs.spring.io/spring-kafka/reference/kafka/receiving-messages/listener-annotation.html>

---

## Go

### `main`

`func main()` in `package main`.

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` (os.Args) · **Asset:** `high`
- **Pattern:** `^func main\s*\(\s*\)` in a file with `package main`.
- **Source:** <https://go.dev/ref/spec#Program_execution>

### `net/http` handlers

`http.HandleFunc("/path", handler)` or `http.Handle("/path", httpHandler)`.

```go
http.HandleFunc("/auth", authHandler)
```

- **Scope:** function (the handler name)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\bhttp\.(HandleFunc|Handle)\s*\(\s*"[^"]+"\s*,\s*(\w+)\b`.
- **Source:** <https://pkg.go.dev/net/http#HandleFunc>

### gin / chi / echo

`r.GET("/path", handler)`, `r.POST(...)`, etc. Same shape across all three routers (receiver is the router instance).

```go
r := gin.Default()
r.GET("/ping", pingHandler)
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\b(\w+)\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Any)\s*\(\s*"[^"]+"\s*,\s*(\w+)` — third capture is the handler.
- **Gotcha:** shape collides with test helpers and generic methods; narrow by import set (`github.com/gin-gonic/gin`, `github.com/go-chi/chi`, `github.com/labstack/echo`).
- **Source:** <https://gin-gonic.com/docs/>, <https://github.com/go-chi/chi>, <https://echo.labstack.com/>

### gRPC server

Struct embedding `pb.Unimplemented<Service>Server` and implementing RPC methods.

- **Scope:** struct method
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** struct embeds a type matching `Unimplemented\w+Server`; its methods are RPC handlers.
- **Source:** <https://grpc.io/docs/languages/go/basics/>

### Cobra commands

`&cobra.Command{Run: handler}` or `RunE: handler`.

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `medium`
- **Pattern:** struct literal `cobra.Command{` with `Run:` or `RunE:` field.
- **Source:** <https://github.com/spf13/cobra>

---

## Rust

### `main`

`fn main()`, often decorated with `#[tokio::main]`, `#[async_std::main]`, `#[actix_web::main]`.

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^(?:\s*#\[[^\]]+\]\s*)*\s*(?:async\s+)?fn\s+main\s*\(`.
- **Source:** Rust Book ch. 1.

### axum

`Router::new().route("/path", get(handler))` and siblings (`post`, `put`, `delete`, etc.).

```rust
let app = Router::new()
    .route("/users/:id", get(get_user).post(create_user));
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\.route\s*\(\s*"[^"]+"\s*,\s*(get|post|put|delete|patch|head|options|any)\s*\(\s*(\w+)`.
- **Source:** <https://docs.rs/axum/latest/axum/>

### actix-web

Procedural macro `#[get("/path")]`, `#[post("/path")]`, etc. on handler functions.

```rust
#[get("/users/{id}")]
async fn get_user(path: web::Path<u64>) -> impl Responder { ... }
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*#\[\s*(get|post|put|delete|patch|head|options|connect|trace)\s*\(`.
- **Source:** <https://actix.rs/docs/handlers/>

### rocket

Procedural macro `#[get("/")]`, `#[post("/")]`, etc.

```rust
#[get("/users/<id>")]
fn get_user(id: u32) -> String { ... }
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*#\[\s*(get|post|put|delete|patch|head|options)\s*\(` in a file importing `rocket`.
- **Gotcha:** syntactically identical to actix-web; disambiguate by import.
- **Source:** <https://rocket.rs/v0.5/guide/>

### warp

`warp::path!("users" / u32).and(warp::get()).map(handler)`. Less structured than axum/actix/rocket — handlers are attached via `.map`/`.and_then`.

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `\.(map|and_then)\s*\(\s*([\w:]+)\s*\)` where the enclosing chain begins with `warp::path!` or similar; needs call-graph reasoning.
- **Gotcha:** no single-line decorator; detection requires following filter chains.
- **Source:** <https://docs.rs/warp/latest/warp/>

### clap derive

`#[derive(Parser)]` on a struct; `#[derive(Subcommand)]` on an enum. The binary's `main` calls `Args::parse()` which feeds user input.

- **Scope:** struct (entrypoint data), not a function
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `medium`
- **Pattern:** `^\s*#\[derive\([^)]*\bParser\b[^)]*\)\]` preceding a struct.
- **Source:** <https://docs.rs/clap/latest/clap/>

### FFI exports

`#[no_mangle] pub extern "C" fn name(...)`.

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*(?:#\[[^\]]+\]\s*)*pub\s+extern\s+"C"\s+fn\b` or preceded by `#[no_mangle]`.
- **Source:** <https://doc.rust-lang.org/nomicon/ffi.html>

---

## Solidity

### External / public functions

Functions with visibility `external` or `public` in a contract are callable from outside. `internal` and `private` are not entrypoints.

```solidity
contract Vault {
    function withdraw(uint256 amount) external { ... }
    function deposit() public payable { ... }
    function _internal() internal { ... }  // NOT an entrypoint
}
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** function signature containing `\b(external|public)\b` between the parameter list and the opening brace (excluding parameter types).
- **Gotcha:** pre-0.5.0 Solidity defaulted to `public` when visibility was omitted; modern compilers require explicit visibility.
- **Source:** <https://docs.soliditylang.org/en/latest/contracts.html#visibility-and-getters>

### `fallback()` and `receive()`

Special functions that handle ETH transfers and calls to undefined functions.

```solidity
receive() external payable { ... }
fallback() external payable { ... }
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*(receive|fallback)\s*\(\s*\)`.
- **Source:** <https://docs.soliditylang.org/en/latest/contracts.html#special-functions>

---

## Cairo / StarkNet

### Contract entrypoints

Attributes `#[external(v0)]`, `#[view]`, `#[l1_handler]`, `#[constructor]`.

```cairo
#[starknet::contract]
mod MyContract {
    #[external(v0)]
    fn transfer(ref self: ContractState, to: ContractAddress, amount: u256) { ... }

    #[constructor]
    fn constructor(ref self: ContractState) { ... }
}
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^\s*#\[\s*(external|view|l1_handler|constructor)\b`.
- **Source:** <https://book.cairo-lang.org/ch14-00-starknet-smart-contracts.html>

---

## Circom

### `component main`

The circuit's root component declaration.

```circom
component main = MyTemplate();
component main {public [in_signal]} = MyTemplate();
```

- **Scope:** module
- **Kind:** `user_input` · **Trust:** `untrusted_external` (prover controls inputs) · **Asset:** `high`
- **Pattern:** `^\s*component\s+main\b`.
- **Source:** <https://docs.circom.io/circom-language/the-main-component/>

---

## Haskell

### `main`

Top-level `main :: IO ()` binding.

- **Scope:** function
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** `^main\s*::` or `^main\s*=`.
- **Source:** <https://www.haskell.org/onlinereport/modules.html>

### Servant

API types in a DSL (`type API = "users" :> Capture "id" Int :> Get '[JSON] User`). Handlers implement the type via `server :: Server API`.

- **Scope:** function (the handler)
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** type-level DSL; detector needs to associate the API type with the server binding — non-trivial without type inference.
- **Source:** <https://docs.servant.dev/>

### Yesod

Resources declared via `parseRoutes` QuasiQuote; handlers follow a naming convention `get<Resource>R`, `post<Resource>R`, etc.

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** functions named `(get|post|put|delete|patch)\w+R\b` in a Yesod app.
- **Source:** <https://www.yesodweb.com/book/routing-and-handlers>

---

## Erlang

### `-export`

Module-level `-export([fn/arity, ...]).` declarations mark functions callable externally.

```erlang
-module(auth).
-export([login/2, logout/1]).
```

- **Scope:** function
- **Kind:** `api` · **Trust:** `semi_trusted_external` (other Erlang processes) · **Asset:** `medium`
- **Pattern:** `^-\s*export\s*\(\s*\[([^\]]+)\]\s*\)\s*\.` — parse the function/arity list.
- **Source:** <https://www.erlang.org/doc/reference_manual/modules.html>

### OTP `gen_server`

Modules implementing `gen_server` behaviour have callbacks `init/1`, `handle_call/3`, `handle_cast/2`, `handle_info/2`, `terminate/2`, `code_change/3`. Message-borne data enters via `handle_call`/`handle_cast`.

- **Scope:** function
- **Kind:** `third_party` · **Trust:** `semi_trusted_external` · **Asset:** `medium`
- **Pattern:** module contains `-behaviour(gen_server).` and functions named `handle_call` or `handle_cast`.
- **Source:** <https://www.erlang.org/doc/design_principles/gen_server_concepts.html>

### Cowboy handlers

Modules implementing Cowboy's `init/2` + `handle/2` callbacks.

- **Scope:** function
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** module is registered as a Cowboy route target and exports `init/2`.
- **Source:** <https://ninenines.eu/docs/en/cowboy/2.10/guide/>

---

## Miden Assembly

### `begin ... end` program block

Top-level `begin ... end` defines the program's entrypoint.

```masm
use.std::sys

begin
    push.1 push.2 add
    # ...
end
```

- **Scope:** module
- **Kind:** `user_input` · **Trust:** `untrusted_external` · **Asset:** `high`
- **Pattern:** file contains a top-level `^begin\s*$`.
- **Source:** <https://0xmiden.github.io/miden-vm/user_docs/assembly/main.html>

### Exported procedures

`export.foo` declarations make procedures callable from other modules.

```masm
export.verify
    # ...
end
```

- **Scope:** procedure
- **Kind:** `api` · **Trust:** `untrusted_external` · **Asset:** `medium`
- **Pattern:** `^\s*export\.([A-Za-z_]\w*)`.

---

## Implementation notes for detectors

**File IO.** Detectors will re-read source files. Cache file contents across a detection run (dict keyed by path).

**Decorator/attribute scanning.** For each function-like `CodeUnit`, walk backwards from `location.start_line - 1` through contiguous lines matching the language's decorator/attribute syntax. Stop at the first blank line, comment-only line that isn't a decorator, or non-decorator statement.

**Visibility modifiers (Solidity, C).** Inspect the signature line itself (and continuation lines until `{` for Solidity, until `)` for C).

**Framework disambiguation.** Where patterns are syntactically identical (Flask vs. FastAPI vs. aiohttp), check imports at file scope to pick the right framework's trust/asset defaults. When ambiguous, prefer the more conservative (higher-risk) classification.

**Confidence levels.** Detectors should emit an `EntrypointKind` even when the framework is uncertain. Use the override file for correction.

**Call-graph-resolved entrypoints.** Some frameworks (Express, Starlette, Cobra) register handlers at call sites rather than decorators. A second pass after the decorator scan can resolve these by finding functions referenced as arguments to known route-registration calls.
