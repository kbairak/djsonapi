# Resources & CRUD

## Fetching

### `get(id, *includes)`

=== "Python"

    ```python
    article = await sdk.articles.get(1)
    # GET /articles/1

    article = await sdk.articles.get(1, "author", "categories")
    # GET /articles/1?include=author,categories
    ```

=== "TypeScript"

    ```typescript
    const article = await sdk.articles.get(1);
    // GET /articles/1

    const article2 = await sdk.articles.get(1, "author", "categories");
    // GET /articles/1?include=author,categories
    ```

### `find(**query, *includes)`

Fetch a single resource by filter. Asserts exactly one result.

=== "Python"

    ```python
    user = await sdk.users.find(username="admin")
    # GET /users?filter[username]=admin

    article = await sdk.articles.find(title__contains="django", "author")
    # GET /articles?filter[title][contains]=django&include=author
    ```

=== "TypeScript"

    ```typescript
    const user = await sdk.users.find({ username: "admin" });
    // GET /users?filter[username]=admin

    const article = await sdk.articles.find(
        { title__contains: "django" },
        "author",
    );
    // GET /articles?filter[title][contains]=django&include=author
    ```

### `list()`

Returns a lazy `Collection` — see [Collections](collection.md).

=== "Python"

    ```python
    articles = sdk.articles.list()
    # No HTTP request yet

    await articles
    # GET /articles
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list();
    // No HTTP request yet

    await articles.fetch();
    // GET /articles
    ```

## Creating

=== "Python"

    ```python
    article = await sdk.articles.create(
        title="New Post",
        content="Hello",
        author=admin_user,  # Resource or ID
    )
    # POST /articles
    ```

    If you `save()` a resource without an ID, it issues a POST (not PATCH):

    ```python
    article = sdk.articles._new(title="New Post", content="Hello")
    # article.id is None
    await article.save()
    # POST /articles — creates, returns with server-assigned ID
    ```

    For client-generated IDs, use `create()`:

    ```python
    article = await sdk.articles.create(id=42, title="New Post")
    # POST /articles — server must support client IDs
    ```

=== "TypeScript"

    ```typescript
    const article = await sdk.articles.create({
        title: "New Post",
        content: "Hello",
        author: adminUser,  // Resource or ID
    });
    // POST /articles
    ```

Available only if `@api.create_one` was registered.

## Updating

Two equivalent forms:

=== "Python"

    ```python
    # Form 1 — pass fields as kwargs
    await article.save(title="Updated", content="New content")
    # PATCH /articles/1

    # Form 2 — set attributes, then save with field names
    article.title = "Updated"
    article.content = "New content"
    await article.save("title", "content")
    # PATCH /articles/1
    ```

=== "TypeScript"

    ```typescript
    // Form 1 — pass fields as object
    await article.save({ title: "Updated", content: "New content" });
    // PATCH /articles/1

    // Form 2 — set attributes, then save with field names
    article.title = "Updated";
    article.content = "New content";
    await article.save("title", "content");
    // PATCH /articles/1
    ```

### `refetch()`

Always hits the network, replaces local state:

=== "Python"

    ```python
    await article.refetch()
    # GET /articles/1
    ```

=== "TypeScript"

    ```typescript
    await article.refetch();
    // GET /articles/1
    ```

## Deleting

=== "Python"

    ```python
    await article.delete()
    # DELETE /articles/1  → 204
    # article.id is now None
    ```

=== "TypeScript"

    ```typescript
    await article.delete();
    // DELETE /articles/1  → 204
    // article.id is now null
    ```

Available only if `@api.delete_one` was registered.

## Relationships

### Accessing relationships

When a resource has a relationship, you access it as a regular attribute.
Whether it's immediately usable depends on whether it was **included**.

=== "Python"

    ```python
    async with sdk:
        # Without include — unfetched stub
        article = await sdk.articles.get(1)
        # article.author is Resource(id=42) — only knows ID
        # article.author.username → AttributeError

        await article.author  # GET /articles/1/author
        print(article.author.username)  # "jdoe"

        # With include — fully populated
        article = await sdk.articles.get(1, "author")
        print(article.author.username)  # "jdoe", no extra HTTP call
    ```

=== "TypeScript"

    ```typescript
    // Without include — unfetched stub
    const article = await sdk.articles.get(1);
    // article.author is Resource — only knows id=42

    await article.author.fetch();  // GET /articles/1/author
    console.log(article.author.username);  // "jdoe"

    // With include — fully populated
    const article2 = await sdk.articles.get(1, "author");
    console.log(article2.author.username);  // "jdoe", no extra HTTP
    ```

### Plural relationships

Plural relationships return a `Collection` instead of a single resource.

=== "Python"

    ```python
    article = await sdk.articles.get(1)
    # article.categories → unfetched Collection

    await article.categories  # GET /articles/1/categories
    for category in article.categories:
        print(category.name)

    # With include — pre-populated
    article = await sdk.articles.get(1, "categories")
    for cat in article.categories:
        print(cat.name)
    ```

=== "TypeScript"

    ```typescript
    const article = await sdk.articles.get(1);
    // article.categories → unfetched Collection

    await article.categories.fetch();  // GET /articles/1/categories
    for (const cat of article.categories) {
        console.log(cat.name);
    }

    // With include — pre-populated
    const article2 = await sdk.articles.get(1, "categories");
    for (const cat of article2.categories) {
        console.log(cat.name);
    }
    ```

### Mutating relationships

=== "Python"

    ```python
    await article.add("categories", cat1, cat2)
    # POST /articles/1/relationship/categories

    await article.remove("categories", cat1)
    # DELETE /articles/1/relationship/categories

    await article.reset("categories", cat1, cat2, cat3)
    # PATCH /articles/1/relationship/categories (replaces all)

    await article.edit("author", new_author)
    # PATCH /articles/1/relationship/author (singular replace)
    ```

=== "TypeScript"

    ```typescript
    await article.add("categories", cat1, cat2);
    // POST /articles/1/relationship/categories

    await article.remove("categories", cat1);
    // DELETE /articles/1/relationship/categories

    await article.reset("categories", cat1, cat2, cat3);
    // PATCH /articles/1/relationship/categories

    await article.edit("author", newAuthor);
    // PATCH /articles/1/relationship/author
    ```

After mutation, local relationship is invalidated. Next `await` fetches fresh:

=== "Python"

    ```python
    await article.add("categories", cat1)
    await article.categories  # GET /articles/1/categories
    ```

=== "TypeScript"

    ```typescript
    await article.add("categories", cat1);
    await article.categories.fetch();  // GET /articles/1/categories
    ```

Only operations with registered endpoints exist. Calling
`article.add("categories", ...)` without a matching server endpoint raises
`AttributeError` at class definition time.

## RPC

Call custom actions on a resource via `rpc()`:

=== "Python"

    ```python
    result = await article.rpc('publish')
    # PUT /articles/1/publish
    ```

=== "TypeScript"

    ```typescript
    const result = await article.rpc('publish');
    // PUT /articles/1/publish
    ```

The HTTP method is auto-detected from the generated `_rpc_methods` ClassVar.
Available actions are typed as `Literal` — only valid names compile.

### Signature

```python
rpc(action, payload=None, mimetype=None)
```

| Param     | Type                                              | Behavior                                             |
| --------- | ------------------------------------------------- | ---------------------------------------------------- |
| `action`  | `str` (typed as `Literal[...]` in generated code) | RPC action name                                      |
| `payload` | omitted / `None`                                  | No request body                                      |
| `payload` | dict / list / scalar (no `mimetype`)              | Sent as JSON, auto `Content-Type: application/json`  |
| `payload` | any value + `mimetype` set                        | Sent as raw body with explicit `Content-Type` header |

### Examples

=== "Python"

    ```python
    # No payload
    await article.rpc('publish')
    # PUT /articles/1/publish  (no body)

    # JSON payload
    await article.rpc('archive', {'reason': 'old'})
    # POST /articles/1/archive
    # Content-Type: application/json
    # {"reason": "old"}

    # Raw payload with custom Content-Type
    await article.rpc('import', json.dumps({...}).encode(), 'application/vnd.api+json')
    # POST /articles/1/import
    # Content-Type: application/vnd.api+json
    # {"...": ...}
    ```

=== "TypeScript"

    ```typescript
    // No payload
    await article.rpc('publish');
    // PUT /articles/1/publish  (no body)

    // JSON payload
    await article.rpc('archive', { reason: 'old' });
    // POST /articles/1/archive
    // Content-Type: application/json
    // {"reason": "old"}

    // Raw payload with custom Content-Type
    await article.rpc('import', JSON.stringify({...}), 'application/vnd.api+json');
    // POST /articles/1/import
    // Content-Type: application/vnd.api+json
    // {"...": ...}
    ```

### Response

The method tries to parse the response body as JSON. If parsing fails, it
returns the raw text:

=== "Python"

    ```python
    result = await article.rpc('publish')
    # {"success": true, "published": true}  (dict)

    result = await article.rpc('export')
    # "raw text content"  (str)
    ```

=== "TypeScript"

    ```typescript
    const result = await article.rpc('publish');
    // { success: true, published: true }  (object)

    const result = await article.rpc('export');
    // "raw text content"  (string)
    ```

### Generated code

The SDK generator emits a `_rpc_methods` ClassVar and a type-narrowed `rpc()`
override with `Literal` action types:

=== "Python"

    ```python
    class Article(Resource):
        _rpc_methods: ClassVar[dict[str, str]] = {'publish': 'PUT'}

        async def rpc(self, action: Literal['publish'], payload: Any = None, mimetype: str | None = None) -> Any:
            return await super().rpc(action, payload=payload, mimetype=mimetype)
    ```

=== "TypeScript"

    ```typescript
    class Article extends Resource {
        static _rpcMethods: Record<string, string> = { publish: "PUT" };

        async rpc(action: 'publish', payload?: unknown, mimetype?: string): Promise<any> {
            return super.rpc(action as string, payload, mimetype);
        }
    }
    ```

Only actions registered via `@api.rpc()` on the server appear in the generated
SDK. Calling an unregistered action is a type error in your IDE.
