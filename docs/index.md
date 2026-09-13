# djsonapi — JSON:API for Django

[JSON:API](https://jsonapi.org/) is a spec that makes you stop arguing about API
design and start shipping. Every decision is made ahead of time — how attributes
differ from relationships, how to request sparse fields, how to paginate, how to
sideload related resources. One consistent wire format means one client
generator, one docs strategy, one way to think.

djsonapi gives you:

- **Minimal Resource classes** — pydantic-adjacent, behave like dataclasses,
  type annotations drive everything
- **FastAPI-style decorators** — JSON:API-aware, auto-validate, auto-parse query
  params, generate OpenAPI
- **Middleware** — auth, logging, and other cross-cutting concerns, see
  [Middleware](server/middleware.md)
- **Free OpenAPI 3.0.3 + Redoc docs** — at `/api/openapi.json` and `/api/docs/`
- **Python & TypeScript SDKs** — generated from your API, sealed types, IDE
  autocomplete

## Quickstart

Install:

```bash
pip install djsonapi
```

Add to `INSTALLED_APPS`. Define a resource:

```python
from djsonapi import Resource
from typing import ClassVar

class ArticleResource(Resource):
    _type: ClassVar = "articles"
    _attributes: ClassVar = ["title", "content", "created_at"]
    _singular_relationships: ClassVar = [("author", "users")]
    _create_fields: ClassVar = ["title", "content", "author"]
    _required_create_fields: ClassVar = ["title"]

    id: int
    title: str
    content: str
    created_at: datetime
    author: int
```

Resources behave like dataclasses. Instantiate and interact with them directly:

```python
article = ArticleResource(id=1, title="Hello", content="...",
                          created_at=datetime.now(), author=42)
article.title = "Updated title"
```

Register endpoints:

```python
from djsonapi import DjsonApi

api = DjsonApi()

@api.get_one("articles")
def get_article(request, article_id: int) -> ArticleResource:
    return ArticleResource.from_model(Article.objects.get(id=article_id))

@api.get_many("articles")
def list_articles(request) -> list[ArticleResource]:
    return [ArticleResource.from_model(a) for a in Article.objects.all()]

@api.create_one("articles")
def create_article(request, payload: ArticleResource) -> ArticleResource:
    article = Article.objects.create(**payload.as_create_payload())
    return ArticleResource.from_model(article)

@api.edit_one("articles")
def edit_article(request, article_id: int, payload: ArticleResource) -> ArticleResource:
    article = Article.objects.get(id=article_id)
    for key, value in payload.as_edit_payload().items():
        setattr(article, key, value)
    article.save()
    return ArticleResource.from_model(article)

@api.delete_one("articles")
def delete_article(request, article_id: int) -> None:
    Article.objects.get(id=article_id).delete()
```

Wire it up:

```python
urlpatterns = [path("api/", api.urls)]
```

| Method   | URL                  | What         |
| -------- | -------------------- | ------------ |
| `GET`    | `/api/articles`      | List         |
| `POST`   | `/api/articles`      | Create       |
| `GET`    | `/api/articles/{id}` | Read         |
| `PATCH`  | `/api/articles/{id}` | Update       |
| `DELETE` | `/api/articles/{id}` | Delete       |
| `GET`    | `/api/openapi.json`  | OpenAPI spec |
| `GET`    | `/api/docs/`         | Redoc docs   |

## What the library did for you

- **Content-Type**: rejected anything not `application/vnd.api+json` on writes
- **Accept**: enforced `application/vnd.api+json`
- **JSON Schema validation**: request body validated before your handler saw it
- **Serialization**: your Resource → proper JSON:API shape with `type`, `id`,
  `attributes`, `relationships`, `links`
- **Link generation**: `self` and relationship links auto-populated based on
  registered endpoints
- **Error documents**: 400/404/405/500 all return JSON:API error bodies

## OpenAPI & Redoc

Visit `/api/docs/` after starting your server:

<!-- SCREENSHOT: browser showing Redoc UI at /api/docs/ with articles endpoints listed -->

## SDK generation

One command generates a sealed, typed client:

=== "Python"

    ```bash
    ./manage.py generate_jsonapi_client articles.views::api \
        --output ~/articles_sdk --language python
    ```

=== "TypeScript"

    ```bash
    ./manage.py generate_jsonapi_client articles.views::api \
        --output ~/articles_sdk_ts --language typescript
    ```

### Usage

=== "Python"

    ```python
    from articles_sdk import sdk

    sdk.setup(
        host="http://localhost:8000/api/",
        headers=lambda: {"Authorization": "Bearer token"},
    )

    async with sdk:
        article = await sdk.articles.get(1)
        print(article.title)          # "Why JSON:API is great"
        print(article.created_at)     # datetime — auto-converted

        await article.save(title="New title")
        await article.delete()
    ```

=== "TypeScript"

    ```typescript
    import { sdk } from "./articles_sdk_ts/index.js";

    sdk.setup({
        host: "http://localhost:8000/api/",
        headers: async () => ({}),
    });

    const article = await sdk.articles.get(1);
    console.log(article.title);

    await article.save({ title: "New title" });
    await article.delete();
    ```

!!! tip "Why not a global singleton?"

    The exported `sdk` is convenient for simple apps, but a single global config
    is unsafe when background tasks make requests on behalf of different users
    (e.g., OAuth). Instantiate separate SDKs with their own config:

    ```python
    admin_sdk = SDK(host="...", headers=lambda: {"Authorization": "Bearer " + admin_token})

    user_sdk = SDK(host="...", headers=lambda: {"Authorization": "Bearer " + user_token})
    ```

### IDE autocompletion

Every method, every filter, every field — generated SDKs are fully typed:

<!-- SCREENSHOT: VSCode showing autocomplete popup for sdk.articles.get() with parameter hints -->
<!-- SCREENSHOT: VSCode showing autocomplete for article.save() showing only writable fields -->

## Requirements

- Python ≥ 3.13
- Django ≥ 4.2
- aiohttp ≥ 3.14 (client only)
- jsonschema ≥ 4.20
