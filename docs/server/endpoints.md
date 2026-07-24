# Endpoints

Endpoints are registered by decorating handler functions on a `DjsonApi`
instance. Each decorator maps to a JSON:API operation and auto-generates the
corresponding route, validation, serialization, and OpenAPI spec.

## Return type shorthand

A handler returning a Resource directly is equivalent to wrapping it in
`Response`:

```python
# These are equivalent:
@api.get_one("articles")
def get_article(request, article_id: int) -> ArticleResource:
    return ArticleResource(...)

@api.get_one("articles")
def get_article(request, article_id: int) -> Response[ArticleResource]:
    return Response(data=ArticleResource(...))
```

Use `Response` when you need to include extra data — includes, pagination
links, top-level `meta`:

```python
@api.get_one("articles", include_types=[UserResource])
def get_article(request, article_id: int) -> Response[ArticleResource]:
    return Response(
        data=ArticleResource(...),
        included=[UserResource(...)],     # sideloaded resources
        links={"next": {"page": "2"}},   # pagination
        meta={"count": 42},              # top-level metadata
    )
```

## Decorator stacking

Standard Django decorators work with endpoint handlers. Place the djsonapi
decorator outermost (above other decorators):

```python
from django.contrib.auth.decorators import login_required

@api.get_one("articles")
@login_required
def get_article(request, article_id: int) -> ArticleResource:
    ...
```

The djsonapi decorator registers the inner function (including its wrappers)
as an endpoint. When a request comes in, `login_required` runs first, then
the handler.

### Async handlers

Handlers work with both `def` and `async def`:

```python
@api.get_many("articles")
async def list_articles(request, filter__title__contains: str = "") -> list[ArticleResource]:
    qs = Article.objects.all()
    if filter__title__contains:
        qs = qs.filter(title__contains=filter__title__contains)
    return [ArticleResource.from_model(a) for a in qs]
```

Sync handlers are wrapped with `sync_to_async` automatically.

## Basic CRUD

### `@api.get_one`

```python
@api.get_one("articles", errors=(NotFound,))
def get_article(request, article_id: int) -> ArticleResource:
    try:
        article = Article.objects.get(id=article_id)
    except Article.DoesNotExist:
        raise NotFound()
    return ArticleResource.from_model(article)
```

- URL: `GET /api/articles/{id}`
- Path param `article_id` is extracted from the URL and typed (int, str, UUID)
- Return type annotation drives the response schema in OpenAPI

### `@api.get_many`

```python
@api.get_many("articles")
def list_articles(
    request,
    filter__title__contains: str = "",
    filter__author: int | None = None,
    sort: Literal["title", "-title", "created_at", "-created_at"] = "-created_at",
) -> list[ArticleResource]:
    qs = Article.objects.all()
    if filter__title__contains:
        qs = qs.filter(title__contains=filter__title__contains)
    if filter__author is not None:
        qs = qs.filter(author_id=filter__author)
    if sort:
        qs = qs.order_by(sort)
    return [ArticleResource.from_model(a) for a in qs]
```

- URL: `GET /api/articles`
- Query parameters declared as function parameters with naming conventions

### `@api.create_one`

```python
@api.create_one("articles", errors=(Conflict,))
def create_article(request, payload: ArticleResource) -> ArticleResource:
    article = Article.objects.create(**payload.as_create_payload())
    return ArticleResource.from_model(article)
```

- URL: `POST /api/articles`
- First non-request parameter must be a `Resource` subclass — handler receives
  a populated instance
- Request body validated against `jsonschema_create()` before handler runs
- Only fields in `_create_fields` are extracted
- Returns `201 Created` with the serialized resource

### `@api.edit_one`

```python
@api.edit_one("articles", errors=(NotFound, Conflict))
def edit_article(request, article_id: int, payload: ArticleResource) -> ArticleResource:
    article = Article.objects.get(id=article_id)
    if payload.title is not ArticleResource.UNSET and article.title != payload.title:
        article.title = payload.title
    if payload.content is not ArticleResource.UNSET:
        article.content = payload.content
    article.save()
    return ArticleResource.from_model(article)
```

- URL: `PATCH /api/articles/{id}`
- Uses `_edit_fields` and `jsonschema_edit()`
- Fields not in the body are set to `UNSET` — always check before applying

!!! tip "UNSET handling"
    Always guard PATCH field access with `is not Resource.UNSET`. Without it,
    you can't distinguish "client didn't send this field" from "client sent
    `null`".

### `@api.delete_one`

```python
@api.delete_one("articles", errors=(NotFound,))
def delete_article(request, article_id: int) -> None:
    deleted, _ = Article.objects.filter(pk=article_id).delete()
    if deleted == 0:
        raise NotFound()
```

- URL: `DELETE /api/articles/{id}`
- Return type `None` → `204 No Content`

## Query parameters

Parameter names use `__` as a separator mapping to JSON:API bracket syntax:

| URL param | Handler param |
|-----------|---------------|
| `filter[title][contains]` | `filter__title__contains` |
| `filter[author]` | `filter__author` |
| `sort` | `sort` |
| `page` | `page` |
| `page[offset]` | `page__offset` |
| `include` | `include__author` (bool) |
| `fields[articles]` | `fields__articles` (set of str) |
| `custom_thing` | `extra__custom_thing` |

### Type coercion

```python
filter__author: int | None      # "42" → 42
filter__title__contains: str    # "json" → "json"
page: int                       # "2" → 2
sort: str                       # "-created_at" → "-created_at"
```

### Validation

- Unknown query parameters → `400 Bad Request`
- Missing required parameters → `400 Bad Request`
- Invalid type conversions → `400 Bad Request`
- `sort` with `Literal` annotation rejects invalid sort fields
- `page` must be parseable as `int`

### Sparse fields

```python
@api.get_many("articles", sparse=True)
def list_articles(
    request,
    fields__articles: set[str] | None = None,
    fields__users: set[str] | None = None,
) -> list[ArticleResource]:
    ...
```

- Client sends `?fields[articles]=title,created_at`
- Handler receives `fields__articles = {"title", "created_at"}`

Even if you ignore the `fields__` parameter, the library strips unrequested
fields from the response automatically. The parameter exists so you can make
your endpoint more efficient (skip DB queries for omitted fields).

- Set `sparse=False` on the decorator to disable for an endpoint
- Invalid type names in `fields[bad_type]` → `400`
- Declare sparse types via `fields__` parameters in the handler signature

### Includes

```python
@api.get_one("articles", include_types=[UserResource])
def get_article(
    request,
    article_id: int,
    include__author: bool = False,
) -> Response[ArticleResource]:
    article = Article.objects.get(id=article_id)
    included = []
    if include__author:
        included.append(UserResource.from_model(article.author))
    return Response(data=ArticleResource.from_model(article), included=included)
```

- Client sends `?include=author` → handler receives `include__author = True`
- Return `Response(data=..., included=[...])` to populate `included` array
- Declare valid include types in `include_types` — invalid raises `ValueError`
  at registration time
- Nested includes via double underscore: `include__author__articles`

### Extra passthrough

Parameters prefixed with `extra__` pass through as-is:

```python
def list_articles(request, extra__custom_param: str = "") -> ...:
```

- `?custom_param=value` → handler gets `custom_param = "value"`
- Use for non-standard query parameters your API needs

## Relationship endpoints

### `@api.get_related`

```python
@api.get_related("articles", "author")
def get_article_author(request, article_id: int) -> UserResource:
    article = Article.objects.get(id=article_id)
    return UserResource.from_model(article.author)
```

- URL: `GET /api/articles/{id}/author`
- Returns the full related resource
- Registers URL name `{type}__{rel}__related` for link generation

Endpoints are just functions. Call one handler from another to reuse logic:

```python
@api.get_related("users", "articles", errors=(NotFound,))
def get_user_articles(request: HttpRequest, user_id: int) -> list[ArticleResource]:
    return get_articles(request, filter__author=user_id)

# Now:
#   GET /api/users/1/articles
# is equivalent to:
#   GET /api/articles?filter[author]=1
```

Query parameters like `filter__author` are passed as regular kwargs. The library
parses them just like it would from the query string.

### `@api.get_relationship`

```python
@api.get_relationship("articles", "author")
def get_article_author_rel(request, article_id: int) -> dict:
    article = Article.objects.get(id=article_id)
    return {"type": "users", "id": str(article.author_id)}
```

- URL: `GET /api/articles/{id}/relationship/author`
- Returns only `{"data": {"type": "users", "id": "42"}}`
- **Auto-derived**: registering only `get_related` automatically creates a
  `get_relationship` endpoint

### Mutating relationships

```python
@api.edit_relationship("articles", "author")       # PATCH .../relationship/author
def edit_article_author(request, article_id: int, new_author_id: int) -> None: ...

@api.add_to_relationship("articles", "categories")  # POST .../relationship/categories
def add_categories(request, article_id: int, category_ids: list[int]) -> None: ...

@api.remove_from_relationship("articles", "categories")  # DELETE
def remove_categories(request, article_id: int, category_ids: list[int]) -> None: ...

@api.reset_relationship("articles", "categories")   # PATCH (replace all)
def reset_categories(request, article_id: int, category_ids: list[int]) -> None: ...
```

- Singular (`edit_relationship`) → single ID parameter
- Plural → `list[int]` parameter
- All return `204 No Content`
- Library parses JSON:API relationship body and coerces IDs to declared type

## Common decorator options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `errors` | `Sequence[type[DjsonApiExceptionSingle]]` | `None` | Exception types → OpenAPI error responses |
| `sparse` | `bool` | `True` | Enable/disable sparse field support |
| `include_types` | `Sequence[type[Resource]]` | `[]` | Valid include target types |

### `errors`

```python
@api.get_one("articles", errors=(NotFound,))
@api.create_one("articles", errors=(Conflict, BadRequest))
```

Declared exceptions become documented error responses in OpenAPI spec.

### `sparse`

Set `sparse=False` to disable. `fields[TYPE]` parameters rejected with 400.

### `include_types`

Declare valid include target types. Invalid includes → `400`. Feeds OpenAPI
parameter descriptions.

## RPC endpoints

RPC (Remote Procedure Call) endpoints let you define non-CRUD actions on a
resource — publish, archive, approve, regenerate, whatever doesn't fit into
`get`/`create`/`edit`/`delete`.

### `@api.rpc()`

```python
@api.rpc("articles", "publish", method="PUT")
def publish_article(request, article_id: int) -> JsonResponse:
    ...
```

- URL: `PUT /api/articles/{id}/publish`
- Handler receives the resource ID as a path parameter
- No JSON:API request body parsing — handler gets raw `HttpRequest`
- Can return any Django response type (`JsonResponse`, `HttpResponse`, etc.)
- No automatic serialization — you control the response content

### Parameters

`@api.rpc(type_name, action, path_operation, method)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type_name` | `str` | — | Resource type name (e.g. `"articles"`) |
| `action` | `str` | — | URL path segment and identifier (`"publish"`) |
| `path_operation` | `dict` | `{}` | OpenAPI operation spec merged into the endpoint |
| `method` (last positional) | `str` | `"POST"` | HTTP method (`"POST"`, `"PUT"`, etc.) |

URL pattern: `/{type_name}/{id}/{action}` — no `/rpc/` prefix.

### OpenAPI spec via `path_operation`

The `path_operation` dict is merged directly into the generated OpenAPI path item.
Use it to document request/response schemas:

```python
@api.rpc(
    "articles",
    "publish",
    {
        "summary": "Publish an article",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "notify": {"type": "boolean"},
                        },
                    }
                }
            }
        },
        "responses": {
            "200": {
                "description": "Publish result",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "published": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        },
    },
)
def publish_article(request, article_id: int) -> JsonResponse:
    article = Article.objects.get(id=article_id)
    article.published = True
    article.save()
    return JsonResponse({"success": True, "published": True})
```

Without `path_operation`, the endpoint appears in OpenAPI but has no
request/response schemas — the consumer sees `{}` for both.

### Errors

RPC endpoints don't have an `errors=` parameter. Raise exceptions normally:

```python
from djsonapi.exceptions import NotFound

@api.rpc("articles", "publish")
def publish_article(request, article_id: int) -> JsonResponse:
    try:
        article = Article.objects.get(id=article_id)
    except Article.DoesNotExist:
        raise NotFound()
    ...
```

To document error responses, include them in `path_operation`:

```python
@api.rpc("articles", "publish", {
    "responses": {
        "404": {"description": "Article not found"},
        "200": {"description": "OK", ...},
    },
})
```

### SDK integration

RPC endpoints appear in the generated SDK as a typed `rpc()` method with
action names as `Literal` types:

=== "Python"

    ```python
    article = await sdk.articles.get(1)
    result = await article.rpc('publish')
    # PUT /articles/1/publish
    ```

=== "TypeScript"

    ```typescript
    const article = await sdk.articles.get(1);
    const result = await article.rpc('publish');
    // PUT /articles/1/publish
    ```

See [SDK Resources & CRUD → RPC](../sdk/resource.md#rpc) for runtime details.

## URL names

Every endpoint gets a URL name for `reverse()` lookups:

| Endpoint | URL name pattern |
|----------|-----------------|
| `get_one` / `edit_one` / `delete_one` | `{type}__item` |
| `get_many` / `create_one` | `{type}__collection` |
| `get_related` | `{type}__{rel}__related` |
| `get_relationship` / `edit_relationship` / etc. | `{type}__{rel}__relationship` |
| `rpc` | `{type}__rpc__{action}` |

Used internally for link auto-discovery (see [URL Linking](url-linking.md)).
