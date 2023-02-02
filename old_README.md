## PostProcessing of return values

If any of the special classmethods return a `django.http.HttpResponse` object,
djsonapi will return it as-is. Otherwise:

### `get_one` and `edit_one`

If your `get_one` or `edit_one` classmethods return anything other than a
`dict`, it will be equivalent to having returned `{'data': return_value}`.
Otherwise, if you return a `dict`, it should at least have the `'data'` key.
Your return value will be enhanced by:

1. Serializing the value of `'data'` key
2. [Serializing the values of a potential `'included'` key](#serializing-included-values)
3. Adding the `'self'` link, with the same path as the request (ie
   `/<type>/<id>`)

The value of the `'data'` key does **not** have to be an ORM instance. It can
be anything, as long as it can be passed to the `serialize` classmethod without
raising errors.

The final response will have the `200 OK` status code on success.

### `create_one`

The return value should be the same as for `get_one` and `edit_one`. The
postprocessing will be almost the same, the only differences being:

1. The `'self'` link of the response won't be the same as the request's path,
   but the path of the newly created object (eg `POST /articles → {..., links:
   {self: '/articles/1'}}`)
2. The path of the newly created object will be added to the `Location` header
   of the response

The final response will have the `201 Created` status code on success.

### `get_many`

Similarly to before, if you return anything other than a `dict`, it will be
equivalent to having returned `{'data': return_value}`. Otherwise, if you
return a `dict`, it should at least have the `'data'` key. The value of the
`'data'` key should be an iterable. Your return value will be enhanced by:

1. Serializing each object of the value of the `'data'` key
2. [Serializing the values of a potential `'included'` key](#serializing-included-values)
3. Adding the `'self'` link, with the same path as the request, along with any
   GET parameters that were provided in the request
4. If the return value has other links and they are `dict`s, they will be
   replaced with a copy of `self`, using the `dict`s as replacements for GET
   parameters. eg

   ```python
   class Article(djsonapi.Resource):
       TYPE = "articles"

       @classmethod
       def get_many(cls, request):
           # ...
           return {'data': ...,
                   'links': {'previous': {'page': 3},
                             'next':     {'page': 5}}}
   ```

   assuming the request was to `/articles?include=author&page=4`, the response will have:

   ```python
   {'data': [...],
    'links': {'previous': "/articles?include=author&page=3",
              'self':     "/articles?include=author&page=4",
              'next':     "/articles?include=author&page=5"}}
   ```

The final response will have the `200 OK` status code on success.

### `delete_one`, `change_<relationship>`, `add_<relationship>`, `remove_<relationship>`, `reset_<relationship>`

These classmethods are not supposed to return anything; if they do return
something, it will be ignored.

The final response will have the `204 No content` status code on success.

### `get_<relationship>`

This method behaves mostly like `get_one` or `get_many`. Because you can use it
to return either a to-one or a to-many relationship, it will be determined
during post-processing whether the value of the `'data'` key is a list or not,
and it will be serialized accordingly. Also, to allow postprocessing to find
which serializer to use, your objects should be wrapped in the appropriate
`Resource` subclass constructor. Eg.

```python
class Article(djsonapi.Resource):
    TYPE = "articles"

    # ...

    @classmethod
    def get_author(cls, request, obj_id):
        article = ArticleModel.objects.get(id=obj_id)
        return User(article.author)
        #      ^^^^

    @classmethod
    def get_categories(cls, request, obj_id):
        article = ArticleModel.objects.get(id=obj_id)
        return [Category(category) for category in article.categories.all()]
        #       ^^^^^^^^


class User(djsonapi.Resource):
    TYPE = "users"

    # ...

    @classmethod
    def serialize(cls, obj):
        return {'id': str(obj.id), 'attributes': {'username': obj.username}}

class Category(djsonapi.Resource):
    TYPE = "categories"

    # ...

    @classmethod
    def serialize(cls, obj):
        return {'id': str(obj.id), 'attributes': {'name': obj.name}}
```

_(Note: now that we are working with multiple `Resource` subclasses, it's worth
mentioning that `.as_views()` returns a list of URL paths. So to combine them
in `urls.py`, you should write `urlpatterns = Article.as_views() +
User.as_views() + Category.as_views()`)_

The final response will have the `200 OK` status code on success.

## Serialization postprocessing

As you may have noticed in the examples, the `serialize` methods can return
incomplete {json:api} objects. They will be enhanced during postprocessing.
This enhancemenets are:

1. The `'type'` field which will be gathered by the `Resource` subbclass's
   `TYPE` class attribute
2. A `'self'` link pointing to the object's `get_one` view (eg `/articles/1`)
3. If the `fields[<TYPE>]` parameter is set, the returned 'attributes' and
   'relationships' of any objects of `<TYPE>`  will be filtered down to
   accommodate, regardless of whether they appear in the `'data'` or
   `'included'` field. If any of the requested fields do not appear in the
   (unfiltered) result, errors will be raised. The `fields[<TYPE>]` filter
   functionality is enabled by default and you cannot disable it, but you can
   [validate](#validation) against the `fields` parameter existence in your
   classmethods

Furthermore, the `'relationships'` of the serialized values can be enhanced.

### To-one relationships

If you want to describe a to-one relationship, you have the following options:

1. A `dict` with a `'data'` field that is a normal {json:api} resource
   identifier:

   ```python
   def serialize(cls, obj):
       return {...,
               'relationships': {'author': {'data': {'type': "authors",
                                                     'id': 2}}}}
   ```

2. A `dict` with a `'data'` field that is a `Resource` subclass constructor
   that wraps the `id` of the related object:

   ```python
   def serialize(cls, obj):
       return {...,
               'relationships': {'author': {'data': User(str(obj.author_id))}}}
   ```

3. A `Resource` subclass constructor that wraps the `id` of the related object:

   ```python
   def serialize(cls, obj):
       return {...,
               'relationships': {'author': User(str(obj.author_id))}}
   ```

In cases `2` and `3`, where the relationship is represented as a `Resource`
subclass instance, it will be used to generate the relationship's resource
identifier for the `'data'` field.

In all cases, postprocessing will attempt to figure out the `related` and
`self` links and add them to the serialized object if they are not already
there. The steps are:

1. For the `'related'` link, if the main `Resource` subclass (eg `Article`) has
   a `get_<relationship>` method for the relationship key currently being
   processed (eg `get_author`), its URL (eg `/articles/1/author`) will be used
2. For the `'related'` link, if the previous attempt failed and if the related
   `Resource` subclass (eg `User`) has a `get_one` method, its URL (eg
   `/users/2`) will be used
3. For the `'self'` link, if the main `Resource` subclass (eg `Article`) has a
   `change_<relationship>` method for the relationship key currently being
   processed (eg `change_author`), its URL (eg
   `/articles/1/relationships/author`) will be used

Keep in mind that postprocessing will not overwrite values that you return, but
only extend them. This way you can supply your own `'self'` and/or `'related'`
links, plus any other links you like:

```python
def serialize(cls, obj):
    return {
    ...,
    'relationships': {'author': {'data': User(str(obj.author.id)),
                                 'links': {'self': "https://reddit.com",
                                           'google': "https://google.com"}}},
    }
```

### To-many relationships

If you want to describe a to-many relationship, you should use a `dict` that
doesn't satisfy the requirements of the 'to-one' relationships we described
before. These include empty `dict`s or `dict`s with `'data'` fields that are
lists. Postprocessing will:

1. Replace any `Resource` subclass instances in the `'data'` field (if it
   exists) with the appropriate resource identifiers

   ```python
   class Article(djsonapi.Resource):
       TYPE = "articles"

       @classmethod
       def get_one(cls, resource, obj_id):
           return ArticleModel.objects.get(id=obj_id)

       @classmethod
       def serialize(cls, obj):
           return {...,
                   'relationships': {'categories': {'data': [Category('2'),
                                                             Category('3')]}}}

   class Category(djsonapi.Resource):
       TYPE = "categories"
   ```

   ```python
   # GET /articles/1 → 200 OK
   {'data': {
       ...,
       'relationships': {'categories': {'data': [
           {'type': "categories", 'id': "2"},
           {'type': "categories", 'id': "3"},
       ]}},
    }}
   ```

2. Attempt to fill the `related` and `self` links:

   1. For the `'related'` link, if the main `Resource` subclass (eg `Article`)
      has a `get_<relationship>` method (eg `get_categories`), its URL (eg
      `/articles/1/categories`) will be used
   2. For the `'self'` link, if the main `Resource` subclass (eg `Article`) has
      at least one of the `add_<relationship>`, `remove_<relationship>` or
      `reset_<relationship>` methods (eg `reset_categories`), their URL (eg
      `/articles/1/relationships/categories`) will be used

   ```python
   class Article(djsonapi.Resource):
       TYPE = "articles"

       # ...

       @classmethod
       def get_one(cls, request, obj_id):
           return ArticleModel.objects.get(id=obj_id)

       @classmethod
       def get_categories(cls, request, obj_id):
           # ...

       @classmethod
       def reset_categories(cls, request, obj_id):
           # ...

       @classmethod
       def serialize(cls, obj):
           return {..., 'relationships': {'categories': {}}}
   ```

   ```python
   # GET /articles/1 → 200 OK
   {'data': {
       ...,
       'relationships': {
           'categories': {'links': {
               'related': "/articles/1/categories",
               'self': "/articles/1/relationships/categories",
           }},
       },
   }}
   ```

As before, postprocessing will only enhance what you return, so you can supply
your own values for the `'self'` and/or `'related'` links. This can be
especially useful in to-many relationships when a `get_<relationship>` method
does not exist:

```python
class Article(djsonapi.Resource):
    TYPE = "articles"

    # ...

    @classmethod
    def get_one(cls, request, obj_id):
        return ArticleModel.objects.get(id=obj_id)

    @classmethod
    def reset_categories(cls, request, obj_id):
        # ...

    @classmethod
    def serialize(cls, obj):
        return {..., 'relationships': {'categories': {'links': {
            'related': f"/categories?filter[article]={obj.id}",
        }}}}
```

Even though we hard-coded the `'related'` link, `'self'` will be automatically
discovered by the fact that the `reset_categories` method is available.

_(Note: You should probably use the URL name from [the
table](#supported-urlsverbs) with `django.urls.reverse` instead of hardcoding
it, eg `reverse("categories_list") + f"?filter[article]={obj.id}"`)_

## Serializing `included` values

Top-level `included` values of classmethod responses should be iterables of
`Resource` subclass instances. This way, postprocessing can figure out which
serializer to use. Also, `Resource` instances are hashable so they can be
included in a `set` to remove duplicates.

For example, to include the authors of a collection of articles:

```python
class Article(djsonapi.Resource):
    TYPE = "articles"

    @classmethod
    def get_many(cls, request):
        queryset = ArticleModel.objects.all()
        result = {'data': queryset}
        if 'author' in request.GET.get('include', "").split(','):
            queryset = queryset.select_related('author')
            result['included'] = set((User(article.author)
                                      for article in queryset))
        return result

    def serialize(cls, obj):
        return {'id': str(obj.id),
                'attributes': {'title': obj.title},
                'relationships': {'author': User(str(obj.author_id))}}

class User(djsonapi.Resource):
    TYPE = "users"

    # ...

    @classmethod
    def serialize(cls, obj):
        return {'id': str(obj.id), 'attributes': {'username': obj.username}}
```

_(Notice how we wrap a User model instance with `User` in `get_many` but we
wrap a User ID with `User` in `serialize`. That is intentional)_

```python
# GET /articles?include=author → 200 OK
{
    'data': [
        {'type': "articles",
         'id': "1",
         'attributes': {'title': "Article 1"},
         'relationships': {'author': {'data': {'type': "users", 'id': "1"}}}},
        {'type': "articles",
         'id': "2",
         'attributes': {'title': "Article 2"},
         'relationships': {'author': {'data': {'type': "users", 'id': "2"}}}},
        {'type': "articles",
         'id': "3",
         'attributes': {'title': "Article 3"},
         'relationships': {'author': {'data': {'type': "users", 'id': "3"}}}},
    ],
    'included': [
        {'type': "users",
         'id': "1",
         'attributes': {'username': "user-1"}},
        {'type': "users",
         'id': "2",
         'attributes': {'username': "user-2"}},
    ],
}
```

## Middleware

If you want certain things to happen to all requests, you can use a middleware.
This is implemented as a classmethod that receives and returns a view function:

```python
from djsonapi import Resource

class Article(Resource):
    @classmethod
    def middleware(cls, get_response):
        def view(request, *args, **kwargs):
            logging.info(f"Article resource invoked with {request!r}, {args}, {kwargs}")
            return get_response(request, *args, **kwargs)
        return view

    @classmethod
    def get_one(cls, request, article_id):
        ...
```

You can stack multiple middlewares like this:

```python
from djsonapi import Resource

class ResourceBase1(Resource):
    @classmethod
    def middleware(cls, get_response):
        get_response = super().middleware(get_response)
        def view(request, *args, **kwargs):
            ...
            result = get_response(request, *args, **kwargs)
            ...
            return result
        return view

class ResourceBase2(ResourceBase1):
    @classmethod
    def middleware(cls, get_response):
        get_response = super().middleware(get_response)
        def view(request, *args, **kwargs):
            ...
            result = get_response(request, *args, **kwargs)
            ...
            return result
        return view

class Article(ResourceBase2):
    @classmethod
    def middleware(cls, get_response):
        get_response = super().middleware(get_response)
        def view(request, *args, **kwargs):
            ...
            result = get_response(request, *args, **kwargs)
            ...
            return result
        return view

    @classmethod
    def get_one(cls, request, article_id):
        ...
```

Or like this:

```python
from djsonapi import Resource

class ResourceBase1(Resource):
    @classmethod
    def middleware(cls, get_response):
        def view(request, *args, **kwargs):
            ...
            result = get_response(request, *args, **kwargs)
            ...
            return result
        return super().middleware(view)

class ResourceBase2(ResourceBase1):
    @classmethod
    def middleware(cls, get_response):
        def view(request, *args, **kwargs):
            ...
            result = get_response(request, *args, **kwargs)
            ...
            return result
        return super().middleware(view)

class Article(ResourceBase2):
    @classmethod
    def middleware(cls, get_response):
        def view(request, *args, **kwargs):
            ...
            result = get_response(request, *args, **kwargs)
            ...
            return result
        return super().middleware(view)

    @classmethod
    def get_one(cls, request, article_id):
        ...
```

In short:

- Replace the `get_response` argument with `super().middleware(get_response)`
  if you want your middleware to "wrap around" the previous middleware(s)

- Return `super().middleware(view)` instead of `view` if you want the previous
  middleware to "wrap around" your middleware

## Exceptions

If you raise any exception from `djsonapi.exceptions`, which are subclassed
from `djsonapi.exceptions.DjsonApiException`, in one of the `Resource` subclass
classmethods, a proper {json:api} error response will be returned.

```python
raise djsonapi.exceptions.NotFound("Happiness not found")
```

```python
{'errors': [{'status': "404",
             'code': "not_found",
             'title': "Not found",
             'detail': "Happiness not found"}]}
```

Apart from `detail`, you can override the `title` and supply a `source` field:

```python
raise djsonapi.exceptions.BadRequest(
    detail="'editor' isn't a valid option for 'relationships'",
    title="Invalid JSON",
    source={'pointer': ".data.relationships.editor"},
)
```

You can define your own subclasses like this:

```python
class Forbidden(djsonapi.exceptions.DjsonApiExceptionSingle):
    STATUS = 403
    CODE = "forbidden"
    TITLE = "Permission denied"
```

If not supplied, the `CODE` and `TITLE` will be automatically generated by the
exception class name. eg `NotFound` → `CODE = "not_found"`, `TITLE = "Not
found"`.

You can raise multiple exceptions at the same time using
`djsonapi.exceptions.DjsonApiExceptionMulti`, like this:

```python
raise djsonapi.exceptions.DjsonApiExceptionMulti(
    djsonapi.exceptions.NotFound("Happiness not found"),
    djsonapi.exceptions.Conflict("I am conflicted"),
)
```

Which you will most probably do with code like:

```python
from djsonapi.exceptions import NotFound, Conflict, DjsonApiExceptionMulti

# ...

errors = []
if not ArticleModel.objects.filter(id=obj_id).exists():
    errors.append(NotFound(f"Article with id '{obj_id}' not found"))
if UserModel.objects.get(username=username).exists()
    errors.append(Conflict(f"User with username '{username}' already exists"))
if errors:
    raise DjsonApiExceptionMulti(*errors)
```

```python
# 400 BadRequest
{'errors': [{'status': "404",
             'code': "not_found",
             'title': "Not found",
             'detail': "Article with id '1' not found"},
            {'status': "409",
             'code': "conflict",
             'title': "Conflict",
             'detail': "User with username 'mary' already exists"}]}
```

As per {json:api} recommendations, the most generally applicable status code
will be used for the response (_404 + 409 = 400_).

### Handling {json:api} exceptions from other views

If you have some server-side code that doesn't run within a `Resource`
subclass, you can still take advantage of `djsonapi`'s exception handling by
using `djsonapi.middleware.DjsonApiExceptionMiddleware`:

```python
# settings.py
MIDDLEWARE = [
    ...,
    "djsonapi.middleware.DjsonApiExceptionMiddleware",
]

# views.py
from django.http import JsonResponse
from djsonapi.exceptions import Unauthorized

def hello_view(request):
    if not request.user.is_authenticated:
        raise Unauthorized("You are not logged in")
    return JsonResponse({"hello": "world"})

# urls.py
from django.urls import path
from .views import hello_view

urlpatterns = [path('', hello_view)]
```

```
GET /

# Successful response
200 OK
{"hello": "world"}

# Unsuccessful response
401 Unauthorized
{"errors": [{"status": "401",
             "code": "unauthorized",
             "title": "Unauthorized",
             "detail": "You are not logged in"}]}
```

## Validation

`djsonapi` does **not** enforce any input validation method. If you choose to
use [jsonschema](https://json-schema.org/) however, you can take advantage of
the contents of the `djsonapi.jsonschema_utils` module:

- **`Object()`**: function to return `object` schema descriptions;
  `additionalProperties` will be set to False (unless overriden by kwargs) and
  `required` will be set to all keys of `properties` (unless overriden by
  kwargs):

  ```python
  Object({'a': {'type': "string"}, 'b': {'type': "number"}})
  # <<< {'type': "object",
  # ...  'additionalProperties': False,
  # ...  'required': ['a', 'b'],
  # ...  'properties': {'a': {'type': "string"}, 'b': {'type': "number"}}}

  Object({'a': {'type': "string"}, 'b': {'type': "number"}},
         required=['a'],
         additionalProperties=True)
  # <<< {'type': "object",
  # ...  'additionalProperties': True,
  # ...  'required': ['a'],
  # ...  'properties': {'a': {'type': "string"}, 'b': {'type': "number"}}}
  ```

- **`String()`**: function to return `string` schema descriptions; the first
  argument, if there, will be used for the `enum` field:

  ```python
  String()
  # <<< {'type': "string"}

  String(['one', 'two'])
  # <<< {'type': "string", 'enum': ['one', 'two']}

  String('only_option')
  # <<< {'type': "string", 'enum': ['only_option']}

  String(pattern=r'\d+')
  # <<< {'type': "string", 'pattern': r'\d+'}
  ```

- **`get_body`**: A simple function that returns `json.loads(request.body)`,
  raising an appropriate `BadRequest` exception on error

- **`raise_for_params(obj, schema)`**: validate `obj` against `schema`, raising
  appropriate `BadRequest` exceptions if necessary; intended for use with
  `request.GET` as `obj`

  ```python
  class User(djsonapi.Resource):
      TYPE = "users"

      @classmethod
      def get_many(cls, request):
          schema = Object({'filter[author]': String(),
                           'page': String(pattern=r'\d+'),
                           'include': String("author")},
                          required=[])
          raise_for_params(request.GET, schema)
          # ...
  ```

  ```python
  # GET /articles?page=one&include=autor&a=b

  # 400 Bad request
  {'errors': [{"status": "400",
               "code": "bad_request",
               "title": "Bad request",
               "detail": "Additional properties are not allowed ('a' was unexpected)"},
              {'status': "400",
               'code': "bad_request",
               'title': "Bad request",
               'detail': "'one' does not match '\\\\d+'",
               'source': {'parameter': "page"}},
              {'status': "400",
               'code': "bad_request",
               'title': "Bad request",
               'detail': "'autor' is not one of ['author']",
               'source': {'parameter': "include"}}]}
  ```

  _Keep in mind that `request.GET` values are always strings. This makes it a
  bit uncomfortable to validate against numbers, like we do here for the `page`
  parameter._

- **`raise_for_body(obj, schema)`**: similar to `raise_for_params`, but
  intended for the JSON body of a request

  ```python
  class User(djsonapi.Resource):
      TYPE = "users"

      @classmethod
      def create_one(cls, request):
          body = get_body(request)
          schema = Object({
              'data': Object({'type': String("users"),
                              'attributes': Object({'username': String()})})
          })
          raise_for_body(body, schema)
          # ...
  ```

  ```python
  # POST /users {'data': {'type': "user",
  #                       'attributes': {'username': 3,
  #                                      'password': "password"}},
  #              'links': {'google': "https://www.google.com"}}

  # 400 Bad request
  {'errors': [
    {'status': "400",
     'code': "bad_request",
     'title': "Bad request",
     'detail': "Additional properties are not allowed ('links' was unexpected)",
     'source': {'pointer': "."}},
    {'status': "400",
     'code': "bad_request",
     'title': "Bad request",
     'detail': "'user' is not one of ['users']",
     'source': {'pointer': ".data.type"}},
    {'status': "400",
     'code': "bad_request",
     'title': "Bad request",
     'detail': "Additional properties are not allowed ('password' was unexpected)",
     'source': {'pointer': ".data.attributes"}},
    {'status': "400",
     'code': "bad_request",
     'title': "Bad request",
     'detail': "3 is not of type 'string'",
     'source': {'pointer': ".data.attributes.username"}}
  ]}
  ```

## Mapping a view to another

Consider this example:

```python
class Article(djsonapi.Resource):
    TYPE = "articles"

    @classmethod
    def get_many(cls, request):
        # Validation
        schema = Object({'filter[category]': String(),
                         'page': String(pattern=r'^\d+$')},
                        required=[])
        raise_for_params(request.GET, schema)

        queryset = ArticleModel.objects.all()

        # Filter
        if 'filter[category]' in request.GET:
            try:
                category = CategoryModel.objects.\
                    get(id=request.GET['filter[category]'])
            except CategoryModel.DoesNotExist:
                raise NotFound(
                    f"Category with id '{request.GET['filter[category]']}' "
                    f"not found"
                )
            queryset = queryset.filter(categories=category)

        # Paginate
        page = int(request.GET.get('page', '1'))
        start = (page - 1) * 10
        end = start + 10

        result = {'data': queryset[start:end]}
        if page > 1:
            result.setdefault('links', {})['previous'] = {'page': page - 1}
        if queryset.count() > end:
            result.setdefault('links', {})['next'] =     {'page': page + 1}

    @classmethod
    def serialize(cls, obj):
        return {'id': str(obj.id),
                'attributes': {'title': obj.title, 'content': obj.content}}
```

Basically an article collection view (`/articles`) that supports filtering by
category and pagination.

What if we want to write a "category-to-articles" view
(`/categories/1/articles`) that supports the same features (input validation
and pagination in this example)? In order to avoid having to replicate the
code, `djsonapi` provides the `map_to_method` utility.

```python
class Category(djsonapi.Resource):
    TYPE = "categories"

    @classmethod
    def get_articles(cls, request, obj_id):
        return cls.map_to_method(request, Article, 'get_many',
                                 {'filter[category]': obj_id})
```

`map_to_method` invokes `Article.get_many`, passing a request object whose GET
parameters are temporarily enhanced by the `{'filter[category]': obj_id}`
dictionary and wraps each item in the result's `'data'` field with the
`Article` class.

Using this utility, the `Category.get_articles` method will have all the
features of `Article.get_many`, including:

1. Validation of GET parameters
2. Support for pagination (or any other feature that `Article.get_many` may
   have)
3. Since `Article.get_many`'s links are described as `dict`s, the final
   response will have appropriate pagination links

```python
# GET /categories/1/articles?page=3 → 200 OK
{'data': [...],
 'links': {'previous': "/categories/1/articles?page=2",
           'self':     "/categories/1/articles?page=3",
           'next':     "/categories/1/articles?page=4"}}
```
