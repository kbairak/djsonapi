# Middleware

Cross-cutting concerns (auth, logging, timing) belong in middleware.

## Why not Django middleware?

Django `MIDDLEWARE` runs at the WSGI/ASGI level and cannot raise
`DjsonApiException` cleanly — the framework expects those in handler code.
Middleware in djsonapi wraps the handler directly, so raising
`Unauthorized`, `Forbidden`, etc. produces a proper JSON:API error response.

## API-level middleware

Pass a list to `DjsonApi`:

```python
from djsonapi import DjsonApi
from djsonapi.exceptions import Unauthorized
import functools

def auth_required(func):
    @functools.wraps(func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise Unauthorized("Authentication required")
        return func(request, *args, **kwargs)
    return wrapper

def log_request(func):
    @functools.wraps(func)
    def wrapper(request, *args, **kwargs):
        print(f"→ {request.method} {request.path}")
        return func(request, *args, **kwargs)
    return wrapper

api = DjsonApi(middleware=[auth_required, log_request])
```

Every endpoint registered on this `api` runs `auth_required` first, then
`log_request`, then the handler.

## Equivalence principle

`middleware=[a, b]` is equivalent to stacking decorators manually:

```python
# These produce the same result:

@api.get_one("articles")
@b
@a
def get_article(request, article_id: int) -> ArticleResource:
    ...

api = DjsonApi(middleware=[a, b])
@api.get_one("articles")
def get_article(request, article_id: int) -> ArticleResource:
    ...
```

First middleware in the list (leftmost) wraps the outermost layer and runs
first — just like the topmost `@decorator` in a stack.

## Per-endpoint middleware

Override middleware for a single endpoint:

```python
@api.get_one("articles", middleware=[cache_check])
def get_article(request, article_id: int) -> ArticleResource:
    ...
```

Only `cache_check` runs — API-level middleware is ignored.

## Skipping all middleware

Pass an empty list to run no middleware at all:

```python
@api.get_one("articles", middleware=[])
def get_article(request, article_id: int) -> ArticleResource:
    ...
```

Useful for health checks or public endpoints.

## Omitting middleware

When `middleware` is not passed, the endpoint inherits the API-level list:

```python
api = DjsonApi(middleware=[auth_required, log_request])

@api.get_one("articles")          # inherits [auth_required, log_request]
def get_article(request, article_id: int) -> ArticleResource:
    ...
```

## Middleware contract

Each middleware receives the handler and returns a wrapper:

```python
def my_middleware(next_handler):
    @functools.wraps(next_handler)
    def wrapper(request, *args, **kwargs):
        # before handler
        result = next_handler(request, *args, **kwargs)
        # after handler
        return result
    return wrapper
```

- Signature: `(request, *args, **kwargs)`
- Must call `next_handler` unless short-circuiting
- May raise `DjsonApiException` (e.g. `Unauthorized`, `Forbidden`)
- Should use `@functools.wraps(func)` to preserve handler signature

## Error handling

Middleware can short-circuit by raising:

```python
def admin_only(func):
    @functools.wraps(func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Forbidden("Staff only")
        return func(request, *args, **kwargs)
    return wrapper
```

The framework catches these and returns a proper JSON:API error document.

## Async middleware

If your handler is `async def`, middleware must also be async:

```python
async def log_timing(func):
    @functools.wraps(func)
    async def wrapper(request, *args, **kwargs):
        start = time.time()
        result = await func(request, *args, **kwargs)
        print(f"Took {time.time() - start:.2f}s")
        return result
    return wrapper
```

Recommendation: always write middleware as async — it works for both sync and
async handlers.