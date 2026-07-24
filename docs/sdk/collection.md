# Collections

A `Collection` is a **lazy, chainable** wrapper around a list endpoint. No HTTP
request happens until you `await` it (Python) or call `.fetch()` (TypeScript).

## Creating a Collection

=== "Python"

    ```python
    articles = sdk.articles.list()
    # No HTTP request — just stores URL and params
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list();
    // No HTTP request yet
    ```

## Chaining

Every method returns a **new** `Collection` instance. The original is untouched
— useful when you conditionally modify a query before sending it, similar to
Django's `QuerySet`:

=== "Python"

    ```python
    articles = sdk.articles.list()  # base query

    if search:
        articles = articles.filter(title__contains=search)
    if sort_by:
        articles = articles.sort(sort_by)
    if page:
        articles = articles.page(page)

    result = await articles  # single HTTP request with all params
    ```

=== "TypeScript"

    ```typescript
    let articles = sdk.articles.list();  // base query

    if (search) {
        articles = articles.filter({ title__contains: search });
    }
    if (sortBy) {
        articles = articles.sort(sortBy);
    }
    if (page) {
        articles = articles.page(page);
    }

    await articles.fetch();  // single HTTP request with all params
    ```

### `filter()`

=== "Python"

    ```python
    articles = sdk.articles.list().filter(title__contains="json")
    # GET /articles?filter[title][contains]=json
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list().filter({ title__contains: "json" });
    // GET /articles?filter[title][contains]=json
    ```

### `sort()`

=== "Python"

    ```python
    articles = sdk.articles.list().sort("-created_at", "title")
    # ?sort=-created_at,title
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list().sort("-created_at", "title");
    // ?sort=-created_at,title
    ```

### `page()`

=== "Python"

    ```python
    articles = sdk.articles.list().page(2)
    # ?page=2

    # Named parameters
    articles = sdk.articles.list().page(offset=20, limit=10)
    # ?page[offset]=20&page[limit]=10
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list().page(2);
    // ?page=2

    # Named parameters
    articles = sdk.articles.list().page({ offset: 20, limit: 10 })
    // ?page[offset]=20&page[limit]=10
    ```

### `include()`

=== "Python"

    ```python
    articles = sdk.articles.list().include("author", "categories")
    # ?include=author,categories
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list().include("author", "categories");
    // ?include=author,categories
    ```

### `fields()`

=== "Python"

    ```python
    articles = sdk.articles.list().fields(articles=["title", "created_at"])
    # ?fields[articles]=title,created_at
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list().fields({
        articles: ["title", "created_at"],
    });
    // ?fields[articles]=title,created_at
    ```

### `extra()`

Non-standard query parameters:

=== "Python"

    ```python
    articles = sdk.articles.list().extra(custom_param="value")
    # ?custom_param=value
    ```

=== "TypeScript"

    ```typescript
    const articles = sdk.articles.list().extra({ custom_param: "value" });
    // ?custom_param=value
    ```

## Fetching

=== "Python"

    ```python
    articles = await sdk.articles.list()
    # GET /articles

    # Now data is available
    print(articles[0])       # first article
    print(len(articles))     # count
    for a in articles:       # iterate
        print(a.title)
    ```

    Accessing data before fetch raises `RuntimeError`:

    ```python
    col = sdk.articles.list()
    col[0]  # RuntimeError: Data not fetched yet
    ```

=== "TypeScript"

    ```typescript
    const articles = await sdk.articles.list().fetch();
    // GET /articles

    console.log(articles.at(0));  // first article
    console.log(articles.length); // count
    for (const a of articles) {   // iterate
        console.log(a.title);
    }
    // Use spread for array methods
    const titles = [...articles].map(a => a.title);
    ```

## Pagination

After fetch, navigate pages:

=== "Python"

    ```python
    col = await sdk.articles.list().page(1)

    if col.has_next():
        next_page = await col.get_next()
        for article in next_page:
            print(article.title)

    # Iterate ALL pages
    async for page in col.all_pages():
        for article in page:
            print(article.title)

    # Iterate ALL items across all pages
    async for article in col.all():
        print(article.title)
    ```

=== "TypeScript"

    ```typescript
    const col = await sdk.articles.list().page(1).fetch();

    if (col.hasNext()) {
        const nextPage = await col.getNext().fetch();
        for await (const article of nextPage) {
            console.log(article.title);
        }
    }

    // Iterate ALL pages
    for await (const page of col.allPages()) {
        for await (const article of page) {
            console.log(article.title);
        }
    }

    // Iterate ALL items across all pages
    for await (const article of col.all()) {
        console.log(article.title);
    }
    ```

### Navigation methods

| Method           | Returns      | Description              |
| ---------------- | ------------ | ------------------------ |
| `has_next()`     | `bool`       | Next page available?     |
| `get_next()`     | `Collection` | Fetch next page          |
| `has_previous()` | `bool`       | Previous page available? |
| `get_previous()` | `Collection` | Fetch previous page      |
| `has_first()`    | `bool`       | First page link exists?  |
| `get_first()`    | `Collection` | Fetch first page         |
| `has_last()`     | `bool`       | Last page link exists?   |
| `get_last()`     | `Collection` | Fetch last page          |

These operate on the `links` object from JSON:API responses. Server must include
pagination links (via `Response(links={...})`) for these to work.

## Async iteration

=== "Python"

    ```python
    async for article in await sdk.articles.list():
        print(article.title)
    ```
    `__aiter__` fetches first if needed, then yields items.

=== "TypeScript"

    ```typescript
    for await (const article of sdk.articles.list()) {
        console.log(article.title);
    }
    ```
    `Collection` implements `AsyncIterable<T>`. Iteration triggers fetch.


