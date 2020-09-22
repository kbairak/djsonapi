import itertools
from collections.abc import Mapping

from django.contrib.auth.models import User as UserModel
from django.utils.text import slugify

import djsonapi
from djsonapi.exceptions import Conflict, DjsonApiExceptionMulti, NotFound
from djsonapi.jsonschema_utils import (Object, String, get_body,
                                       raise_for_body, raise_for_params)

from .models import Article as ArticleModel
from .models import Category as CategoryModel


class Article(djsonapi.Resource):
    TYPE = "articles"

    @classmethod
    def get_one(cls, request, obj_id):
        schema = Object({'include': String('author'),
                         f'fields[{cls.TYPE}]': String(),
                         f'fields[{User.TYPE}]': String()},
                        required=[])
        raise_for_params(request.GET, schema)

        queryset = ArticleModel.objects.filter(id=obj_id)

        include_author = request.GET.get('include') == "author"
        if include_author:
            queryset = queryset.select_related('author')

        article = cls._get_article(obj_id, queryset)

        result = {'data': article}

        if include_author:
            result['included'] = [User(article.author)]

        return result

    @classmethod
    def edit_one(cls, request, obj_id):
        body = get_body(request)
        schema = Object({'data': Object(
            {
                'type': String(cls.TYPE),
                'id': String(),
                'attributes': Object({'slug': String(),
                                      'title': String(),
                                      'content': String()},
                                     required=[],
                                     minProperties=1),
                'relationships': Object({
                    'author': Object({
                        'data': Object({'type': String(User.TYPE),
                                        'id': String()}),
                    })
                }),
            },
            required=['type', 'id'],
            minProperties=3,
        )})
        raise_for_body(body, schema)

        article = cls._get_article(obj_id,
                                   ArticleModel.objects.
                                   select_related('author'))

        kwargs = dict(body['data'].get('attributes', {}))
        if 'relationships' in body['data']:
            user_id = body['data']['relationships']['author']['data']['id']
            if int(user_id) != article.author_id:
                kwargs['author'] = User._get_user(user_id)

        for key, value in kwargs.items():
            setattr(article, key, value)
        article.save()

        return {'data': article, 'included': [User(article.author)]}

    @classmethod
    def delete_one(cls, request, obj_id):
        count, _ = ArticleModel.objects.filter(id=obj_id).delete()
        if count == 0:
            raise NotFound(f"Article with id '{obj_id}' not found")

    @classmethod
    def create_one(cls, request):
        body = get_body(request)
        schema = Object({'data': Object({
            'type': String(cls.TYPE),
            'attributes': Object({'slug': String(),
                                  'title': String(),
                                  'content': String()},
                                 required=['title', 'content']),
            'relationships': Object({'author': Object({'data': Object({
                'type': String(User.TYPE),
                'id': String(),
            })})}),
        })})
        raise_for_body(body, schema)

        user_id = body['data']['relationships']['author']['data']['id']
        author = User._get_user(user_id)

        attributes = body['data']['attributes']
        if 'slug' in attributes:
            slug = attributes.pop('slug')
            if ArticleModel.objects.filter(slug=slug).exists():
                raise Conflict(f"Article with slug '{slug}' already exists")
        else:
            prefix = slugify(attributes['title'])
            existing_slugs = set(ArticleModel.objects.
                                 filter(slug__startswith=prefix).
                                 values_list('slug', flat=True))
            if prefix not in existing_slugs:
                slug = prefix
            else:
                for i in itertools.count(1):
                    slug = f"{prefix}-{i}"
                    if slug not in existing_slugs:
                        break
        try:
            article = ArticleModel.objects.\
                create(author=author, slug=slug, **attributes)
        except Exception as exc:
            raise Conflict(str(exc))

        return {'data': article, 'included': [User(author)]}

    @classmethod
    def get_many(cls, request):
        schema = Object({'filter[author]': String(),
                         'filter[category]': String(),
                         'page': String(pattern=r'^\d+$'),
                         'include': String('author'),
                         f'fields[{cls.TYPE}]': String(),
                         f'fields[{User.TYPE}]': String()},
                        [])
        raise_for_params(request.GET, schema)

        queryset = ArticleModel.objects.order_by('id')

        if 'filter[author]' in request.GET:
            user_id = request.GET['filter[author]']
            User._get_user(user_id)
            queryset = queryset.filter(author_id=user_id)

        if 'filter[category]' in request.GET:
            category = Category._get_category(request.GET['filter[category]'])
            queryset = queryset.filter(categories=category)

        page = int(request.GET.get('page', '1'))
        start = (page - 1) * 10
        end = start + 10

        include_author = request.GET.get('include') == "author"
        if include_author:
            queryset = queryset.select_related('author')

        result = {'data': queryset[start:end]}

        if include_author:
            result['included'] = sorted(
                set((User(article.author) for article in queryset[start:end])),
                key=lambda a: a.obj.id,
            )

        if page > 1:
            result.setdefault('links', {})['previous'] = {'page': page - 1}
        if queryset.count() > end:
            result.setdefault('links', {})['next'] = {'page': page + 1}

        return result

    @classmethod
    def get_author(cls, request, obj_id):
        article = cls._get_article(obj_id)
        result = User.get_one(request, article.author_id)
        if not isinstance(result, Mapping):
            result = {'data': result}
        result['data'] = User(result['data'])
        return result

    @classmethod
    def change_author(cls, request, obj_id):
        body = get_body(request)
        schema = Object({'data': Object({'type': String(User.TYPE),
                                         'id': String()})})
        raise_for_body(body, schema)

        article = cls._get_article(obj_id)
        user = User._get_user(body['data']['id'])

        if user.id != article.author_id:
            article.author = user
            article.save()

    @classmethod
    def get_categories(cls, request, obj_id):
        return cls.map_to_method(request, Category, 'get_many',
                                 {'filter[article]': obj_id})

    @classmethod
    def reset_categories(cls, request, obj_id):
        article, categories = cls._get_categories(request, obj_id)
        article.categories.set(categories)

    @classmethod
    def add_categories(cls, request, obj_id):
        article, categories = cls._get_categories(request, obj_id)
        article.categories.add(*categories)

    @classmethod
    def remove_categories(cls, request, obj_id):
        article, categories = cls._get_categories(request, obj_id)
        article.categories.remove(*categories)

    @classmethod
    def _get_categories(cls, request, obj_id):
        body = get_body(request)
        schema = Object({'data': {
            'type': "array",
            'items': Object({'type': String(Category.TYPE), 'id': String()}),
        }})
        raise_for_body(body, schema)

        article = cls._get_article(obj_id)

        requested_ids = set((category['id'] for category in body['data']))
        categories = CategoryModel.objects.filter(id__in=requested_ids)
        found_ids = set((str(category.id) for category in categories))
        errors = [NotFound(f"Category with id '{category_id}' not found")
                  for category_id in requested_ids - found_ids]
        if errors:
            raise DjsonApiExceptionMulti(*errors)

        return article, categories

    @classmethod
    def serialize(cls, obj):
        return {'id': str(obj.id),
                'attributes': {'slug': obj.slug,
                               'title': obj.title,
                               'content': obj.content},
                'relationships': {'author': User(obj.author_id),
                                  'categories': {}}}

    @classmethod
    def _get_article(cls, obj_id, queryset=ArticleModel.objects):
        try:
            return queryset.get(id=obj_id)
        except ArticleModel.DoesNotExist:
            raise NotFound(f"Article with id '{obj_id}' not found")


class User(djsonapi.Resource):
    TYPE = "users"

    @classmethod
    def get_one(cls, request, obj_id):
        schema = Object({f'fields[{cls.TYPE}]': String()}, required=[])
        raise_for_params(request.GET, schema)
        return cls._get_user(obj_id)

    @classmethod
    def edit_one(cls, request, obj_id):
        body = get_body(request)

        schema = Object({'data': Object({
            'type': String(cls.TYPE),
            'id': String(pattern=r'^\d+$'),
            'attributes': Object({'username': String(),
                                  'first_name': String(),
                                  'last_name': String()},
                                 required=[],
                                 minProperties=1),
        })})
        raise_for_body(body, schema)

        user = cls._get_user(obj_id)

        for key, value in body['data']['attributes'].items():
            setattr(user, key, value)
        user.save()

        return user

    @classmethod
    def delete_one(cls, request, obj_id):
        count, _ = UserModel.objects.filter(id=obj_id).delete()
        if count == 0:
            cls._raise_not_found(obj_id)

    @classmethod
    def get_many(cls, request):
        schema = Object({'filter[username]': String(),
                         'page': String(pattern=r'^\d+$'),
                         f'fields[{cls.TYPE}]': String()},
                        required=[])
        raise_for_params(request.GET, schema)

        queryset = UserModel.objects.order_by('username')

        if 'filter[username]' in request.GET:
            queryset = queryset.\
                filter(username=request.GET['filter[username]'])

        page = int(request.GET.get('page', "1"))
        start = (page - 1) * 10
        end = start + 10

        result = {'data': queryset[start:end]}

        if page > 1:
            result.setdefault('links', {})['previous'] = {'page' - 1}
        if queryset.count() > end:
            result.setdefault('links', {})['next'] = {'page' + 1}

        return result

    @classmethod
    def create_one(cls, request):
        body = get_body(request)
        schema = Object({'data': Object({
            'type': String(cls.TYPE),
            'attributes': Object(
                {'username': String(),
                 'password': String(),
                 'first_name': String(),
                 'last_name': String()},
                required=['username', 'password'],
            )
        })})
        raise_for_body(body, schema)

        attributes = dict(body['data']['attributes'])
        if UserModel.objects.filter(username=attributes['username']).exists():
            raise Conflict(f"User with username '{attributes['username']}' "
                           f"already exists")

        password = attributes.pop('password')
        user = UserModel(**attributes)
        user.set_password(password)
        user.save()

        return user

    @classmethod
    def get_articles(cls, request, obj_id):
        return cls.map_to_method(request, Article, 'get_many',
                                 {'filter[author]': obj_id})

    @classmethod
    def serialize(cls, obj):
        return {'id': str(obj.id),
                'attributes': {'username': obj.username,
                               'first_name': obj.first_name,
                               'last_name': obj.last_name},
                'relationships': {'articles': {}}}

    @classmethod
    def _raise_not_found(cls, obj_id):
        raise NotFound(f"User with id '{obj_id}' not found")

    @classmethod
    def _get_user(cls, obj_id):
        try:
            return UserModel.objects.get(id=obj_id)
        except UserModel.DoesNotExist:
            cls._raise_not_found(obj_id)


class Category(djsonapi.Resource):
    TYPE = "categories"

    @classmethod
    def get_one(cls, request, obj_id):
        schema = Object({f'fields[{cls.TYPE}]': String()}, required=[])
        raise_for_params(request.GET, schema)
        return cls._get_category(obj_id)

    @classmethod
    def edit_one(cls, request, obj_id):
        body = get_body(request)
        schema = Object({'data': Object({
            'type': String(cls.TYPE),
            'id': String(),
            'attributes': Object({'slug': String(),
                                  'name': String()},
                                 minProperties=1),
        })})
        raise_for_body(body, schema)

        category = cls._get_category(obj_id)
        attributes = dict(body['data']['attributes'])

        if ('slug' in attributes and
                attributes['slug'] != category.slug and
                CategoryModel.objects.
                filter(slug=attributes['slug']).exists()):
            raise Conflict(f"Category with slug '{attributes['slug']}' "
                           f"already exists")

        for key, value in attributes.items():
            setattr(category, key, value)
        category.save()

        return category

    @classmethod
    def delete_one(cls, request, obj_id):
        count, _ = CategoryModel.objects.filter(id=obj_id).delete()
        if count == 0:
            raise NotFound(f"Category with id '{obj_id}' not found")

    @classmethod
    def get_many(cls, request):
        schema = Object({'page': String(pattern=r'^\d+$'),
                         'filter[slug]': String(),
                         'filter[name]': String(),
                         'filter[article]': String(),
                         f'fields[{cls.TYPE}]': String()},
                        required=[])
        raise_for_params(request.GET, schema)

        queryset = CategoryModel.objects.order_by('slug')

        if 'filter[slug]' in request.GET:
            queryset = queryset.filter(slug=request.GET['filter[slug]'])
        if 'filter[name]' in request.GET:
            queryset = queryset.filter(name=request.GET['filter[name]'])
        if 'filter[article]' in request.GET:
            article = Article._get_article(request.GET['filter[article]'])
            queryset = queryset.filter(articles=article)

        page = int(request.GET.get('page', "1"))
        start = (page - 1) * 10
        end = start + 10
        result = {'data': queryset[start:end],
                  'meta': {'count': queryset.count()}}

        if page > 1:
            result.setdefault('links', {})['previous'] = {'page': page - 1}
        if queryset.count() > end:
            result.setdefault('links', {})['next'] = {'page': page + 1}

        return result

    @classmethod
    def create_one(cls, request):
        body = get_body(request)
        schema = Object({'data': Object({
            'type': String(cls.TYPE),
            'attributes': Object({'slug': String(), 'name': String()}),
        })})
        raise_for_body(body, schema)

        attributes = body['data']['attributes']
        slug = attributes['slug']
        if CategoryModel.objects.filter(slug=slug).exists():
            raise Conflict(f"Category with slug '{slug}' already exists")

        return CategoryModel.objects.create(**attributes)

    @classmethod
    def get_articles(cls, request, obj_id):
        return cls.map_to_method(request, Article, 'get_many',
                                 {'filter[category]': obj_id})

    @classmethod
    def reset_articles(cls, request, obj_id):
        category, articles = cls._get_articles(request, obj_id)
        category.articles.set(articles)

    @classmethod
    def add_articles(cls, request, obj_id):
        category, articles = cls._get_articles(request, obj_id)
        category.articles.add(*articles)

    @classmethod
    def remove_articles(cls, request, obj_id):
        category, articles = cls._get_articles(request, obj_id)
        category.articles.remove(*articles)

    @classmethod
    def _get_articles(cls, request, obj_id):
        body = get_body(request)
        schema = Object({'data': {
            'type': "array",
            'items': Object({'type': String(Article.TYPE), 'id': String()}),
        }})
        raise_for_body(body, schema)

        category = cls._get_category(obj_id)

        requested_ids = set((article['id'] for article in body['data']))
        articles = ArticleModel.objects.filter(id__in=requested_ids)
        found_ids = set((str(article.id) for article in articles))
        errors = [NotFound(f"Article with id '{article_id}' not found")
                  for article_id in requested_ids - found_ids]
        if errors:
            raise DjsonApiExceptionMulti(*errors)

        return category, articles

    @classmethod
    def serialize(cls, obj):
        return {
            'id': str(obj.id),
            'attributes': {'slug': obj.slug, 'name': obj.name},
            'relationships': {'articles': {}},
        }

    @classmethod
    def _get_category(cls, obj_id):
        try:
            return CategoryModel.objects.get(id=obj_id)
        except CategoryModel.DoesNotExist:
            raise NotFound(f"Category with id '{obj_id}' not found")
