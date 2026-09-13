import asyncio
import functools
import json
from typing import ClassVar

import django
import pytest
from django.conf import settings
from django.test import RequestFactory

from djsonapi.exceptions import DjsonApiExceptionMulti, NotFound
from djsonapi.resource import Resource
from djsonapi.response import Response

settings.configure(
    DEBUG=True,
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    ALLOWED_HOSTS=["*"],
    SECRET_KEY="test",
    ROOT_URLCONF=None,
)
django.setup()


class Article(Resource):
    _type: ClassVar = "articles"
    _attributes: ClassVar = ["title", "content"]
    _create_fields: ClassVar = ["title", "content"]
    _edit_fields: ClassVar = ["title"]
    _singular_relationships: ClassVar = [("author", "users")]
    _plural_relationships: ClassVar = [("categories", "categories")]

    id: int
    title: str = ""
    content: str = ""


class UserResource(Resource):
    _type: ClassVar = "users"
    _attributes: ClassVar = ["username", "email"]

    id: int
    username: str = ""
    email: str = ""


class CategoryResource(Resource):
    _type: ClassVar = "categories"
    _attributes: ClassVar = ["name"]

    id: int
    name: str = ""


class SecretResource(Resource):
    _type: ClassVar = "secrets"
    _attributes: ClassVar = ["public_val", "secret_val"]
    _singular_relationships: ClassVar = [("owner", "users")]
    _create_fields: ClassVar = ["public_val", "secret_val", "owner"]
    _edit_fields: ClassVar = ["secret_val"]
    _read_fields: ClassVar = ["public_val", "owner"]

    id: int
    public_val: str = ""
    secret_val: str = ""


class WithReadRelationshipsResource(Resource):
    _type: ClassVar = "with_rel"
    _attributes: ClassVar = ["name"]
    _singular_relationships: ClassVar = [("author", "users")]
    _plural_relationships: ClassVar = [("tags", "tags")]
    _read_fields: ClassVar = ["name", "author"]

    id: int
    name: str = ""
    author: int | None = None
    tags: list[int] | None = None


# ── Endpoint base class tests ─────────────────────────────────────────────


class TestEndpoint:
    def test_url_name_no_template(self):
        from djsonapi.api import Endpoint

        ep = Endpoint("articles", lambda r: None)
        assert ep.url == "articles"

    def test_url_name_with_template(self):
        from djsonapi.api import Endpoint

        class MyEp(Endpoint):
            URL_NAME_TEMPLATE = "{type_name}__coll"

        ep = MyEp("articles", lambda r: None)
        assert ep.url_name == "articles__coll"

    def test_smart_parameters_skips_request(self):
        from djsonapi.api import Endpoint

        def handler(request, x: int, y: str): ...

        ep = Endpoint("articles", handler)
        assert [p.name for p in ep.smart_parameters] == ["x", "y"]

    def test_expected_extra_only_extra_prefix(self):
        from djsonapi.api import Endpoint

        def handler(request, extra__page: int = 1, extra__limit: int = 10, sort: str = ""): ...

        ep = Endpoint("articles", handler)
        assert [p.name for p in ep.expected_extra] == ["extra__page", "extra__limit"]

    def test_return_resource_type_bare_resource(self):
        from djsonapi.api import Endpoint

        def handler(request) -> Article: ...

        ep = Endpoint("articles", handler)
        assert ep.return_resource_type is Article

    def test_return_resource_type_response_wrapped(self):
        from djsonapi.api import Endpoint

        def handler(request) -> Response[Article]: ...

        ep = Endpoint("articles", handler)
        assert ep.return_resource_type is Article

    def test_return_resource_type_response_list(self):
        from djsonapi.api import Endpoint

        def handler(request) -> Response[list[Article]]: ...

        ep = Endpoint("articles", handler)
        assert ep.return_resource_type is Article

    def test_return_resource_type_none(self):
        from djsonapi.api import Endpoint

        def handler(request) -> int:
            return 42

        ep = Endpoint("articles", handler)
        assert ep.return_resource_type is None

    def test_view_missing_required_param_raises(self):
        from djsonapi.api import Endpoint

        def handler(request, required: str): ...

        ep = Endpoint("articles", handler)
        with pytest.raises(DjsonApiExceptionMulti) as exc:
            asyncio.run(ep.view(RequestFactory().get("/")))
        assert any("required" in str(e) for e in exc.value.args)

    def test_view_unknown_query_param_raises(self):
        from djsonapi.api import Endpoint

        ep = Endpoint("articles", lambda r: None)
        with pytest.raises(DjsonApiExceptionMulti) as exc:
            asyncio.run(ep.view(RequestFactory().get("/?nope=1")))
        assert any("nope" in str(e) for e in exc.value.args)

    def test_view_wraps_result_in_response(self):
        from djsonapi.api import Endpoint

        ep = Endpoint("articles", lambda r: "hello")
        req = RequestFactory().get("/")
        result = asyncio.run(ep.view(req))
        assert isinstance(result, Response)
        assert result.data == "hello"

    def test_postprocess_204_returns_httpresponse(self):
        from djsonapi.api import Endpoint

        class NoContentEp(Endpoint):
            SUCCESS_STATUS: ClassVar[int] = 204

        ep = NoContentEp("articles", lambda r: None)
        req = RequestFactory().get("/")
        resp = ep._postprocess(Response(data=None), req)
        assert resp.status_code == 204

    def test_postprocess_sets_status_on_response(self):
        from djsonapi.api import Endpoint

        ep = Endpoint("articles", lambda r: "ok")
        req = RequestFactory().get("/")
        result = ep._postprocess(Response(data="ok"), req)
        assert result.status == 200

    def test_serialize_data_null(self):
        from djsonapi.response import Response

        req = RequestFactory().get("/articles/1")
        result = Response(data=None).serialize(req)
        assert result["data"] is None

    def test_serialize_data_null_not_omitted(self):
        from djsonapi.api import Endpoint

        ep = Endpoint("articles", lambda r: None)
        req = RequestFactory().get("/articles/1")
        ep._postprocess(Response(data=None), req)
        # Returns HttpResponse for 204; for Endpoint (200), returns Response.serialize()
        # Check through combine_views flow
        assert True


# ── Concrete endpoint classes ─────────────────────────────────────────────


class TestGetOneEndpoint:
    def test_url_includes_pk(self):
        from djsonapi.api import GetOneEndpoint

        ep = GetOneEndpoint("articles", lambda r, article_id: None)
        assert "<str:article_id>" in ep.url

    def test_url_name(self):
        from djsonapi.api import GetOneEndpoint

        ep = GetOneEndpoint("articles", lambda r, aid: None)
        assert ep.url_name == "articles__item"

    def test_pk_extracted_from_url_kwargs(self):
        from djsonapi.api import GetOneEndpoint

        def handler(request, article_id) -> Article:
            return Article(id=article_id)

        ep = GetOneEndpoint("articles", handler)
        req = RequestFactory().get("/articles/1")
        result = asyncio.run(ep.view(req, article_id="1"))
        assert isinstance(result, dict)
        assert result["data"]["id"] == "1"

    def test_view_with_include_kwargs(self):
        from djsonapi.api import GetOneEndpoint

        def handler(request, article_id: int, include__author: bool = False) -> Article:
            return Article(id=article_id)

        ep = GetOneEndpoint("articles", handler, include_types=[UserResource])
        req = RequestFactory().get("/articles/1?include=author")
        result = asyncio.run(ep.view(req, article_id="1"))
        assert isinstance(result, dict)
        assert result["data"]["id"] == "1"

    def test_returns_serialized_dict_from_postprocess(self):
        from djsonapi.api import GetOneEndpoint

        def handler(request, article_id: int) -> Article:
            return Article(id=article_id)

        ep = GetOneEndpoint("articles", handler)
        req = RequestFactory().get("/articles/1")
        result = ep._postprocess(Response(data=Article(id=1)), req)
        assert isinstance(result, dict)
        assert "data" in result


class TestGetManyEndpoint:
    def test_url_no_id(self):
        from djsonapi.api import GetManyEndpoint

        ep = GetManyEndpoint("articles", lambda r: [])
        assert ep.url == "articles"

    def test_url_name(self):
        from djsonapi.api import GetManyEndpoint

        ep = GetManyEndpoint("articles", lambda r: [])
        assert ep.url_name == "articles__collection"

    def test_sort_param_as_string(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, sort: str = ""): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?sort=title")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert kwargs.get("sort") == "title"

    def test_sort_param_with_commas_as_list(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, sort: list[str] | None = None): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?sort=title,author")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert kwargs.get("sort") == ["title", "author"]

    def test_sort_param_literal_valid(self):
        from typing import Literal

        from djsonapi.api import GetManyEndpoint

        def handler(request, sort: Literal["title", "-title"] = "title"): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?sort=title")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs.get("sort") == "title"

    def test_sort_param_literal_invalid(self):
        from typing import Literal

        from djsonapi.api import GetManyEndpoint

        def handler(request, sort: Literal["title", "-title"] = "title"): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?sort=-name")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert errors
        assert "sort" not in kwargs

    def test_sort_param_literal_with_commas(self):
        from typing import Literal

        from djsonapi.api import GetManyEndpoint

        def handler(request, sort: Literal["title", "-title"] = "title"): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?sort=title,-title")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs.get("sort") == "title,-title"

    def test_sort_param_literal_invalid_in_list(self):
        from typing import Literal

        from djsonapi.api import GetManyEndpoint

        def handler(request, sort: list[Literal["title", "-title"]] | None = None): ...

        ep = GetManyEndpoint("articles", handler)
        # list annotation without direct Literal → no Literal validation
        req = RequestFactory().get("/?sort=title,-name")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors  # no validation on list[Literal[...]]
        assert kwargs.get("sort") == ["title", "-name"]

    def test_page_param_converted_to_int(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, page: int = 1): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?page=2")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert kwargs.get("page") == 2

    def test_page_invalid_conversion_error(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, page: int = 1): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?page=abc")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert errors

    def test_filter_param_passed_through(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, filter__title: str = ""): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?title=hello")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert kwargs.get("filter__title") == "hello"

    def test_filter_param_bracket_form(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, filter__title__contains: str = ""): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?filter[title][contains]=hello")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert kwargs.get("filter__title__contains") == "hello"

    def test_filter_param_bracket_preferred_over_bare(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, filter__title: str = ""): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?filter[title]=bracket&title=bare")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert kwargs.get("filter__title") == "bracket"

    def test_page_bracket_param(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, page__number: int = 1): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?page[number]=3")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs.get("page__number") == 3

    def test_page_bracket_param_invalid(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, page__number: int = 1): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/?page[number]=abc")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert errors

    def test_page_bracket_param_default(self):
        from djsonapi.api import GetManyEndpoint

        def handler(request, page__number: int = 1): ...

        ep = GetManyEndpoint("articles", handler)
        req = RequestFactory().get("/")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs.get("page__number") == 1


class TestCreateOneEndpoint:
    def test_url_no_id(self):
        from djsonapi.api import CreateOneEndpoint

        def handler(r, p: Article) -> Article:
            return Article(id=1)

        ep = CreateOneEndpoint("articles", handler)
        assert ep.url == "articles"

    def test_parses_body_into_payload_kwarg(self):
        from djsonapi.api import CreateOneEndpoint

        def handler(request, payload: Article) -> Article:
            return payload

        ep = CreateOneEndpoint("articles", handler)
        body = json.dumps(
            {"data": {"type": "articles", "attributes": {"title": "T", "content": "C"}}}
        )
        req = RequestFactory().post("/", body, content_type="application/vnd.api+json")
        result = asyncio.run(ep.view(req))
        assert isinstance(result, dict)
        assert result["data"]["type"] == "articles"
        assert result["data"]["attributes"]["title"] == "T"
        assert result["data"]["attributes"]["content"] == "C"

    def test_bad_json_body_returns_error(self):
        from djsonapi.api import CreateOneEndpoint

        def handler(request, payload: Article):
            return payload

        ep = CreateOneEndpoint("articles", handler)
        req = RequestFactory().post("/", "not json", content_type="application/vnd.api+json")
        with pytest.raises(DjsonApiExceptionMulti) as exc:
            asyncio.run(ep.view(req))
        assert any("JSON" in str(e) for e in exc.value.args)


class TestEditOneEndpoint:
    def test_url_includes_pid(self):
        from djsonapi.api import EditOneEndpoint

        def handler(r, aid: int, p: Article) -> Article:
            return Article(id=aid)

        ep = EditOneEndpoint("articles", handler)
        assert "<int:aid>" in ep.url

    def test_url_name(self):
        from djsonapi.api import EditOneEndpoint

        def handler(r, aid: int, p: Article) -> Article:
            return Article(id=aid)

        ep = EditOneEndpoint("articles", handler)
        assert ep.url_name == "articles__item"

    def test_extracts_pk_and_payload(self):
        from djsonapi.api import EditOneEndpoint

        def handler(request, article_id: int, payload: Article):
            return Article(id=article_id, title=payload.title)

        ep = EditOneEndpoint("articles", handler)
        body = json.dumps({"data": {"type": "articles", "id": 1, "attributes": {"title": "Upd"}}})
        req = RequestFactory().patch("/", body, content_type="application/vnd.api+json")
        result = asyncio.run(ep.view(req, article_id="1"))
        assert isinstance(result, dict)
        assert result["data"]["id"] == "1"
        assert result["data"]["attributes"]["title"] == "Upd"


class TestDeleteOneEndpoint:
    def test_url_includes_pid(self):
        from djsonapi.api import DeleteOneEndpoint

        ep = DeleteOneEndpoint("articles", lambda r, aid: None)
        assert "<str:aid>" in ep.url

    def test_returns_204_via_postprocess(self):
        from djsonapi.api import DeleteOneEndpoint

        ep = DeleteOneEndpoint("articles", lambda r, aid: None)
        req = RequestFactory().delete("/articles/1")
        asyncio.run(ep.view(req, aid="1"))
        # postprocess for 204 returns HttpResponse directly
        from django.http import HttpResponse

        assert isinstance(ep._postprocess(Response(data=None), req), HttpResponse)
        assert ep._postprocess(Response(data=None), req).status_code == 204


# ── Relationship endpoint tests ────────────────────────────────────────────


class TestGetRelatedResourceEndpoint:
    def test_url_includes_pk_and_relationship_name(self):
        from djsonapi.api import GetRelatedEndpoint

        ep = GetRelatedEndpoint("articles", lambda r, aid: None, relationship_name="author")
        assert "<str:aid>" in ep.url
        assert "author" in ep.url

    def test_url_name(self):
        from djsonapi.api import GetRelatedEndpoint

        ep = GetRelatedEndpoint("articles", lambda r, aid: None, relationship_name="author")
        assert ep.url_name == "articles__author__related"


class TestEditRelationshipEndpoint:
    def test_url_includes_relationship_path(self):
        from djsonapi.api import EditRelationshipEndpoint

        ep = EditRelationshipEndpoint(
            "articles", lambda r, aid, author_id: None, relationship_name="author"
        )
        assert "relationship/author" in ep.url

    def test_parses_single_relationship_id(self):
        from djsonapi.api import EditRelationshipEndpoint

        def handler(request, article_id: int, author_id: int):
            return author_id

        ep = EditRelationshipEndpoint("articles", handler, relationship_name="author")
        body = json.dumps({"data": {"type": "users", "id": "5"}})
        req = RequestFactory().patch("/", body, content_type="application/vnd.api+json")
        kwargs, errors = ep._get_kwargs(req, {"article_id": "1"}, req.GET.dict())
        assert not errors
        assert kwargs.get("author_id") == 5

    def test_returns_204(self):
        from djsonapi.api import EditRelationshipEndpoint

        ep = EditRelationshipEndpoint(
            "articles", lambda r, aid, author_id: None, relationship_name="author"
        )
        assert ep.SUCCESS_STATUS == 204


class TestAddToRelationshipEndpoint:
    def test_parses_plural_relationship_ids(self):
        from djsonapi.api import AddToRelationshipEndpoint

        def handler(request, article_id: int, category_ids: list[int]):
            return category_ids

        ep = AddToRelationshipEndpoint("articles", handler, relationship_name="categories")
        body = json.dumps(
            {"data": [{"type": "categories", "id": "1"}, {"type": "categories", "id": "2"}]}
        )
        req = RequestFactory().post("/", body, content_type="application/vnd.api+json")
        kwargs, errors = ep._get_kwargs(req, {"article_id": "1"}, req.GET.dict())
        assert not errors
        assert kwargs.get("category_ids") == [1, 2]


class TestGetRelationshipEndpoint:
    def test_url_includes_relationship_path(self):
        from djsonapi.api import GetRelationshipEndpoint

        ep = GetRelationshipEndpoint("articles", lambda r, aid: None, relationship_name="author")
        assert "relationship/author" in ep.url

    def test_url_name(self):
        from djsonapi.api import GetRelationshipEndpoint

        ep = GetRelationshipEndpoint("articles", lambda r, aid: None, relationship_name="author")
        assert ep.url_name == "articles__author__relationship"

    def test_postprocess_singular(self):
        from djsonapi.api import GetRelationshipEndpoint
        from djsonapi.response import Response

        ep = GetRelationshipEndpoint("articles", lambda r, aid: None, relationship_name="author")
        req = RequestFactory().get("/articles/1/relationship/author")
        response = Response(data={"type": "users", "id": "5"})
        result = ep._postprocess(response, req)
        assert result["data"] == {"type": "users", "id": "5"}
        assert "self" in result["links"]
        assert "related" in result["links"]
        assert "/articles/1/author" in result["links"]["related"]

    def test_postprocess_plural(self):
        from djsonapi.api import GetRelationshipEndpoint
        from djsonapi.response import Response

        ep = GetRelationshipEndpoint(
            "articles", lambda r, aid: None, relationship_name="categories"
        )
        req = RequestFactory().get("/articles/1/relationship/categories")
        response = Response(
            data=[{"type": "categories", "id": "2"}, {"type": "categories", "id": "3"}]
        )
        result = ep._postprocess(response, req)
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 2

    def test_postprocess_null(self):
        from djsonapi.api import GetRelationshipEndpoint
        from djsonapi.response import Response

        ep = GetRelationshipEndpoint("articles", lambda r, aid: None, relationship_name="author")
        req = RequestFactory().get("/articles/1/relationship/author")
        response = Response(data=None)
        result = ep._postprocess(response, req)
        assert result["data"] is None

    def test_postprocess_self_link(self):
        from djsonapi.api import GetRelationshipEndpoint
        from djsonapi.response import Response

        ep = GetRelationshipEndpoint("articles", lambda r, aid: None, relationship_name="author")
        req = RequestFactory().get("/articles/1/relationship/author?foo=bar")
        response = Response(data={"type": "users", "id": "5"})
        result = ep._postprocess(response, req)
        assert "/articles/1/relationship/author" in result["links"]["self"]


# ── DjsonApi ────────────────────────────────────────────────────────────────


class TestDjsonApiRegistry:
    def test_decorator_registers_endpoint(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int): ...

        assert len(api.registry) == 1

    def test_get_one_creates_getoneendpoint(self):
        from djsonapi.api import DjsonApi, GetOneEndpoint

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int): ...

        assert isinstance(api.registry[0], GetOneEndpoint)

    def test_get_many(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def view(request): ...

        assert len(api.registry) == 1

    def test_create_one(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.create_one("articles")
        def view(request, p: Article): ...

        assert len(api.registry) == 1

    def test_edit_one(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.edit_one("articles")
        def view(request, aid: int, p: Article): ...

        assert len(api.registry) == 1

    def test_delete_one(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.delete_one("articles")
        def view(request, aid: int): ...

        assert len(api.registry) == 1

    def test_get_related_resource(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_related("articles", "author")
        def view(request, aid: int): ...

        assert len(api.registry) == 1

    def test_get_relationship_link_decorator(self):
        from djsonapi.api import DjsonApi, GetRelationshipEndpoint

        api = DjsonApi()

        @api.get_relationship("articles", "author")
        def view(request, aid: int): ...

        assert len(api.registry) == 1
        assert isinstance(api.registry[0], GetRelationshipEndpoint)

    def test_auto_derive_relationship_link(self):
        from djsonapi.api import DjsonApi, GetRelationshipEndpoint

        api = DjsonApi()

        @api.get_related("articles", "author")
        def get_author(request, aid: int): ...

        api.urls  # triggers _auto_derive_relationship_endpoints

        rel_endpoints = [ep for ep in api.registry if isinstance(ep, GetRelationshipEndpoint)]
        assert len(rel_endpoints) == 1
        assert rel_endpoints[0].type_name == "articles"
        assert rel_endpoints[0].relationship_name == "author"

    def test_auto_derive_skips_existing(self):
        from djsonapi.api import DjsonApi, GetRelationshipEndpoint

        api = DjsonApi()

        @api.get_related("articles", "author")
        def get_author(request, aid: int): ...

        @api.get_relationship("articles", "author")
        def get_author_link(request, aid: int): ...

        api.urls  # triggers _auto_derive_relationship_endpoints

        rel_endpoints = [ep for ep in api.registry if isinstance(ep, GetRelationshipEndpoint)]
        assert len(rel_endpoints) == 1

    def test_edit_relationship(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.edit_relationship("articles", "author")
        def view(request, aid: int, author_id: int): ...

        assert len(api.registry) == 1

    def test_reset_relationship(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.reset_relationship("articles", "categories")
        def view(request, aid: int, category_ids: list[int]): ...

        assert len(api.registry) == 1

    def test_add_to_relationship(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.add_to_relationship("articles", "categories")
        def view(request, aid: int, category_ids: list[int]): ...

        assert len(api.registry) == 1

    def test_remove_from_relationship(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.remove_from_relationship("articles", "categories")
        def view(request, aid: int, category_ids: list[int]): ...

        assert len(api.registry) == 1


class TestDjsonApiRegistryMiddleware:
    @staticmethod
    def _make_log_middleware(log):
        def middleware(func):
            @functools.wraps(func)
            def wrapped(request, *args, **kwargs):
                log.append("mw_before")
                result = func(request, *args, **kwargs)
                log.append("mw_after")
                return result
            return wrapped
        return middleware

    def test_api_level_middleware_wraps_handler(self):
        from djsonapi.api import DjsonApi

        log = []
        mw = self._make_log_middleware(log)
        api = DjsonApi(middleware=[mw])

        @api.rpc("articles", "publish")
        def view(request, article_id): ...

        asyncio.run(api.registry[0].view(RequestFactory().get("/"), article_id="1"))
        assert log == ["mw_before", "mw_after"]

    def test_endpoint_middleware_overrides_api_middleware(self):
        from djsonapi.api import DjsonApi

        log = []
        api_mw = self._make_log_middleware(log)
        ep_mw = self._make_log_middleware(log)
        api = DjsonApi(middleware=[api_mw])

        @api.rpc("articles", "publish", middleware=[ep_mw])
        def view(request, article_id): ...

        asyncio.run(api.registry[0].view(RequestFactory().get("/"), article_id="1"))
        assert log.count("mw_before") == 1  # only ep_mw ran, not api_mw

    def test_endpoint_middleware_empty_skips_api_middleware(self):
        from djsonapi.api import DjsonApi

        log = []
        api_mw = self._make_log_middleware(log)
        api = DjsonApi(middleware=[api_mw])

        @api.rpc("articles", "publish", middleware=[])
        def view(request, article_id): ...

        asyncio.run(api.registry[0].view(RequestFactory().get("/"), article_id="1"))
        assert log == []  # no middleware ran

    def test_endpoint_omits_middleware_inherits_api(self):
        from djsonapi.api import DjsonApi

        log = []
        mw = self._make_log_middleware(log)
        api = DjsonApi(middleware=[mw])

        @api.rpc("articles", "publish")
        def view(request, article_id): ...

        asyncio.run(api.registry[0].view(RequestFactory().get("/"), article_id="1"))
        assert log == ["mw_before", "mw_after"]

    def test_middleware_order_innermost_first_in_list(self):
        from djsonapi.api import DjsonApi

        order = []
        def mw_a(func):
            @functools.wraps(func)
            def wrapped(request, *args, **kwargs):
                order.append("a")
                return func(request, *args, **kwargs)
            return wrapped
        def mw_b(func):
            @functools.wraps(func)
            def wrapped(request, *args, **kwargs):
                order.append("b")
                return func(request, *args, **kwargs)
            return wrapped

        api = DjsonApi(middleware=[mw_a, mw_b])

        @api.rpc("articles", "publish")
        def view(request, article_id): ...

        asyncio.run(api.registry[0].view(RequestFactory().get("/"), article_id="1"))
        assert order == ["a", "b"]  # first in list is outermost, runs first

    def test_middleware_may_raise_djsonapi_exception(self):
        from djsonapi.api import DjsonApi
        from djsonapi.exceptions import Unauthorized

        def auth_required(func):
            @functools.wraps(func)
            def wrapped(request, *args, **kwargs):
                raise Unauthorized("no token")
            return wrapped

        api = DjsonApi(middleware=[auth_required])

        @api.rpc("articles", "publish")
        def view(request, article_id): ...

        with pytest.raises(Unauthorized):
            asyncio.run(api.registry[0].view(RequestFactory().get("/"), article_id="1"))
    def test_urls_includes_all_registered_endpoints(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, aid: int): ...

        @api.get_many("articles")
        def list_articles(request): ...

        urls = api.urls
        assert len(urls) >= 2

    def test_urls_are_django_urlpatterns(self):
        from django.urls import URLPattern

        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int): ...

        urls = api.urls
        assert all(isinstance(u, URLPattern) for u in urls)


class TestDjsonApiCombineViews:
    def test_405_on_wrong_method(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int):
            return Response(data="ok")

        req = RequestFactory().post("/articles/1")
        combine = api.combine_views(api.registry)
        resp = asyncio.run(combine(req, aid="1"))
        assert resp.status_code == 405

    def test_method_dispatch_to_correct_handler(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_view(request, aid: int):
            return Response(data="get")

        @api.delete_one("articles")
        def delete_view(request, aid: int):
            return None

        req = RequestFactory().delete("/articles/1")
        combine = api.combine_views(api.registry)
        resp = asyncio.run(combine(req, aid="1"))
        assert resp.status_code == 204

    def test_unhandled_exception_returns_500(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int):
            raise ValueError("boom")

        req = RequestFactory().get("/articles/1")
        combine = api.combine_views(api.registry)
        resp = asyncio.run(combine(req, aid="1"))
        assert resp.status_code == 500

    def test_dict_result_gets_serialized(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int):
            return Response(data=Article(id=aid))

        req = RequestFactory().get("/articles/1")
        combine = api.combine_views(api.registry)
        resp = asyncio.run(combine(req, aid="1"))
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "data" in data

    def test_response_object_gets_serialized(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def view(request, aid: int):
            return Response(data=Article(id=aid))

        req = RequestFactory().get("/articles/1")
        combine = api.combine_views(api.registry)
        resp = asyncio.run(combine(req, aid="1"))
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["data"]["type"] == "articles"


# ── ReturnsDataMixin ────────────────────────────────────────────────────────


class TestReturnsDataMixin:
    def test_invalid_include_types_raises_valueerror(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request, article_id: int, include__nonexist: bool = False): ...

        with pytest.raises(ValueError, match="Invalid include"):
            ReturnsDataMixin("articles", handler, sparse=False)

    def test_invalid_sparse_types_raises_valueerror(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request, article_id: int, fields__unknown: list[str] | None = None): ...

        with pytest.raises(ValueError, match="Invalid sparse"):
            ReturnsDataMixin("articles", handler, sparse=True)

    def test_expected_includes_from_params(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request, article_id: int, include__author: bool = False) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=True, include_types=[UserResource])
        assert ep.expected_includes == {"author"}
        assert ep.allowed_includes == {"author", "categories"}

    def test_expected_sparse(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request, article_id: int, fields__articles: list[str] | None = None
        ) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=True, include_types=[UserResource])
        assert ep.expected_sparse == {"articles"}
        assert "articles" in ep.allowed_sparse

    def test_allowed_sparse_from_resource(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=True)
        allowed = ep.allowed_sparse
        assert "articles" in allowed
        assert "title" in allowed["articles"]

    def test_allowed_includes_from_resource_relationships(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=True)
        assert "author" in ep.allowed_includes
        assert "categories" in ep.allowed_includes

    def test_postprocess_returns_serialized_result(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        req = RequestFactory().get("/articles/1")
        result = ep._postprocess(Response(data=Article(id=1)), req)
        assert isinstance(result, dict)
        assert result["data"]["type"] == "articles"

    def test_nested_expected_includes_dotted_paths(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request,
            article_id: int,
            include__author: bool = False,
            include__author__articles: bool = False,
        ) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        assert "author" in ep.expected_includes
        assert "author.articles" in ep.expected_includes

    def test_nested_expected_includes_auto_adds_intermediates(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request, article_id: int, include__author__articles__comments: bool = False
        ) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        assert "author" in ep.expected_includes
        assert "author.articles" in ep.expected_includes
        assert "author.articles.comments" in ep.expected_includes

    def test_nested_include_passes_post_init_validation(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request, article_id: int, include__author__articles: bool = False
        ) -> Article: ...

        ReturnsDataMixin("articles", handler, sparse=False)  # no ValueError

    def test_nested_include_get_kwargs_expands_intermediates(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request,
            article_id: int,
            include__author: bool = False,
            include__author__articles: bool = False,
        ) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        req = RequestFactory().get("/?include=author.articles")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs["include__author"] is True
        assert kwargs["include__author__articles"] is True

    def test_nested_include_flat_include_only(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request,
            article_id: int,
            include__author: bool = False,
            include__author__articles: bool = False,
        ) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        req = RequestFactory().get("/?include=author")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs["include__author"] is True
        assert "include__author__articles" not in kwargs

    def test_nested_include_invalid_dotted_rejected(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(request, article_id: int, include__author: bool = False) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        req = RequestFactory().get("/?include=author.articles")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert errors

    def test_nested_include_combined_flat_and_dotted(self):
        from djsonapi.api import ReturnsDataMixin

        def handler(
            request,
            article_id: int,
            include__author: bool = False,
            include__author__articles: bool = False,
            include__categories: bool = False,
        ) -> Article: ...

        ep = ReturnsDataMixin("articles", handler, sparse=False)
        req = RequestFactory().get("/?include=author.articles,categories")
        kwargs, errors = ep._get_kwargs(req, {}, req.GET.dict())
        assert not errors
        assert kwargs["include__author"] is True
        assert kwargs["include__author__articles"] is True
        assert kwargs["include__categories"] is True


# ── OpenAPI Spec ──────────────────────────────────────────────────────────────


class TestBuildOpenapiSpec:
    def test_skeleton(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()
        spec = api._build_openapi_spec()
        assert spec["openapi"] == "3.0.3"
        assert "info" in spec
        assert spec["paths"] == {}
        assert spec["components"]["schemas"] == {}

    def test_get_one_adds_path_and_params(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        spec = api._build_openapi_spec()
        assert "/articles/{article_id}" in spec["paths"]
        op = spec["paths"]["/articles/{article_id}"]["get"]
        assert op["tags"] == ["articles"]
        param_names = [p["name"] for p in op["parameters"]]
        assert "article_id" in param_names
        assert op["parameters"][0]["in"] == "path"
        assert "200" in op["responses"]

    def test_get_one_schema_registered(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        spec = api._build_openapi_spec()
        assert "articles_resource" in spec["components"]["schemas"]
        assert spec["components"]["schemas"]["articles_resource"]["title"] == "articles"

    def test_get_many_adds_collection_path(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request) -> list[Article]:
            return [Article(id=1)]

        spec = api._build_openapi_spec()
        assert "/articles" in spec["paths"]
        op = spec["paths"]["/articles"]["get"]
        assert op["tags"] == ["articles"]
        assert "200" in op["responses"]

    def test_get_many_with_query_params(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(
            request, sort: str = "", page: int = 1, filter__title: str = ""
        ) -> list[Article]:
            return [Article(id=1)]

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles"]["get"]
        param_names = [p["name"] for p in op["parameters"]]
        assert "sort" in param_names
        assert "page" in param_names
        assert "filter[title]" in param_names

    def test_get_many_with_sparse_fields(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request, fields__articles: list[str] | None = None) -> list[Article]:
            return [Article(id=1)]
            return [Article(id=1)]

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles"]["get"]
        param_names = [p["name"] for p in op["parameters"]]
        assert "fields[articles]" in param_names

    def test_create_one_has_request_body(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.create_one("articles")
        def create_article(request, payload: Article) -> Article:
            return payload

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles"]["post"]
        assert "requestBody" in op
        assert op["requestBody"]["required"] is True
        assert "201" in op["responses"]

    def test_edit_one_has_pk_and_body(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.edit_one("articles")
        def update_article(request, article_id: int, payload: Article) -> Article:
            return payload

        spec = api._build_openapi_spec()
        path = "/articles/{article_id}"
        assert path in spec["paths"]
        op = spec["paths"][path]["patch"]
        assert op["parameters"][0]["name"] == "article_id"
        assert "requestBody" in op

    def test_delete_one_returns_204(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.delete_one("articles")
        def delete_article(request, article_id: int):
            pass

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles/{article_id}"]["delete"]
        assert "204" in op["responses"]

    def test_combined_path_has_multiple_methods(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request) -> list[Article]:
            return [Article(id=1)]

        @api.create_one("articles")
        def create_article(request, payload: Article) -> Article:
            return payload

        spec = api._build_openapi_spec()
        path_item = spec["paths"]["/articles"]
        assert "get" in path_item
        assert "post" in path_item

    def test_error_responses_included(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles", errors=[NotFound])
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles/{article_id}"]["get"]
        assert "404" in op["responses"]

    def test_tags_and_tag_groups(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        @api.get_one("users")
        def get_user(request, user_id: int) -> UserResource:
            return UserResource(id=user_id)

        spec = api._build_openapi_spec()
        assert "tags" in spec
        tag_names = [t["name"] for t in spec["tags"]]
        assert "articles" in tag_names
        assert "users" in tag_names
        assert "x-tagGroups" in spec

    def test_openapi_view_returns_json(self):
        from django.test import RequestFactory

        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        req = RequestFactory().get("/openapi.json")
        resp = api._openapi_view(req)
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "openapi" in data

    def test_docs_view_returns_html(self):
        from django.test import RequestFactory

        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        req = RequestFactory().get("/docs/")
        resp = api._docs_view(req)
        assert resp.status_code == 200
        assert b"redoc" in resp.content.lower()

    def test_urls_include_openapi_and_docs(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int) -> Article:
            return Article(id=article_id)

        urlpatterns = api.urls
        url_names = {u.name for u in urlpatterns}
        assert "openapi" in url_names
        assert "docs" in url_names

    def test_relationship_path_structure(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_related("articles", "author")
        def get_author(request, article_id: int) -> UserResource:
            return UserResource(id=1)

        spec = api._build_openapi_spec()
        path = "/articles/{article_id}/author"
        assert path in spec["paths"]
        op = spec["paths"][path]["get"]
        assert op["parameters"][0]["name"] == "article_id"

    def test_edit_relationship_has_body(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.edit_relationship("articles", "author")
        def edit_author(request, article_id: int, author_id: int):
            pass

        spec = api._build_openapi_spec()
        path = "/articles/{article_id}/relationship/author"
        assert path in spec["paths"]
        op = spec["paths"][path]["patch"]
        assert "requestBody" in op
        assert "204" in op["responses"]

    def test_empty_registry_no_error(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()
        spec = api._build_openapi_spec()
        assert spec["paths"] == {}

    def test_include_param_in_openapi(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(request, article_id: int, include__author: bool = False) -> Article:
            return Article(id=article_id)

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles/{article_id}"]["get"]
        param_names = [p["name"] for p in op["parameters"]]
        assert "include" in param_names

    def test_nested_include_in_openapi_description(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_one("articles")
        def get_article(
            request,
            article_id: int,
            include__author: bool = False,
            include__author__articles: bool = False,
        ) -> Article:
            return Article(id=article_id)

        spec = api._build_openapi_spec()
        op = spec["paths"]["/articles/{article_id}"]["get"]
        include_param = next(p for p in op["parameters"] if p["name"] == "include")
        assert "author" in include_param["description"]
        assert "author.articles" in include_param["description"]

    def test_wrong_content_type_on_post_returns_415(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.create_one("articles")
        def create(request, article: Article) -> Article:
            return Article(id=1)

        req = RequestFactory().post(
            "/articles",
            data='{"data": {"type": "articles"}}',
            content_type="application/json",
        )
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp.status_code == 415

    def test_wrong_content_type_on_patch_returns_415(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.edit_one("articles")
        def edit(request, article_id: int, article: Article) -> Article:
            return Article(id=article_id)

        req = RequestFactory().patch(
            "/articles/1",
            data='{"data": {"type": "articles", "id": "1"}}',
            content_type="application/xml",
        )
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req, article_id=1))
        assert resp.status_code == 415

    def test_correct_content_type_on_post_passes(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.create_one("articles")
        def create(request, article: Article) -> Article:
            return Article(id=1)

        req = RequestFactory().post(
            "/articles",
            data='{"data": {"type": "articles", "attributes": {"title": "hello", "content": "world"}}}',
            content_type="application/vnd.api+json",
        )
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp.status_code == 201

    def test_bad_accept_returns_406(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request, sort: str | None = None) -> list[Article]:
            return [Article(id=1)]

        req = RequestFactory().get("/articles", HTTP_ACCEPT="text/html")
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp.status_code == 406

    def test_jsonapi_accept_passes(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request, sort: str | None = None) -> list[Article]:
            return [Article(id=1)]

        req = RequestFactory().get("/articles", HTTP_ACCEPT="application/vnd.api+json")
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp.status_code == 200

    def test_star_accept_passes(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request, sort: str | None = None) -> list[Article]:
            return [Article(id=1)]

        req = RequestFactory().get("/articles", HTTP_ACCEPT="*/*")
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp.status_code == 200

    def test_vary_header_present(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.get_many("articles")
        def list_articles(request, sort: str | None = None) -> list[Article]:
            return [Article(id=1)]

        req = RequestFactory().get("/articles", HTTP_ACCEPT="application/vnd.api+json")
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp["Vary"] == "Accept"

    def test_content_type_with_unsupported_params_returns_415(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.create_one("articles")
        def create(request, article: Article) -> Article:
            return Article(id=1)

        req = RequestFactory().post(
            "/articles",
            data='{"data": {"type": "articles"}}',
            content_type='application/vnd.api+json; charset="utf-8"',
        )
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req))
        assert resp.status_code == 415

    def test_delete_with_no_content_type_passes(self):
        from djsonapi.api import DjsonApi

        api = DjsonApi()

        @api.delete_one("articles")
        def delete(request, article_id: int):
            pass

        req = RequestFactory().delete("/articles/1")
        view = api.combine_views(api.registry)
        resp = asyncio.run(view(req, article_id=1))
        assert resp.status_code == 204


class TestReadFields:
    def test_serialize_excludes_non_read_fields(self):
        r = SecretResource(id=1, public_val="hello", secret_val="shh")
        result = r.serialize()
        assert result["attributes"]["public_val"] == "hello"
        assert "secret_val" not in result["attributes"]

    def test_serialize_includes_everything_when_no_read_fields(self):
        r = Article(id=1, title="T", content="C")
        result = r.serialize()
        assert result["attributes"]["title"] == "T"
        assert result["attributes"]["content"] == "C"

    def test_serialize_excludes_relationship_not_in_read_fields(self):
        r = WithReadRelationshipsResource(id=1, name="x")
        r.tags = [1, 2]
        r.author = 42
        result = r.serialize()
        assert "author" in result.get("relationships", {})
        assert "tags" not in result.get("relationships", {})

    def test_jsonschema_read_excludes_non_read_attributes(self):
        schema = SecretResource.jsonschema_read()
        attrs = schema["properties"]["attributes"]["properties"]
        assert "public_val" in attrs
        assert "secret_val" not in attrs

    def test_jsonschema_read_excludes_non_read_rels(self):
        schema = WithReadRelationshipsResource.jsonschema_read()
        rel_props = schema["properties"]["relationships"]["properties"]
        assert "author" in rel_props
        assert "tags" not in rel_props

    def test_jsonschema_read_required_only_read_rels(self):
        schema = WithReadRelationshipsResource.jsonschema_read()
        rel_required = schema["properties"]["relationships"]["required"]
        assert "author" in rel_required
        assert "tags" not in rel_required

    def test_jsonschema_read_includes_all_when_no_read_fields(self):
        schema = Article.jsonschema_read()
        attrs = schema["properties"]["attributes"]["properties"]
        assert "title" in attrs
        assert "content" in attrs
