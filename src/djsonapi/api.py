import asyncio
import functools
import inspect
import json
import logging
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Literal, Sequence, get_args, get_origin
from uuid import UUID

import jsonschema  # type: ignore
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import NoReverseMatch, URLPattern, reverse
from django.urls import path as django_path
from django.views.decorators.csrf import csrf_exempt

from djsonapi.exceptions import (
    BadRequest,
    DjsonApiException,
    DjsonApiExceptionMulti,
    DjsonApiExceptionSingle,
    InternalServerError,
    MethodNotAllowed,
    NotAcceptable,
    NotFound,
    UnsupportedMediaType,
    _class_name_to_title,
)
from djsonapi.resource import Resource
from djsonapi.response import Response

_PYTHON_TO_OPENAPI = {
    UUID: {"type": "string", "format": "uuid"},
    int: {"type": "integer"},
    str: {"type": "string"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}

_PATH_TYPE = {UUID: "uuid", int: "int", str: "str"}


def _path_type(pk_type: type) -> str:
    return _PATH_TYPE[pk_type]


def _type_to_openapi_schema(tp: type) -> dict:
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is list:
        item_tp = args[0] if args else str
        return {"type": "array", "items": _type_to_openapi_schema(item_tp)}
    if origin is Literal:
        return {"type": "string", "enum": list(args)}
    return _PYTHON_TO_OPENAPI.get(tp, {"type": "string"})


def _annotation_is_list(annotation: type) -> bool:
    origin = get_origin(annotation)
    if origin is list or origin is Sequence:
        return True
    if origin is not None:
        return any(
            get_origin(arg) is list or get_origin(arg) is Sequence
            for arg in get_args(annotation)
            if arg is not type(None)
        )
    return False


def _bracket_name(prefix: str, suffix: str) -> str:
    parts = suffix.split("__")
    return prefix + "[" + "][".join(parts) + "]"


def _query_param_name(name: str) -> str:
    if name.startswith("filter__"):
        return _bracket_name("filter", name[len("filter__") :])
    if name.startswith("page"):
        if "__" in name:
            _, suffix = name.split("__", 1)
            return f"page[{suffix}]"
        return "page"
    if name == "sort":
        return "sort"
    if name.startswith("include__"):
        return "include"
    if name.startswith("fields__"):
        return f"fields[{name[len('fields__') :]}]"
    if name.startswith("extra__"):
        return name[len("extra__") :]
    raise ValueError(f"Unknown query parameter: {name}")


def _pop_typed_param(
    remaining: dict[str, str],
    param: inspect.Parameter,
    *,
    query_name: str,
    bracket_fallbacks: tuple[str, ...] = (),
) -> tuple[Any, DjsonApiExceptionSingle | None]:
    """Pop one typed query parameter, converting/validating against ``param``.

    Tries ``query_name`` then any ``bracket_fallbacks``. Returns ``(value, error)``;
    ``error`` is ``None`` on success. Honors the parameter default / required-ness.
    """
    raw = remaining.pop(query_name, None)
    for fallback in bracket_fallbacks:
        if raw is None:
            raw = remaining.pop(fallback, None)
    if raw is not None:
        tp = param.annotation if param.annotation != inspect.Parameter.empty else str
        try:
            return tp(raw), None
        except ValueError as e:
            return None, BadRequest(str(e), source={"parameter": query_name})
    if param.default is inspect.Parameter.empty:
        return None, BadRequest(
            f"Missing required parameter: {query_name}", source={"parameter": query_name}
        )
    return param.default, None


def _handler_query_params(handler: Callable) -> list[dict]:
    sig = inspect.signature(handler)
    params = []

    include_suffixes = []
    for name in sig.parameters:
        if name.startswith("include__"):
            include_suffixes.append(name[len("include__") :].replace("__", "."))
    if include_suffixes:
        description = f"Allowed values: {', '.join(sorted(include_suffixes))}"
        params.append(
            {
                "name": "include",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": description,
            }
        )

    for name, param in sig.parameters.items():
        if name == "request":
            continue
        annotation = param.annotation if param.annotation != inspect.Parameter.empty else str
        if isinstance(annotation, type) and issubclass(annotation, Resource):
            continue
        if name.startswith("include__"):
            continue
        if not any(
            name.startswith(p)
            for p in ("filter__", "page", "sort", "include__", "fields__", "extra__")
        ):
            continue

        has_default = param.default is not inspect.Parameter.empty
        query_name = _query_param_name(name)
        param_obj = {
            "name": query_name,
            "in": "query",
            "required": not has_default,
            "schema": _type_to_openapi_schema(annotation),
        }
        if (
            has_default
            and param.default is not None
            and isinstance(param.default, (str, int, float, bool))
        ):
            param_obj["schema"]["default"] = param.default
        params.append(param_obj)
    return params


def _sparse_field_names(resource_class: type[Resource] | None) -> list[str]:
    if resource_class is None:
        return []
    names: list[str] = []
    if resource_class._attributes:
        names.extend(resource_class._attributes)
    if resource_class._singular_rels:
        names.extend(f for f, _ in resource_class._singular_rels)
    if resource_class._plural_rels:
        names.extend(f for f, _ in resource_class._plural_rels)
    return names


def _add_links_to_schema(schema: dict, resource_class: type[Resource]) -> None:
    rel_props = schema.get("properties", {}).get("relationships", {}).get("properties", {})
    for rel_field, _ in resource_class._singular_rels:
        if rel_field in rel_props:
            rel_props[rel_field]["properties"] = rel_props[rel_field].get("properties", {})
            rel_props[rel_field]["properties"]["links"] = {
                "type": "object",
                "properties": {
                    "related": {"type": "string", "format": "uri"},
                    "self": {"type": "string", "format": "uri"},
                },
            }
    for rel_field, _ in resource_class._plural_rels:
        if rel_field in rel_props:
            rel_props[rel_field]["properties"] = rel_props[rel_field].get("properties", {})
            rel_props[rel_field]["properties"]["links"] = {
                "type": "object",
                "properties": {
                    "related": {"type": "string", "format": "uri"},
                    "self": {"type": "string", "format": "uri"},
                },
            }


def _ensure_schema(spec: dict, type_name: str, resource_class: type[Resource]) -> None:
    schema_name = f"{type_name}_resource"
    if schema_name not in spec["components"]["schemas"]:
        schema_obj = resource_class.jsonschema_read()
        _add_links_to_schema(schema_obj, resource_class)
        schema_obj["title"] = type_name
        spec["components"]["schemas"][schema_name] = schema_obj


def _response_schema(
    type_name: str, resource_class: type[Resource], spec: dict, has_include: bool = False
) -> dict:
    _ensure_schema(spec, type_name, resource_class)
    links_schema: dict = {
        "type": "object",
        "properties": {"self": {"type": "string"}},
        "additionalProperties": {"type": "string"},
    }
    properties: dict = {
        "data": {"$ref": f"#/components/schemas/{type_name}_resource"},
        "jsonapi": {
            "type": "object",
            "properties": {"version": {"const": "1.0"}},
        },
        "links": links_schema,
    }
    if has_include:
        properties["included"] = {
            "type": "array",
            "items": {"type": "object"},
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["data", "jsonapi"],
    }


def _build_relationship_schema(rel_id_type: type, is_plural: bool) -> dict:
    if is_plural:
        args = get_args(rel_id_type)
        effective_id_type = args[0] if args else str
    else:
        non_null_args = [a for a in get_args(rel_id_type) if a is not type(None)]
        effective_id_type = non_null_args[0] if non_null_args else rel_id_type
    id_schema = _type_to_openapi_schema(effective_id_type)
    item_schema: dict = {
        "type": "object",
        "properties": {"id": id_schema, "type": {"type": "string"}},
        "required": ["id", "type"],
    }
    if is_plural:
        data_schema: dict = {"type": "array", "items": item_schema}
    else:
        data_schema = item_schema
    return {
        "type": "object",
        "properties": {"data": data_schema},
        "required": ["data"],
    }


def _django_path_to_openapi(django_path: str) -> str:
    return re.sub(r"<\w+:(\w+)>", r"{\1}", django_path)


def _merge_openapi_into(base: dict, overlay: dict) -> None:
    for key, val in overlay.items():
        if key in base:
            if isinstance(base[key], dict) and isinstance(val, dict):
                _merge_openapi_into(base[key], val)
            elif isinstance(base[key], list) and isinstance(val, list):
                base[key].extend(val)
            else:
                base[key] = val
        else:
            base[key] = val


@dataclass
class Endpoint:
    type_name: str
    handler: Callable[..., Any]
    errors: Sequence[type[DjsonApiExceptionSingle]] | None = None

    METHOD = ""
    URL_NAME_TEMPLATE = ""
    SUCCESS_STATUS: ClassVar[int] = 200

    def __post_init__(self):
        pass

    @property
    def url_name(self) -> str:
        return self.URL_NAME_TEMPLATE.format(type_name=self.type_name)

    @functools.cached_property
    def url(self):
        return f"{self.type_name}"

    @functools.cached_property
    def signature(self) -> inspect.Signature:
        return inspect.signature(self.handler)

    @functools.cached_property
    def parameters(self) -> list[inspect.Parameter]:
        return list(self.signature.parameters.values())

    @functools.cached_property
    def return_annotation(self):
        return self.signature.return_annotation

    @functools.cached_property
    def smart_parameters(self) -> list[inspect.Parameter]:
        return self.parameters[1:]

    @functools.cached_property
    def expected_extra(self) -> list[inspect.Parameter]:
        return [
            parameter
            for parameter in self.smart_parameters
            if parameter.name.startswith("extra__")
        ]

    @functools.cached_property
    def return_resource_type(self):
        """Infer Resource subclass from handler return annotation.

        Handles bare Resource, Response[Resource], Response[list[Resource]].
        Returns None if no Resource type found.
        """

        if isinstance(self.return_annotation, type) and issubclass(
            self.return_annotation, Resource
        ):
            return self.return_annotation
        origin = get_origin(self.return_annotation)
        if isinstance(origin, type) and issubclass(origin, Response):
            args = get_args(self.return_annotation)
            if not args:
                return None
            inner = args[0]
            if isinstance(inner, type) and issubclass(inner, Resource):
                return inner
            iorigin = get_origin(inner)
            if isinstance(iorigin, type) and issubclass(iorigin, list):
                iargs = get_args(inner)
                if iargs and isinstance(iargs[0], type) and issubclass(iargs[0], Resource):
                    return iargs[0]
            return None
        if isinstance(origin, type) and issubclass(origin, list):
            iargs = get_args(self.return_annotation)
            if iargs and isinstance(iargs[0], type) and issubclass(iargs[0], Resource):
                return iargs[0]
        return None

    async def view(self, request: HttpRequest, **url_kwargs: Any) -> Any:
        remaining_params = request.GET.dict()
        kwargs, errors = self._get_kwargs(request, url_kwargs, remaining_params)
        for unknown in sorted(remaining_params):
            errors.append(
                BadRequest(f"Unknown query parameter: {unknown}", source={"parameter": unknown})
            )
        remaining_params.clear()
        for param in self.smart_parameters:
            if param.default is inspect.Parameter.empty and param.name not in kwargs:
                try:
                    qp = _query_param_name(param.name)
                except ValueError:
                    qp = param.name
                errors.append(
                    BadRequest(f"Missing required parameter: {qp}", source={"parameter": qp})
                )
        if errors:
            raise DjsonApiExceptionMulti(*errors)
        if asyncio.iscoroutinefunction(self.handler):
            result = await self.handler(request, **kwargs)
        else:
            result = await sync_to_async(self.handler)(request, **kwargs)
        if not isinstance(result, Response):
            result = Response(data=result)
        return self._postprocess(result, request)

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        return {}, []

    def _openapi_operation(self) -> dict:
        return {}

    def _postprocess(self, response: Response, request: HttpRequest) -> Any:
        if self.SUCCESS_STATUS == 204:
            return HttpResponse(status=204)
        response.status = self.SUCCESS_STATUS
        return response

    def __call__(self, *args, **kwargs) -> Any:
        return self.handler(*args, **kwargs)


class ExpectsIdMixin(Endpoint):
    parameters: ClassVar[list[inspect.Parameter]]
    type_name: ClassVar[str]

    @functools.cached_property
    def pk_name(self):
        return self.parameters[1].name

    @functools.cached_property
    def pk_type(self):
        param = self.parameters[1]
        return param.annotation if param.annotation != inspect.Parameter.empty else str

    @functools.cached_property
    def url(self):
        path_type = _path_type(self.pk_type)
        return f"{self.type_name}/<{path_type}:{self.pk_name}>"

    @functools.cached_property
    def smart_parameters(self) -> list[inspect.Parameter]:
        return self.parameters[2:]

    def _openapi_operation(self) -> dict:
        result = super()._openapi_operation()
        try:
            pk_name = self.pk_name
            pk_type = self.pk_type
        except IndexError:
            return result
        _merge_openapi_into(
            result,
            {
                "parameters": [
                    {
                        "name": pk_name,
                        "in": "path",
                        "required": True,
                        "schema": _PYTHON_TO_OPENAPI.get(pk_type, {"type": "string"}),
                    }
                ]
            },
        )
        return result

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        kwargs, errors = super()._get_kwargs(request, url_kwargs, remaining_params)
        if self.pk_name not in url_kwargs:
            return kwargs, errors
        raw = url_kwargs[self.pk_name]
        if isinstance(raw, self.pk_type):
            pk_value = raw
        else:
            try:
                pk_value = self.pk_type(raw)
            except Exception:
                raise NotFound("The URL does not exist")
        kwargs[self.pk_name] = pk_value
        return kwargs, errors
        kwargs[self.pk_name] = pk_value
        return kwargs, errors


@dataclass(kw_only=True)
class ExpectsPayloadMixin(Endpoint):
    def __post_init__(self):
        super().__post_init__()
        if not (
            isinstance(self.payload_parameter.annotation, type)
            and issubclass(self.payload_parameter.annotation, Resource)
        ):
            raise ValueError(
                f"First smart parameter '{self.payload_parameter.name}' must be a Resource subclass, "
                f"got {self.payload_parameter.annotation}"
            )

    @functools.cached_property
    def payload_parameter(self) -> inspect.Parameter:
        return self.smart_parameters[0]

    def _get_payload_schema_fn(self, resource_class: type[Resource]):
        raise NotImplementedError

    def _get_payload_fields(self, resource_class: type[Resource]) -> list[str] | None:
        raise NotImplementedError

    def _openapi_operation(self) -> dict:
        result = super()._openapi_operation()
        resource_class = self.payload_parameter.annotation
        schema_fn = self._get_payload_schema_fn(resource_class)
        _merge_openapi_into(
            result,
            {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/vnd.api+json": {
                            "schema": schema_fn() if schema_fn else {"type": "object"}
                        }
                    },
                }
            },
        )
        return result

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        kwargs, errors = super()._get_kwargs(request, url_kwargs, remaining_params)
        payload, parse_errors = self._parse_body(request)
        if parse_errors:
            errors.extend(parse_errors)
        else:
            kwargs[self.payload_parameter.name] = payload
        return kwargs, errors

    def _parse_body(
        self, request: HttpRequest
    ) -> tuple[Any | None, list[DjsonApiExceptionSingle]]:
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError as e:
            return None, [BadRequest(f"Invalid JSON: {e}")]

        resource_class = self.payload_parameter.annotation
        if not (isinstance(resource_class, type) and issubclass(resource_class, Resource)):
            return None, [BadRequest("Could not determine resource type")]

        schema_fn = self._get_payload_schema_fn(resource_class)
        if schema_fn:
            try:
                jsonschema.validate(body.get("data", body), schema_fn())
            except jsonschema.ValidationError as e:
                if "data" in body:
                    parts = ["data"] + list(e.absolute_path)
                else:
                    parts = list(e.absolute_path)
                pointer = "/" + "/".join(str(p) for p in parts) if parts else "/"
                return None, [BadRequest(str(e), source={"pointer": pointer})]

        fields = self._get_payload_fields(resource_class)
        payload = resource_class._from_jsonapi_payload(body, fields=fields)
        return payload, []


@dataclass(kw_only=True)
class ReturnsDataMixin(Endpoint):
    return_annotation: ClassVar
    smart_parameters: ClassVar[Sequence[inspect.Parameter]]
    return_resource_type: ClassVar[type[Resource] | None]
    expected_extra: ClassVar[list[inspect.Parameter]]

    sparse: bool = True
    include_types: Sequence[type[Resource]] = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        flat_expected = {inc for inc in self.expected_includes if "." not in inc}
        if forbidden_includes := flat_expected - self.allowed_includes:
            raise ValueError(f"Invalid include types: {forbidden_includes}")
        if forbidden_sparse_types := self.expected_sparse - self.allowed_sparse.keys():
            raise ValueError(f"Invalid sparse types: {forbidden_sparse_types}")

    @functools.cached_property
    def expected_sparse(self) -> set[str]:
        """Sparse types that the handler function expects

        >>> @api.get_many("articles", include_types=[User, Category])
        ... def get_articles(
        ...     request: HttpRequest,
        ...     fields__articles: Sequence[str] | None = None,
        ...     fields__users: Sequence[str] | None = None,
        ...     fields__categories: Sequence[str] | None = None,
        ... ) -> Response[list[ArticleResource]: ...
        ...
        >>> get_articles.expected_sparse
        <<< {'articles', 'users', 'categories'}
        """

        return {
            parameter.name[len("fields__") :]
            for parameter in self.smart_parameters
            if parameter.name.startswith("fields__")
        }

    @functools.cached_property
    def expected_includes(self) -> set[str]:
        result = set()
        for parameter in self.smart_parameters:
            if not parameter.name.startswith("include__"):
                continue
            suffix = parameter.name[len("include__") :]
            dotted = suffix.replace("__", ".")
            parts = dotted.split(".")
            for i in range(1, len(parts) + 1):
                result.add(".".join(parts[:i]))
        return result

    @functools.cached_property
    def allowed_sparse(self) -> dict[str, set[str]]:
        """Allowed sparse types and fields based on return and include types

        >>> @api.get_many("articles", include_types=[User, Category])
        ... def get_articles(request: HttpRequest) -> Response[list[ArticleResource]]: ...

        >>> get_articles.allowed_sparse
        <<< {'articles': {'title', 'content', ...},
        ...  'users': {'username', ...},
        ...  'categories': {'name', ...}}
        """

        if not self.sparse:
            return {}

        result = {}

        if self.return_resource_type is not None:
            result[self.return_resource_type._type] = {
                *self.return_resource_type._attributes,
                *[_type for _, _type in self.return_resource_type._singular_rels],
                *[_type for _, _type in self.return_resource_type._plural_rels],
            }

        for include_type in self.include_types:
            result[include_type._type] = {
                *include_type._attributes,
                *[_type for _, _type in include_type._singular_rels],
                *[_type for _, _type in include_type._plural_rels],
            }

        return result

    @functools.cached_property
    def allowed_includes(self) -> set[str]:
        """Allowed include types based on return and include types

        >>> @api.get_many("articles", include_types=[User, Category])
        ... def get_articles(request: HttpRequest) -> Response[list[ArticleResource]]: ...

        >>> get_articles.allowed_sparse
        <<< {'articles', 'author', 'categories'}  # 'author', not 'users'
        """

        if self.return_resource_type is None:
            return set()
        return {
            *[rel for rel, _ in self.return_resource_type._singular_rels],
            *[rel for rel, _ in self.return_resource_type._plural_rels],
        }

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        kwargs, errors = super()._get_kwargs(request, url_kwargs, remaining_params or {})
        rp = remaining_params or {}

        for param in self.smart_parameters:
            if param.name in ("sort", "page"):
                raw = rp.pop(param.name, None)
                if raw is None:
                    if param.default is inspect.Parameter.empty:
                        errors.append(
                            BadRequest(
                                f"Missing required parameter: {param.name}",
                                source={"parameter": param.name},
                            )
                        )
                    else:
                        kwargs[param.name] = param.default
                elif param.name == "page":
                    try:
                        kwargs[param.name] = int(raw)
                    except ValueError:
                        errors.append(
                            BadRequest("page must be an integer", source={"parameter": "page"})
                        )
                else:
                    sort_fields = [s.strip() for s in raw.split(",") if s.strip()]
                    if get_origin(param.annotation) is Literal:
                        invalid = [f for f in sort_fields if f not in get_args(param.annotation)]
                        if invalid:
                            errors.append(
                                BadRequest(
                                    f"Invalid sort fields: {invalid}",
                                    source={"parameter": "sort"},
                                )
                            )
                            continue
                    kwargs[param.name] = (
                        sort_fields if _annotation_is_list(param.annotation) else raw
                    )
            elif param.name.startswith("filter__"):
                filter_key = param.name[len("filter__") :]
                value, error = _pop_typed_param(
                    rp,
                    param,
                    query_name=_bracket_name("filter", filter_key),
                    bracket_fallbacks=(filter_key,),
                )
                if error is not None:
                    errors.append(error)
                else:
                    kwargs[param.name] = value
            elif param.name.startswith("page__"):
                page_key = f"page[{param.name[len('page__') :]}]"
                value, error = _pop_typed_param(rp, param, query_name=page_key)
                if error is not None:
                    errors.append(error)
                else:
                    kwargs[param.name] = value

        request_includes = {r for r in rp.pop("include", "").split(",") if r}
        expanded = set()
        for inc in request_includes:
            parts = inc.split(".")
            for i in range(1, len(parts)):
                expanded.add(".".join(parts[:i]))
            expanded.add(inc)
        if forbidden_includes := expanded - self.expected_includes:
            errors.append(
                BadRequest(
                    f"Invalid include types: {forbidden_includes}", source={"parameter": "include"}
                )
            )
        for inc in expanded:
            kwargs[f"include__{inc.replace('.', '__')}"] = True

        request_sparse: dict[str, set[str]] = {}
        for key in list(rp):
            if m := re.search(r"^fields\[(\w+)\]$", key):
                request_sparse[m.group(1)] = {
                    f.strip() for f in rp.pop(key).split(",") if f.strip()
                }
        for t in sorted(request_sparse.keys() - self.allowed_sparse.keys()):
            errors.append(
                BadRequest(f"Invalid fields type: {t}", source={"parameter": f"fields[{t}]"})
            )
        for sparse_type, fields in request_sparse.items():
            if sparse_type in self.expected_sparse:
                kwargs[f"fields__{sparse_type}"] = fields

        for param in self.expected_extra:
            extra_name = param.name[len("extra__") :]
            value, error = _pop_typed_param(rp, param, query_name=extra_name)
            if error is not None:
                errors.append(error)
            else:
                kwargs[param.name] = value

        return kwargs, errors

    def _openapi_operation(self) -> dict:
        result = super()._openapi_operation()

        query_params = _handler_query_params(self.handler)
        if query_params:
            _merge_openapi_into(result, {"parameters": query_params})

        if self.sparse:
            sparse_params = []
            for type_name in sorted(self.allowed_sparse):
                field_names = _sparse_field_names(
                    next(
                        (
                            t
                            for t in [self.return_resource_type, *self.include_types]
                            if t is not None and t._type == type_name
                        ),
                        None,
                    )
                )
                desc = (
                    f"Comma-separated list of fields. Available fields: {', '.join(field_names)}."
                    if isinstance(field_names, list) and field_names
                    else "Comma-separated list of fields."
                )
                sparse_params.append(
                    {
                        "name": f"fields[{type_name}]",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": desc,
                    }
                )
            if sparse_params:
                _merge_openapi_into(result, {"parameters": sparse_params})

        if self.return_resource_type is not None:
            has_include = any(p.name.startswith("include__") for p in self.smart_parameters)
            spec_placeholder: dict = {"components": {"schemas": {}}}
            response_schema = _response_schema(
                self.return_resource_type._type,
                self.return_resource_type,
                spec_placeholder,
                has_include=has_include,
            )
            fragment: dict = {
                "responses": {
                    str(self.SUCCESS_STATUS): {
                        "description": "OK",
                        "content": {"application/vnd.api+json": {"schema": response_schema}},
                    }
                }
            }
            if spec_placeholder["components"]["schemas"]:
                fragment["components"] = spec_placeholder["components"]
            _merge_openapi_into(result, fragment)

        return result

    def _postprocess(self, response: Response, request: HttpRequest) -> dict[str, Any]:
        data = response.serialize(request)
        data.setdefault("links", {})["self"] = request.get_full_path()
        return data


@dataclass(kw_only=True)
class ExpectsRelationshipIdsMixin(Endpoint):
    def __post_init__(self):
        super().__post_init__()

    @functools.cached_property
    def ids_parameter(self) -> inspect.Parameter:
        return self.smart_parameters[0]

    def _openapi_operation(self) -> dict:
        result = super()._openapi_operation()
        annotation = self.ids_parameter.annotation
        plural = _annotation_is_list(annotation)
        schema = _build_relationship_schema(annotation, plural)
        _merge_openapi_into(
            result,
            {
                "requestBody": {
                    "required": True,
                    "content": {"application/vnd.api+json": {"schema": schema}},
                }
            },
        )
        return result

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        kwargs, errors = super()._get_kwargs(request, url_kwargs, remaining_params)

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError as e:
            return kwargs, errors + [BadRequest(f"Invalid JSON: {e}")]

        raw = body.get("data")
        if raw is None:
            return kwargs, errors + [BadRequest("Missing 'data' in request body")]

        annotation = self.ids_parameter.annotation
        plural = _annotation_is_list(annotation)

        if plural:
            if not isinstance(raw, list):
                return kwargs, errors + [
                    BadRequest("Expected array of resource identifier objects")
                ]
            inner_type = get_args(annotation)[0] if get_args(annotation) else str
            try:
                kwargs[self.ids_parameter.name] = [inner_type(item["id"]) for item in raw]
            except (KeyError, TypeError, ValueError) as e:
                return kwargs, errors + [BadRequest(f"Invalid resource identifier: {e}")]
        else:
            item_type = annotation if annotation is not inspect.Parameter.empty else str
            if not isinstance(raw, dict):
                return kwargs, errors + [BadRequest("Expected single resource identifier object")]
            try:
                kwargs[self.ids_parameter.name] = item_type(raw["id"])
            except (KeyError, TypeError, ValueError) as e:
                return kwargs, errors + [BadRequest(f"Invalid resource identifier: {e}")]

        return kwargs, errors


@dataclass(kw_only=True)
class ExpectsRelationshipMixin(ExpectsIdMixin):
    relationship_name: str

    URL_NAME_TEMPLATE: ClassVar[str]

    @property
    def url_name(self):
        return self.URL_NAME_TEMPLATE.format(
            type_name=self.type_name, relationship_name=self.relationship_name
        )

    @functools.cached_property
    def url(self):
        path_type = _path_type(self.pk_type)
        return (
            f"{self.type_name}/<{path_type}:{self.pk_name}>/relationship/{self.relationship_name}"
        )


@dataclass
class GetOneEndpoint(ReturnsDataMixin, ExpectsIdMixin, Endpoint):
    METHOD = "GET"
    URL_NAME_TEMPLATE = "{type_name}__item"


@dataclass
class EditOneEndpoint(ReturnsDataMixin, ExpectsIdMixin, ExpectsPayloadMixin, Endpoint):
    METHOD = "PATCH"
    URL_NAME_TEMPLATE = "{type_name}__item"

    def _get_payload_schema_fn(self, resource_class: type[Resource]):
        return resource_class.jsonschema_edit

    def _get_payload_fields(self, resource_class: type[Resource]) -> list[str] | None:
        return resource_class._edit_fields


@dataclass
class DeleteOneEndpoint(ExpectsIdMixin, Endpoint):
    METHOD = "DELETE"
    URL_NAME_TEMPLATE = "{type_name}__item"
    SUCCESS_STATUS: ClassVar[int] = 204


@dataclass
class GetManyEndpoint(ReturnsDataMixin, Endpoint):
    METHOD = "GET"
    URL_NAME_TEMPLATE = "{type_name}__collection"


@dataclass
class CreateOneEndpoint(ReturnsDataMixin, ExpectsPayloadMixin, Endpoint):
    METHOD = "POST"
    URL_NAME_TEMPLATE = "{type_name}__collection"
    SUCCESS_STATUS: ClassVar[int] = 201

    def _get_payload_schema_fn(self, resource_class: type[Resource]):
        return resource_class.jsonschema_create

    def _get_payload_fields(self, resource_class: type[Resource]) -> list[str] | None:
        return resource_class._create_fields

    def _postprocess(self, response: Response, request: HttpRequest) -> Any:
        if response.data is None:
            return _jsonapi_response(
                {"jsonapi": {"version": "1.0"}},
                status=202,
                content_type="application/vnd.api+json",
            )
        return super()._postprocess(response, request)


@dataclass
class GetRelatedEndpoint(ReturnsDataMixin, ExpectsRelationshipMixin, Endpoint):
    METHOD = "GET"
    URL_NAME_TEMPLATE = "{type_name}__{relationship_name}__related"

    @functools.cached_property
    def url(self):
        path_type = _path_type(self.pk_type)
        return f"{self.type_name}/<{path_type}:{self.pk_name}>/{self.relationship_name}"


@dataclass
class GetRelationshipEndpoint(ExpectsRelationshipMixin, Endpoint):
    METHOD = "GET"
    URL_NAME_TEMPLATE = "{type_name}__{relationship_name}__relationship"

    def _postprocess(self, response: Response, request: HttpRequest) -> dict:
        result: dict = {"data": response.data}
        links = result.setdefault("links", {})
        assert isinstance(links, dict)
        links["self"] = request.get_full_path()
        try:
            related = re.sub(
                rf"/relationship/{re.escape(self.relationship_name)}$",
                f"/{self.relationship_name}",
                request.path,
            )
            links["related"] = related
        except Exception:
            pass
        return result

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        kwargs, errors = super()._get_kwargs(request, url_kwargs, remaining_params)
        if remaining_params is not None:
            remaining_params.clear()
        return kwargs, errors


@dataclass
class EditRelationshipEndpoint(ExpectsRelationshipMixin, ExpectsRelationshipIdsMixin, Endpoint):
    METHOD = "PATCH"
    URL_NAME_TEMPLATE = "{type_name}__{relationship_name}__relationship"
    SUCCESS_STATUS: ClassVar[int] = 204


@dataclass
class ResetRelationshipEndpoint(ExpectsRelationshipMixin, ExpectsRelationshipIdsMixin, Endpoint):
    METHOD = "PATCH"
    URL_NAME_TEMPLATE = "{type_name}__{relationship_name}__relationship"
    SUCCESS_STATUS: ClassVar[int] = 204


@dataclass
class AddToRelationshipEndpoint(ExpectsRelationshipMixin, ExpectsRelationshipIdsMixin, Endpoint):
    METHOD = "POST"
    URL_NAME_TEMPLATE = "{type_name}__{relationship_name}__relationship"
    SUCCESS_STATUS: ClassVar[int] = 204


@dataclass
class RemoveFromRelationshipEndpoint(
    ExpectsRelationshipMixin, ExpectsRelationshipIdsMixin, Endpoint
):
    METHOD = "DELETE"
    URL_NAME_TEMPLATE = "{type_name}__{relationship_name}__relationship"
    SUCCESS_STATUS: ClassVar[int] = 204


@dataclass(kw_only=True)
class RpcEndpoint(ExpectsIdMixin, Endpoint):
    SUCCESS_STATUS: ClassVar[int] = 200
    action_name: str
    path_operation: dict | None = None
    method: str = "POST"

    @functools.cached_property
    def url(self):
        path_type = _path_type(self.pk_type)
        return f"{self.type_name}/<{path_type}:{self.pk_name}>/{self.action_name}"

    @property
    def url_name(self):
        return f"{self.type_name}__rpc__{self.action_name}"

    async def view(self, request: HttpRequest, **url_kwargs: Any) -> Any:
        remaining_params = request.GET.dict()
        kwargs, errors = self._get_kwargs(request, url_kwargs, remaining_params)
        for unknown in sorted(remaining_params):
            errors.append(
                BadRequest(f"Unknown query parameter: {unknown}", source={"parameter": unknown})
            )
        remaining_params.clear()
        if errors:
            raise DjsonApiExceptionMulti(*errors)
        if asyncio.iscoroutinefunction(self.handler):
            result = await self.handler(request, **kwargs)
        else:
            result = await sync_to_async(self.handler)(request, **kwargs)
        if isinstance(result, HttpResponse):
            return result
        if isinstance(result, Response):
            return result.serialize(request)
        return result

    def _get_kwargs(
        self,
        request: HttpRequest,
        url_kwargs: dict[str, Any],
        remaining_params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[DjsonApiExceptionSingle]]:
        kwargs = {}
        errors: list[DjsonApiExceptionSingle] = []

        if self.pk_name in url_kwargs:
            raw = url_kwargs[self.pk_name]
            if isinstance(raw, self.pk_type):
                pk_value = raw
            else:
                try:
                    pk_value = self.pk_type(raw)
                except Exception:
                    raise NotFound("The URL does not exist")
            kwargs[self.pk_name] = pk_value

        if request.body:
            try:
                body = json.loads(request.body)
            except json.JSONDecodeError as e:
                return kwargs, [BadRequest(f"Invalid JSON: {e}")]
            if isinstance(body, dict):
                kwargs.update(body)

        return kwargs, errors

    def _openapi_operation(self) -> dict:
        result = {}
        result["requestBody"] = {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "description": "Arbitrary RPC parameters",
                    }
                }
            },
        }
        result["responses"] = {
            "200": {
                "description": "OK",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "description": "RPC response",
                        }
                    }
                },
            }
        }
        if self.path_operation:
            _merge_openapi_into(result, self.path_operation)
        return result


def _validate_content_type(request: HttpRequest) -> None:
    if request.method not in ("POST", "PATCH", "DELETE"):
        return
    raw = request.META.get("CONTENT_TYPE", "")
    if not raw:
        return
    parts = [p.strip() for p in raw.split(";")]
    media_type = parts[0]
    params = parts[1:]
    if media_type != "application/vnd.api+json":
        raise UnsupportedMediaType(
            f"Unsupported media type '{media_type}'. Must be 'application/vnd.api+json'",
            source={"header": "Content-Type"},
        )
    for param in params:
        key = param.split("=")[0].strip() if "=" in param else param
        if key not in ("ext", "profile"):
            raise UnsupportedMediaType(
                f"Unsupported media type parameter '{key}'. Only 'ext' and 'profile' are allowed.",
                source={"header": "Content-Type"},
            )
        if key == "ext":
            raise UnsupportedMediaType(
                "This server does not support any extensions",
                source={"header": "Content-Type"},
            )


def _validate_accept(request: HttpRequest) -> None:
    raw = request.META.get("HTTP_ACCEPT", "")
    if not raw or raw == "*/*":
        return
    ranges = [a.strip() for a in raw.split(",")]
    for media_range in ranges:
        mt = media_range.split(";")[0].strip()
        if mt in ("*/*", "application/*", "application/vnd.api+json"):
            return
    raise NotAcceptable(
        "This server only serves 'application/vnd.api+json'",
        source={"header": "Accept"},
    )


def _jsonapi_response(*args, **kwargs) -> JsonResponse:
    resp = JsonResponse(*args, **kwargs)
    resp["Vary"] = "Accept"
    return resp


@dataclass
class DjsonApi:
    registry: list[Endpoint] = field(default_factory=list)

    def get_one(
        self,
        type_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
        sparse: bool = True,
        include_types: Sequence[type[Resource]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        if include_types is None:
            include_types = []

        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = GetOneEndpoint(
                type_name, handler, errors, sparse=sparse, include_types=include_types
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def get_many(
        self,
        type_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
        sparse: bool = True,
        include_types: Sequence[type[Resource]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        if include_types is None:
            include_types = []

        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = GetManyEndpoint(
                type_name, handler, errors, sparse=sparse, include_types=include_types
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def create_one(
        self,
        type_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
        sparse: bool = True,
        include_types: Sequence[type[Resource]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        if include_types is None:
            include_types = []

        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = CreateOneEndpoint(
                type_name, handler, errors, sparse=sparse, include_types=include_types
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def edit_one(
        self,
        type_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
        sparse: bool = True,
        include_types: Sequence[type[Resource]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        if include_types is None:
            include_types = []

        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = EditOneEndpoint(
                type_name, handler, errors, sparse=sparse, include_types=include_types
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def delete_one(
        self,
        type_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = DeleteOneEndpoint(type_name, handler, errors)
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def get_related(
        self,
        type_name: str,
        relationship_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
        sparse: bool = True,
        include_types: Sequence[type[Resource]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        if include_types is None:
            include_types = []

        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = GetRelatedEndpoint(
                type_name,
                handler,
                errors,
                relationship_name=relationship_name,
                sparse=sparse,
                include_types=include_types,
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def get_relationship(
        self,
        type_name: str,
        relationship_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = GetRelationshipEndpoint(
                type_name, handler, errors, relationship_name=relationship_name
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def edit_relationship(
        self,
        type_name: str,
        relationship_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = EditRelationshipEndpoint(
                type_name, handler, errors, relationship_name=relationship_name
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def reset_relationship(
        self,
        type_name: str,
        relationship_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = ResetRelationshipEndpoint(
                type_name, handler, errors, relationship_name=relationship_name
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def add_to_relationship(
        self,
        type_name: str,
        relationship_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = AddToRelationshipEndpoint(
                type_name, handler, errors, relationship_name=relationship_name
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def remove_from_relationship(
        self,
        type_name: str,
        relationship_name: str,
        errors: Sequence[type[DjsonApiExceptionSingle]] | None = None,
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = RemoveFromRelationshipEndpoint(
                type_name, handler, errors, relationship_name=relationship_name
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def rpc(
        self,
        type_name: str,
        action: str,
        path_operation: dict | None = None,
        method: str = "POST",
    ) -> Callable[[Callable[..., Any]], Endpoint]:
        def decorator(handler: Callable[..., Any]) -> Endpoint:
            endpoint = RpcEndpoint(
                type_name,
                handler,
                action_name=action,
                path_operation=path_operation,
                method=method,
            )
            self.registry.append(endpoint)
            return endpoint

        return decorator

    def _auto_derive_relationship_endpoints(self):
        existing = {
            (ep.type_name, ep.relationship_name)
            for ep in self.registry
            if isinstance(ep, GetRelationshipEndpoint)
        }
        for ep in list(self.registry):
            if not isinstance(ep, GetRelatedEndpoint):
                continue
            key = (ep.type_name, ep.relationship_name)
            if key in existing:
                continue
            self.registry.append(self._make_auto_relationship_ep(ep))
            existing.add(key)

    def _make_auto_relationship_ep(
        self, related_ep: GetRelatedEndpoint
    ) -> GetRelationshipEndpoint:
        async def _auto_handler(request, **kw):
            raw = related_ep.handler(request, **kw)
            if asyncio.iscoroutine(raw):
                raw = await raw
            if isinstance(raw, Response):
                data = raw.data
            else:
                data = raw
            if data is None:
                return None
            if isinstance(data, list):
                return [{"type": r._type, "id": str(r.id)} for r in data]
            return {"type": data._type, "id": str(data.id)}

        _auto_handler.__name__ = (
            f"get_{related_ep.type_name}_{related_ep.relationship_name}_relationship"
        )

        ep = GetRelationshipEndpoint(
            related_ep.type_name,
            _auto_handler,
            errors=related_ep.errors,
            relationship_name=related_ep.relationship_name,
        )
        ep.pk_type = related_ep.pk_type
        ep.pk_name = related_ep.pk_name
        return ep

    @property
    def urls(self) -> list[URLPattern]:
        self._auto_derive_relationship_endpoints()
        by_path: dict[str, list] = {}
        for endpoint in self.registry:
            by_path.setdefault(endpoint.url, []).append(endpoint)
        result = [
            django_path(path, self.combine_views(endpoints), name=endpoints[0].url_name)
            for path, endpoints in by_path.items()
        ]
        result.append(django_path("openapi.json", self._openapi_view, name="openapi"))
        result.append(django_path("docs/", self._docs_view, name="docs"))
        result.append(django_path("<path:path>", self._catch_all_404_view))
        return result

    def _catch_all_404_view(self, request: HttpRequest, path: str = "") -> JsonResponse:
        return _jsonapi_response(
            {
                "errors": [
                    {
                        "status": "404",
                        "code": "not_found",
                        "title": "Not Found",
                        "detail": f"The path '{request.path}' does not exist.",
                    }
                ]
            },
            status=404,
            content_type="application/vnd.api+json",
        )

    def _openapi_view(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse(self._build_openapi_spec())

    def _docs_view(self, request: HttpRequest) -> HttpResponse:
        try:
            openapi_url = reverse("openapi")
        except (NoReverseMatch, ImproperlyConfigured):
            openapi_url = "/openapi.json"
        html = f"""<!DOCTYPE html>
            <html>
            <head>
              <title>API Docs</title>
              <meta charset="utf-8"/>
              <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body>
              <redoc spec-url='{openapi_url}'></redoc>
              <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
            </body>
            </html>
        """
        return HttpResponse(html.encode(), content_type="text/html")

    def _build_openapi_spec(self) -> dict:
        spec: dict = {
            "openapi": "3.0.3",
            "info": {"title": "API", "version": "1.0.0"},
            "paths": {},
            "components": {"schemas": {}},
        }
        tags_set: set[str] = set()

        for endpoint in self.registry:
            type_name = endpoint.type_name
            tags_set.add(type_name)

            raw_name = endpoint.handler.__name__.replace("_", " ")
            summary = raw_name[0].upper() + raw_name[1:] if raw_name else raw_name

            op = endpoint._openapi_operation()
            op["tags"] = [type_name]
            op["summary"] = summary
            method = (getattr(endpoint, "method", None) or endpoint.METHOD).lower()

            if "responses" not in op:
                op["responses"] = {
                    str(endpoint.SUCCESS_STATUS): {
                        "description": "No Content" if endpoint.SUCCESS_STATUS == 204 else "OK"
                    }
                }

            if endpoint.errors:
                for ex_class in endpoint.errors:
                    status = str(getattr(ex_class, "STATUS", 400))
                    title = _class_name_to_title(ex_class.__name__)
                    op["responses"][status] = {
                        "description": title,
                        "content": {
                            "application/vnd.api+json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "errors": {"type": "array", "items": {"type": "object"}}
                                    },
                                }
                            }
                        },
                    }

            path_str = _django_path_to_openapi(f"/{endpoint.url}")
            spec["paths"].setdefault(path_str, {})[method] = op

            for schema_name, schema_obj in op.get("components", {}).get("schemas", {}).items():
                spec["components"]["schemas"].setdefault(schema_name, schema_obj)

        endpoint_tags = sorted(tags_set)
        spec["tags"] = [{"name": t} for t in endpoint_tags]

        schema_names = list(spec["components"]["schemas"].keys())
        if schema_names:
            schema_tags = []
            for schema_name in schema_names:
                type_name = schema_name.removesuffix("_resource")
                tag_name = f"{type_name}_schema"
                schema_tags.append(tag_name)
                spec["tags"].append(
                    {
                        "name": tag_name,
                        "x-displayName": type_name,
                        "description": f'<SchemaDefinition schemaRef="#/components/schemas/{schema_name}" />',
                    }
                )
            spec["x-tagGroups"] = [
                {"name": "Endpoints", "tags": endpoint_tags},
                {"name": "Schemas", "tags": schema_tags},
            ]
        else:
            spec["x-tagGroups"] = [
                {"name": "Endpoints", "tags": endpoint_tags},
            ]
        return spec

    def combine_views(self, endpoints: list[Endpoint]) -> Callable[..., Any]:
        by_method: dict[str, Endpoint] = {
            getattr(endpoint, "method", None) or endpoint.METHOD: endpoint
            for endpoint in endpoints
        }

        @csrf_exempt
        async def view(request: HttpRequest, *args, **kwargs):
            try:
                if request.method not in by_method:
                    raise MethodNotAllowed(
                        f"Method {request.method} not allowed for this endpoint, "
                        f"allowed methods: {', '.join(by_method.keys())}"
                    )
                endpoint = by_method[request.method]
                if not isinstance(endpoint, RpcEndpoint):
                    _validate_accept(request)
                    _validate_content_type(request)
                result = await endpoint.view(request, *args, **kwargs)
            except DjsonApiException as exc:
                return _jsonapi_response(
                    {"errors": exc.render()},
                    status=exc.status,
                    content_type="application/vnd.api+json",
                )
            except Exception:
                logging.exception("Unhandled exception in djsonapi endpoint")
                tb = traceback.format_exc() if settings.DEBUG else None
                exc = InternalServerError(detail=tb)
                return _jsonapi_response(
                    {"errors": exc.render()},
                    status=exc.status,
                    content_type="application/vnd.api+json",
                )

            if isinstance(result, dict):
                result.setdefault("links", {})["self"] = request.get_full_path()
                if isinstance(result.get("data"), list):
                    for item in result["data"]:
                        self._fill_resource_links(item)
                        self._filter_out_sparse(item, request)
                elif result.get("data") is not None:
                    self._fill_resource_links(result["data"])
                    self._filter_out_sparse(result["data"], request)
                for item in result.get("included", []):
                    self._fill_resource_links(item)
                    self._filter_out_sparse(item, request)
                django_response = _jsonapi_response(
                    result, status=endpoint.SUCCESS_STATUS, content_type="application/vnd.api+json"
                )
                if endpoint.SUCCESS_STATUS == 201 and isinstance(result.get("data"), dict):
                    resource_id = result["data"].get("id")
                    if resource_id is not None:
                        try:
                            django_response["Location"] = reverse(
                                f"{endpoint.type_name}__item", args=(str(resource_id),)
                            )
                        except (NoReverseMatch, ImproperlyConfigured):
                            pass
                return django_response

            if isinstance(result, Response):
                data = result.serialize(request)
                return _jsonapi_response(
                    data,
                    status=result.status or endpoint.SUCCESS_STATUS,
                    content_type="application/vnd.api+json",
                )

            return result

        return view

    def _fill_resource_links(self, resource: dict[str, Any]) -> None:
        _type = resource.get("type")
        _id = resource.get("id")

        try:
            self_link = reverse(f"{_type}__item", args=(_id,))
        except (NoReverseMatch, ImproperlyConfigured):
            pass
        else:
            resource.setdefault("links", {})["self"] = self_link

        for relationship_name, relationship in resource.get("relationships", {}).items():
            related_link = None
            try:
                related_link = reverse(f"{_type}__{relationship_name}__related", args=(_id,))
            except (NoReverseMatch, ImproperlyConfigured):
                pass

            if related_link:
                relationship.setdefault("links", {})["related"] = related_link

            try:
                relationship_link = reverse(
                    f"{_type}__{relationship_name}__relationship", args=(_id,)
                )
            except (NoReverseMatch, ImproperlyConfigured):
                pass
            else:
                relationship.setdefault("links", {})["self"] = relationship_link

    def _filter_out_sparse(self, resource: dict[str, Any], request: HttpRequest) -> None:
        _type = resource.get("type")
        if not _type:
            return

        allowed_fields: set[str] | None = None
        for key in request.GET.keys():
            if (m := re.search(r"^fields\[(\w+)\]$", key)) and m.group(1) == _type:
                raw = request.GET.get(key, "")
                allowed_fields = {f.strip() for f in raw.split(",") if f.strip()}
                break

        if allowed_fields is None:
            return

        if "attributes" in resource:
            resource["attributes"] = {
                k: v for k, v in resource["attributes"].items() if k in allowed_fields
            }
        if "relationships" in resource:
            resource["relationships"] = {
                k: v for k, v in resource["relationships"].items() if k in allowed_fields
            }
        if len(resource.get("attributes", {})) == 0:
            resource.pop("attributes", None)
        if len(resource.get("relationships", {})) == 0:
            resource.pop("relationships", None)
