import pytest
from django.contrib.auth.models import User
from django.test import Client

from articles.models import Article, Category


client = Client()


class Fixtures:
    """ Usage:

            >>> # Make fixture generators
            >>> UserFixtures = Fixtures(User, username="user-{}", ...)
            >>> ArticleFixtures = Fixtures(Article, slug="article-{}", ...)

            >>> # Create a new user, formatting their strings with '1'
            >>> user = UserFixtures[1]

            >>> # Create 2 articles, formatting their strings with '1' and '2'
            >>> # respectively, with the newly created user both as their
            >>> # author
            >>> article = ArticleFixtures.add_extra(author=user)[1:3]
    """

    def __init__(self, model, extra=None, **kwargs):
        self.model = model
        self.kwargs = kwargs

    def add_extra(self, **kwargs):
        return self.__class__(self.model, **{**kwargs, **self.kwargs})

    def _get(self, i):
        result = {}
        for key, value in self.kwargs.items():
            try:
                value = value.format(i)
            except Exception:
                pass
            result[key] = value
        return self.model.objects.create(**result)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start = index.start or 1
            stop = index.stop
            step = index.step or 1
            return [self._get(i) for i in range(start, stop, step)]
        else:
            return self._get(index)


ArticleFixtures = Fixtures(Article,
                           slug="article-{}",
                           title="Article {}",
                           content="Content of article {}")
UserFixtures = Fixtures(User,
                        username="author-{}",
                        first_name="Author{}",
                        last_name="Authoropoulos{}")
CategoryFixtures = Fixtures(Category, slug="category-{}", name="Category {}")


@pytest.mark.django_db
def test_get_one_article():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.get(f"/articles/{article.id}")
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': article.slug,
                           'title': article.title,
                           'content': article.content},
            'relationships': {
                'author': {
                    'data': {'type': "users", 'id': str(author.id)},
                    'links': {
                        'related': f"/articles/{article.id}/author",
                        'self': f"/articles/{article.id}/relationships/author",
                    },
                },
                'categories': {
                    'links': {
                        'related': f"/articles/{article.id}/categories",
                        'self': (f"/articles/{article.id}/relationships/"
                                 f"categories"),
                    },
                },
            },
            'links': {'self': f"/articles/{article.id}"},
        },
        'links': {'self': f"/articles/{article.id}"},
    }


@pytest.mark.django_db
def test_get_one_article_not_found():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.get(f"/articles/{article.id + 1}")
    assert response.status_code == 404
    assert response.json() == {'errors': [{
        'status': "404",
        'code': "not_found",
        'title': "Not found",
        'detail': f"Article with id '{article.id + 1}' not found",
    }]}


def test_get_one_article_invalid_params():
    response = client.get('/articles/1', {'a': "b", 'include': "not author"})
    assert response.status_code == 400
    response_body = response.json()
    assert set(response_body.keys()) == {'errors'}
    assert len(response_body['errors']) == 2


@pytest.mark.django_db
def test_get_one_article_with_include():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.get(f"/articles/{article.id}", {'include': "author"})
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': article.slug,
                           'title': article.title,
                           'content': article.content},
            'relationships': {
                'author': {
                    'data': {'type': "users", 'id': str(author.id)},
                    'links': {
                        'related': f"/articles/{article.id}/author",
                        'self': f"/articles/{article.id}/relationships/author",
                    },
                },
                'categories': {
                    'links': {
                        'related': f"/articles/{article.id}/categories",
                        'self': (f"/articles/{article.id}/relationships/"
                                 f"categories"),
                    },
                },
            },
            'links': {'self': f"/articles/{article.id}"},
        },
        'included': [{
            'type': "users",
            'id': str(author.id),
            'attributes': {'username': author.username,
                           'first_name': author.first_name,
                           'last_name': author.last_name},
            'relationships': {
                'articles': {
                    'links': {'related': f"/users/{author.id}/articles"},
                },
            },
            'links': {'self': f"/users/{author.id}"},
        }],
        'links': {'self': f"/articles/{article.id}?include=author"},
    }


@pytest.mark.django_db
def test_edit_one():
    author1, author2 = UserFixtures[1:3]
    article = ArticleFixtures.add_extra(author=author1)[1]
    response = client.patch(
        f"/articles/{article.id}",
        {'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': article.slug + " (edited)",
                           'title': article.title + " (edited)",
                           'content': article.content + " (edited)"},
            'relationships': {'author': {'data': {'type': "users",
                                                  'id': str(author2.id)}}},
        }},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': article.slug + " (edited)",
                           'title': article.title + " (edited)",
                           'content': article.content + " (edited)"},
            'relationships': {
                'author': {
                    'data': {'type': "users", 'id': str(author2.id)},
                    'links': {
                        'related': f"/articles/{article.id}/author",
                        'self': f"/articles/{article.id}/relationships/author",
                    },
                },
                'categories': {
                    'links': {
                        'related': f"/articles/{article.id}/categories",
                        'self': (f"/articles/{article.id}/relationships/"
                                 f"categories"),
                    },
                },
            },
            'links': {'self': f"/articles/{article.id}"},
        },
        'included': [{
            'type': "users",
            'id': str(author2.id),
            'attributes': {'username': author2.username,
                           'first_name': author2.first_name,
                           'last_name': author2.last_name},
            'relationships': {
                'articles': {
                    'links': {'related': f"/users/{author2.id}/articles"},
                },
            },
            'links': {'self': f"/users/{author2.id}"},
        }],
        'links': {'self': f"/articles/{article.id}"},
    }
    assert (list(Article.objects.
                 filter(id=article.  id).
                 values_list('slug', 'title', 'content', 'author_id')) ==
            [(article.slug + " (edited)",
              article.title + " (edited)",
              article.content + " (edited)",
              author2.id)])


@pytest.mark.django_db
def test_delete_one():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.delete(f"/articles/{article.id}")
    assert response.status_code == 204
    assert not response.content
    assert not Article.objects.filter(id=article.id).exists()


@pytest.mark.django_db
def test_create_one_article():
    author = UserFixtures[1]
    response = client.post(
        "/articles",
        {'data': {
            'type': "articles",
            'attributes': {'slug': "article-1",
                           'title': "Article 1",
                           'content': "Content of article 1"},
            'relationships': {'author': {'data': {'type': "users",
                                                  'id': str(author.id)}}},
        }},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    article = Article.objects.get()
    assert response['Location'] == f"/articles/{article.id}"
    assert article.slug == "article-1"
    assert article.title == "Article 1"
    assert article.content == "Content of article 1"
    assert response['Location'] == f"/articles/{article.id}"
    assert response.json() == {
        'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': article.slug,
                           'title': article.title,
                           'content': article.content},
            'relationships': {
                'author': {
                    'data': {'type': "users", 'id': str(author.id)},
                    'links': {
                        'related': f"/articles/{article.id}/author",
                        'self': f"/articles/{article.id}/relationships/author",
                    },
                },
                'categories': {
                    'links': {
                        'related': f"/articles/{article.id}/categories",
                        'self': (f"/articles/{article.id}/relationships/"
                                 f"categories"),
                    },
                },
            },
            'links': {'self': f"/articles/{article.id}"},
        },
        'included': [{
            'type': "users",
            'id': str(author.id),
            'attributes': {'username': author.username,
                           'first_name': author.first_name,
                           'last_name': author.last_name},
            'relationships': {
                'articles': {
                    'links': {'related': f"/users/{author.id}/articles"},
                },
            },
            'links': {'self': f"/users/{author.id}"},
        }],
        'links': {'self': f"/articles/{article.id}"},
    }


@pytest.mark.django_db
def test_create_one_article_bad_request():
    response = client.post('/articles', "hello world",
                           content_type="text/plain")
    assert response.status_code == 400
    response_body = response.json()
    assert set(response_body.keys()) == {'errors'}
    assert len(response_body['errors']) == 1
    error = response_body['errors'][0]
    assert error['status'] == "400"
    assert error['code'] == "bad_request"
    assert error['title'] == "Bad request"

    response = client.post('/articles', {'data': {'type': "article"}},
                           content_type="application/json")
    assert response.status_code == 400
    response_body = response.json()
    assert set(response_body.keys()) == {'errors'}
    assert len(response_body['errors']) == 3


@pytest.mark.django_db
def test_create_one_articleuser_not_found():
    response = client.post(
        "/articles",
        {'data': {
            'type': "articles",
            'attributes': {'slug': "article-1",
                           'title': "Article 1",
                           'content': "Content of article 1"},
            'relationships': {'author': {'data': {'type': "users",
                                                  'id': "1"}}},
        }},
        content_type="application/json",
    )
    assert response.status_code == 404
    assert response.json() == {'errors': [{
        'status': "404",
        'code': "not_found",
        'title': "Not found",
        'detail': "User with id '1' not found",
    }]}


@pytest.mark.django_db
def test_create_one_article_preexisting_slug():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.post(
        "/articles",
        {'data': {
            'type': "articles",
            'attributes': {'slug': article.slug,
                           'title': "Article 2",
                           'content': "Content of article 2"},
            'relationships': {'author': {'data': {'type': "users",
                                                  'id': str(author.id)}}},
        }},
        content_type="application/json",
    )
    assert response.status_code == 409
    response_body = response.json()
    assert set(response_body.keys()) == {'errors'}
    assert len(response_body['errors']) == 1
    error = response_body['errors'][0]
    assert error['status'] == "409"
    assert error['code'] == "conflict"
    assert error['title'] == "Conflict"
    assert (error['detail'] ==
            f"Article with slug '{article.slug}' already exists")


@pytest.mark.django_db
def test_create_one_articleautogenerate_slug():
    author = UserFixtures[1]
    ArticleFixtures.add_extra(author=author)[1]
    response = client.post(
        "/articles",
        {'data': {
            'type': "articles",
            'attributes': {'title': "foo",
                           'content': "Content of article 2"},
            'relationships': {'author': {'data': {'type': "users",
                                                  'id': str(author.id)}}},
        }},
        content_type="application/json",
    )
    assert response.status_code == 201
    new_article = Article.objects.get(id=response.json()['data']['id'])
    assert new_article.title == "foo"
    assert new_article.content == "Content of article 2"
    assert new_article.slug == "foo"
    assert new_article.author == author


@pytest.mark.django_db
def test_create_one_articleautogenerate_slug_avoid_conflict():
    author = UserFixtures[1]
    old_article = ArticleFixtures.add_extra(author=author)[1]
    response = client.post(
        "/articles",
        {'data': {
            'type': "articles",
            'attributes': {'title': old_article.slug,
                           'content': "Content of article 2"},
            'relationships': {'author': {'data': {'type': "users",
                                                  'id': str(author.id)}}},
        }},
        content_type="application/json",
    )
    assert response.status_code == 201
    new_article = Article.objects.get(id=response.json()['data']['id'])
    assert new_article.title == old_article.slug
    assert new_article.content == "Content of article 2"
    assert new_article.slug == old_article.slug + "-1"
    assert new_article.author == author


@pytest.mark.django_db
def test_get_many_articles():
    author = UserFixtures[1]
    articles = ArticleFixtures.add_extra(author=author)[1:4]
    response = client.get("/articles")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"},
            }
            for article in articles
        ],
        'links': {'self': "/articles"},
    }


@pytest.mark.django_db
def test_get_many_articles_filter_author():
    author1, author2 = UserFixtures[1:3]
    ArticleFixtures.add_extra(author=author1)[1:3]
    articles2 = ArticleFixtures.add_extra(author=author2)[3:6]
    response = client.get(f"/articles?filter[author]={author2.id}")
    assert response.status_code == 200
    assert len(response.json()['data']) == 3
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author2.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"},
            }
            for article in articles2
        ],
        'links': {'self': f"/articles?filter[author]={author2.id}"},
    }


@pytest.mark.django_db
def test_get_many_articles_filter_author_not_found():
    response = client.get("/articles?filter[author]=1")
    assert response.status_code == 404
    assert response.json() == {'errors': [{
        'status': "404",
        'code': "not_found",
        'title': "Not found",
        'detail': "User with id '1' not found",
    }]}


@pytest.mark.django_db
def test_get_many_articles_pagination():
    author = UserFixtures[1]
    articles = ArticleFixtures.add_extra(author=author)[1:24]

    response = client.get('/articles')
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles[:10]
        ],
        'links': {'self': "/articles", 'next': "/articles?page=2"},
    }

    response = client.get('/articles?page=2')
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"},
            }
            for article in articles[10:20]
        ],
        'links': {'previous': "/articles?page=1",
                  'self': "/articles?page=2",
                  'next': "/articles?page=3"},
    }

    response = client.get('/articles?page=3')
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"},
            }
            for article in articles[20:]
        ],
        'links': {'previous': "/articles?page=2",
                  'self': "/articles?page=3"},
    }


@pytest.mark.django_db
def test_get_many_articles_include_author():
    author1, author2 = UserFixtures[1:3]
    articles1 = ArticleFixtures.add_extra(author=author1)[1:4]
    articles2 = ArticleFixtures.add_extra(author=author2)[4:6]

    response = client.get('/articles?include=author')
    assert response.status_code == 200
    assert response.json() == {
        'data': ([
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author1.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"},
            } for article in articles1
        ] + [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author2.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles2]),
        'included': [
            {
                'type': "users",
                'id': str(author1.id),
                'attributes': {'username': author1.username,
                               'first_name': author1.first_name,
                               'last_name': author1.last_name},
                'relationships': {
                    'articles': {
                        'links': {'related': f"/users/{author1.id}/articles"},
                    },
                },
                'links': {'self': f"/users/{author1.id}"},
            },
            {
                'type': "users",
                'id': str(author2.id),
                'attributes': {'username': author2.username,
                               'first_name': author2.first_name,
                               'last_name': author2.last_name},
                'relationships': {
                    'articles': {
                        'links': {'related': f"/users/{author2.id}/articles"},
                    },
                },
                'links': {'self': f"/users/{author2.id}"},
            },
        ],
        'links': {'self': "/articles?include=author"},
    }


@pytest.mark.django_db
def test_get_author():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.get(f"/articles/{article.id}/author")
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "users",
            'id': str(author.id),
            'attributes': {'username': author.username,
                           'first_name': author.first_name,
                           'last_name': author.last_name},
            'relationships': {
                'articles': {
                    'links': {'related': f"/users/{author.id}/articles"},
                },
            },
            'links': {'self': f"/users/{author.id}"},
        },
        'links': {'self': f"/articles/{article.id}/author"},
    }


@pytest.mark.django_db
def test_get_categories():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    categories = CategoryFixtures[:4]
    article.categories.add(*categories)
    response = client.get(f"/articles/{article.id}/categories")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "categories",
                'id': str(category.id),
                'attributes': {'slug': category.slug, 'name': category.name},
                'relationships': {
                    'articles': {
                        'links': {
                            'self': (f"/categories/{category.id}/"
                                     f"relationships/articles"),
                            'related': f"/categories/{category.id}/articles",
                        },
                    },
                },
                'links': {'self': f"/categories/{category.id}"}
            }
            for category in categories
        ],
        'links': {'self': f"/articles/{article.id}/categories"},
        'meta': {'count': 3},
    }


@pytest.mark.django_db
def test_get_categories_paginated():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    categories = sorted(CategoryFixtures[:24], key=lambda c: c.slug)
    article.categories.add(*categories)

    response = client.get(f"/articles/{article.id}/categories")
    assert response.status_code == 200, response.json()
    assert response.json() == {
        'data': [
            {
                'type': "categories",
                'id': str(category.id),
                'attributes': {'slug': category.slug, 'name': category.name},
                'relationships': {
                    'articles': {
                        'links': {
                            'self': (f"/categories/{category.id}/"
                                     f"relationships/articles"),
                            'related': f"/categories/{category.id}/articles",
                        },
                    },
                },
                'links': {'self': f"/categories/{category.id}"},
            }
            for category in categories[:10]
        ],
        'links': {'self': f"/articles/{article.id}/categories",
                  'next': f"/articles/{article.id}/categories?page=2"},
        'meta': {'count': 23},
    }

    response = client.get(f"/articles/{article.id}/categories?page=2")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "categories",
                'id': str(category.id),
                'attributes': {'slug': category.slug, 'name': category.name},
                'relationships': {
                    'articles': {
                        'links': {
                            'self': (f"/categories/{category.id}/"
                                     f"relationships/articles"),
                            'related': f"/categories/{category.id}/articles",
                        },
                    },
                },
                'links': {'self': f"/categories/{category.id}"},
            }
            for category in categories[10:20]
        ],
        'links': {'previous': f"/articles/{article.id}/categories?page=1",
                  'self': f"/articles/{article.id}/categories?page=2",
                  'next': f"/articles/{article.id}/categories?page=3"},
        'meta': {'count': 23},
    }

    response = client.get(f"/articles/{article.id}/categories?page=3")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "categories",
                'id': str(category.id),
                'attributes': {'slug': category.slug,
                               'name': category.name},
                'relationships': {
                    'articles': {
                        'links': {
                            'self': (f"/categories/{category.id}/"
                                     f"relationships/articles"),
                            'related': f"/categories/{category.id}/articles",
                        },
                    },
                },
                'links': {'self': f"/categories/{category.id}"},
            }
            for category in categories[20:]
        ],
        'links': {'previous': f"/articles/{article.id}/categories?page=2",
                  'self': f"/articles/{article.id}/categories?page=3"},
        'meta': {'count': 23},
    }


@pytest.mark.django_db
def test_get_categories_article_not_found():
    response = client.get("/articles/1/categories")
    assert response.status_code == 404
    assert response.json() == {'errors': [{
        'status': "404",
        'code': "not_found",
        'title': "Not found",
        'detail': "Article with id '1' not found",
    }]}


@pytest.mark.django_db
def test_get_one_article_fields():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.get(f"/articles/{article.id}?"
                          f"fields[articles]=slug,title")
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': "article-1",
                           'title': "Article 1"},
            'links': {'self': f"/articles/{article.id}"},
        },
        'links': {
            'self': f"/articles/{article.id}?fields[articles]=slug,title",
        },
    }


@pytest.mark.django_db
def test_get_one_article_fields_of_included():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    response = client.get(f"/articles/{article.id}?"
                          f"include=author&"
                          f"fields[users]=username,last_name")
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "articles",
            'id': str(article.id),
            'attributes': {'slug': article.slug,
                           'title': article.title,
                           'content': article.content},
            'relationships': {
                'author': {
                    'data': {'type': "users", 'id': str(author.id)},
                    'links': {
                        'related': f"/articles/{article.id}/author",
                        'self': f"/articles/{article.id}/relationships/author",
                    },
                },
                'categories': {
                    'links': {
                        'related': f"/articles/{article.id}/categories",
                        'self': (f"/articles/{article.id}/relationships/"
                                 f"categories"),
                    },
                },
            },
            'links': {'self': f"/articles/{article.id}"},
        },
        'included': [{'type': "users",
                      'id': str(author.id),
                      'attributes': {'username': author.username,
                                     'last_name': author.last_name},
                      'links': {'self': f"/users/{author.id}"}}],
        'links': {
            'self': (f"/articles/{article.id}?"
                     f"include=author&"
                     f"fields[users]=username,last_name"),
        },
    }


@pytest.mark.django_db
def test_inline_plural_relationship():
    author = UserFixtures[1]
    articles = sorted(ArticleFixtures.add_extra(author=author)[1:4],
                      key=lambda a: a.slug)
    category = CategoryFixtures[1]
    category.articles.add(*articles)
    response = client.get(f"/categories/{category.id}")
    assert response.status_code == 200
    assert response.json() == {
        'data': {
            'type': "categories",
            'id': str(category.id),
            'attributes': {'slug': category.slug, 'name': category.name},
            'relationships': {
                'articles': {
                    'links': {
                        'self': (f"/categories/{category.id}/relationships/"
                                 f"articles"),
                        'related': f"/categories/{category.id}/articles",
                    },
                },
            },
            'links': {'self': f"/categories/{category.id}"},
        },
        'links': {'self': f"/categories/{category.id}"}
    }


@pytest.mark.django_db
def test_map_to_method():
    author = UserFixtures[1]
    articles = sorted(ArticleFixtures.add_extra(author=author)[1:4],
                      key=lambda a: a.id)
    response = client.get(f"/users/{author.id}/articles")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles
        ],
        'links': {'self': f"/users/{author.id}/articles"}
    }


@pytest.mark.django_db
def test_map_to_method_with_include():
    author = UserFixtures[1]
    articles = sorted(ArticleFixtures.add_extra(author=author)[1:4],
                      key=lambda a: a.id)
    response = client.get(f"/users/{author.id}/articles?include=author")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles
        ],
        'included': [{
            'type': "users",
            'id': str(author.id),
            'attributes': {'username': author.username,
                           'first_name': author.first_name,
                           'last_name': author.last_name},
            'relationships': {
                'articles': {
                    'links': {'related': f"/users/{author.id}/articles"},
                },
            },
            'links': {'self': f"/users/{author.id}"},
        }],
        'links': {'self': f"/users/{author.id}/articles?include=author"}
    }


@pytest.mark.django_db
def test_map_to_method_with_pagination():
    author = UserFixtures[1]
    articles = sorted(ArticleFixtures.add_extra(author=author)[1:24],
                      key=lambda a: a.id)

    response = client.get(f"/users/{author.id}/articles")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles[:10]
        ],
        'links': {'self': f"/users/{author.id}/articles",
                  'next': f"/users/{author.id}/articles?page=2"}
    }

    response = client.get(f"/users/{author.id}/articles?page=2")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles[10:20]
        ],
        'links': {'previous': f"/users/{author.id}/articles?page=1",
                  'self': f"/users/{author.id}/articles?page=2",
                  'next': f"/users/{author.id}/articles?page=3"}
    }

    response = client.get(f"/users/{author.id}/articles?page=3")
    assert response.status_code == 200
    assert response.json() == {
        'data': [
            {
                'type': "articles",
                'id': str(article.id),
                'attributes': {'slug': article.slug,
                               'title': article.title,
                               'content': article.content},
                'relationships': {
                    'author': {
                        'data': {'type': "users", 'id': str(author.id)},
                        'links': {
                            'related': f"/articles/{article.id}/author",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"author"),
                        },
                    },
                    'categories': {
                        'links': {
                            'related': f"/articles/{article.id}/categories",
                            'self': (f"/articles/{article.id}/relationships/"
                                     f"categories"),
                        },
                    },
                },
                'links': {'self': f"/articles/{article.id}"}
            }
            for article in articles[20:]
        ],
        'links': {'previous': f"/users/{author.id}/articles?page=2",
                  'self': f"/users/{author.id}/articles?page=3"}
    }


@pytest.mark.django_db
def test_change_author():
    author1, author2 = UserFixtures[1:3]
    article = ArticleFixtures.add_extra(author=author1)[1]
    response = client.patch(f"/articles/{article.id}/relationships/author",
                            {'data': {'type': "users",
                                      'id': str(author2.id)}},
                            content_type="application/json")
    assert response.status_code == 204
    assert not response.content

    article = Article.objects.get(id=article.id)  # Reload
    assert article.author_id == author2.id


@pytest.mark.django_db
def test_add_categories():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    categories = CategoryFixtures[1:3]
    response = client.post(f"/articles/{article.id}/relationships/categories",
                           {'data': [{'type': "categories",
                                      'id': str(category.id)}
                                     for category in categories]},
                           content_type="application/json")
    assert response.status_code == 204
    assert not response.content
    assert (list(article.categories.
                 order_by('id').
                 values_list('id', flat=True)) ==
            sorted((c.id for c in categories)))


@pytest.mark.django_db
def test_remove_categories():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    categories = CategoryFixtures[1:3]
    article.categories.add(*categories)
    response = client.delete(
        f"/articles/{article.id}/relationships/categories",
        {'data': [{'type': "categories",
                   'id': str(category.id)} for category in categories]},
        content_type="application/json",
    )
    assert response.status_code == 204
    assert not response.content
    assert not article.categories.exists()


@pytest.mark.django_db
def test_reset_categories():
    author = UserFixtures[1]
    article = ArticleFixtures.add_extra(author=author)[1]
    categories = CategoryFixtures[1:3]
    response = client.patch(f"/articles/{article.id}/relationships/categories",
                            {'data': [{'type': "categories",
                                       'id': str(category.id)}
                                      for category in categories]},
                            content_type="application/json")
    assert response.status_code == 204
    assert not response.content
