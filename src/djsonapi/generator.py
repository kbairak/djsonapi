"""Generate a typed client SDK package from a ``DjsonApi`` instance.

The generated package copies ``djsonapi_client_py`` verbatim as its ``_runtime``
sub-package and adds a thin typed layer on top: per-resource classes with
attribute annotations, capability flags and typed ``list``/``get``/``create``/
``save``/``fetch`` wrappers, plus an ``SDK`` subclass with the known resource
types sealed in.
"""

from __future__ import annotations

import collections.abc
import datetime
import inspect
import shutil
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import NoneType, UnionType
from typing import Annotated, Any, ClassVar, Literal, Union, get_args, get_origin

from djsonapi.api import (
    AddToRelationshipEndpoint,
    CreateOneEndpoint,
    DeleteOneEndpoint,
    DjsonApi,
    EditOneEndpoint,
    EditRelationshipEndpoint,
    ExpectsPayloadMixin,
    GetManyEndpoint,
    GetOneEndpoint,
    RemoveFromRelationshipEndpoint,
    ResetRelationshipEndpoint,
    RpcEndpoint,
)
from djsonapi.resource import Resource
from djsonapi.response import Response

QUERY_PREFIXES = ("filter__", "page", "sort", "include__", "fields__", "extra__")


@dataclass
class _TypeSpec:
    type_name: str
    resource_class: type[Resource]
    capabilities: set[str] = field(default_factory=set)
    rel_capabilities: dict[str, set[str]] = field(default_factory=dict)
    rel_targets: dict[str, tuple[str, bool]] = field(default_factory=dict)
    query_params: list[inspect.Parameter] = field(default_factory=list)
    get_query_params: list[inspect.Parameter] = field(default_factory=list)
    pk_type: type = str
    allowed_sparse: dict[str, set[str]] = field(default_factory=dict)
    get_include_types: set[str] = field(default_factory=set)
    find_include_types: set[str] = field(default_factory=set)
    rpc_actions: dict[str, str] = field(default_factory=dict)

    @property
    def class_name(self) -> str:
        return self.resource_class.__name__


class _Renderer:
    """Render type annotations as source code, tracking needed imports."""

    def __init__(self) -> None:
        self.imports: set[str] = set()

    def render(self, tp: Any) -> str:
        if tp is inspect.Parameter.empty or tp is None or tp is Any:
            return "Any"
        origin = get_origin(tp)
        args = get_args(tp)
        if origin is ClassVar:
            return self.render(args[0]) if args else "Any"
        if origin is Annotated:
            return self.render(args[0])
        if origin is Literal:
            self.imports.add("from typing import Literal")
            return "Literal[" + ", ".join(repr(a) for a in args) + "]"
        if origin in (Union, types.UnionType):
            return " | ".join("None" if a is type(None) else self.render(a) for a in args)
        if origin is list or origin is collections.abc.Sequence:
            return f"list[{self.render(args[0]) if args else 'Any'}]"
        if origin is dict:
            return "dict[str, Any]"
        if tp is type(None):
            return "None"
        if tp in (str, int, float, bool):
            return tp.__name__
        if tp is datetime.datetime:
            self.imports.add("import datetime")
            return "datetime.datetime"
        if tp is datetime.date:
            self.imports.add("import datetime")
            return "datetime.date"
        if tp is datetime.time:
            self.imports.add("import datetime")
            return "datetime.time"
        if tp is uuid.UUID:
            self.imports.add("import uuid")
            return "uuid.UUID"
        return "Any"

    def render_optional(self, tp: Any) -> str:
        rendered = self.render(tp)
        if rendered != "Any" and not _is_optional(tp):
            return f"{rendered} | None"
        return rendered


def _returns_list(endpoint) -> bool:
    annotation = endpoint.return_annotation
    if annotation is inspect.Parameter.empty:
        return False
    origin = get_origin(annotation)
    if origin is list:
        return True
    if isinstance(origin, type) and issubclass(origin, Response):
        args = get_args(annotation)
        return bool(args) and get_origin(args[0]) is list
    return False


def _query_params_of(endpoint) -> list[inspect.Parameter]:
    result = []
    for param in endpoint.smart_parameters:
        if isinstance(param.annotation, type) and issubclass(param.annotation, Resource):
            continue
        if not any(param.name.startswith(p) for p in QUERY_PREFIXES):
            continue
        result.append(param)
    return result


def _collect(api: DjsonApi) -> dict[str, _TypeSpec]:
    specs: dict[str, _TypeSpec] = {}

    def spec_for(type_name: str, resource_class: type[Resource] | None) -> _TypeSpec | None:
        if type_name in specs:
            return specs[type_name]
        if resource_class is None:
            return None
        specs[type_name] = _TypeSpec(type_name, resource_class)
        return specs[type_name]

    for endpoint in api.registry:
        resource_class = endpoint.return_resource_type
        if resource_class is None and isinstance(endpoint, ExpectsPayloadMixin):
            try:
                annotation = endpoint.payload_parameter.annotation
            except IndexError:
                annotation = None
            if isinstance(annotation, type) and issubclass(annotation, Resource):
                resource_class = annotation
        spec = spec_for(endpoint.type_name, resource_class)
        if spec is None:
            continue

        if isinstance(endpoint, GetOneEndpoint):
            spec.capabilities.add("get_one")
            spec.get_query_params = _query_params_of(endpoint)
            spec.pk_type = endpoint.pk_type
            spec.get_include_types = endpoint.allowed_includes
        elif isinstance(endpoint, GetManyEndpoint):
            spec.capabilities.add("get_many")
            spec.query_params = _query_params_of(endpoint)
            spec.allowed_sparse = endpoint.allowed_sparse
            spec.find_include_types = endpoint.allowed_includes
        elif isinstance(endpoint, CreateOneEndpoint):
            spec.capabilities.add("create")
        elif isinstance(endpoint, EditOneEndpoint):
            spec.capabilities.add("edit")
            spec.pk_type = endpoint.pk_type
        elif isinstance(endpoint, DeleteOneEndpoint):
            spec.capabilities.add("delete")
            spec.pk_type = endpoint.pk_type
        elif isinstance(endpoint, EditRelationshipEndpoint):
            spec.rel_capabilities.setdefault(endpoint.relationship_name, set()).add("edit")
        elif isinstance(endpoint, ResetRelationshipEndpoint):
            spec.rel_capabilities.setdefault(endpoint.relationship_name, set()).add("reset")
        elif isinstance(endpoint, AddToRelationshipEndpoint):
            spec.rel_capabilities.setdefault(endpoint.relationship_name, set()).add("add")
        elif isinstance(endpoint, RemoveFromRelationshipEndpoint):
            spec.rel_capabilities.setdefault(endpoint.relationship_name, set()).add("remove")
        elif isinstance(endpoint, RpcEndpoint):
            spec.rpc_actions[endpoint.action_name] = endpoint.method

    for spec in specs.values():
        cls = spec.resource_class
        if cls is not None:
            for field, target in cls._singular_rels:
                spec_for(target, None)
                spec.rel_targets.setdefault(field, (target, False))
            for field, target in cls._plural_rels:
                spec_for(target, None)
                spec.rel_targets.setdefault(field, (target, True))

    return specs


def _indent(lines: list[str], prefix: str = "    ") -> list[str]:
    return [prefix + line if line else "" for line in lines]


def _render_typed_dict(name: str, entries: list[tuple[str, str]]) -> list[str]:
    lines = [f"class {name}(TypedDict, total=False):"]
    if entries:
        lines += _indent([f"{key}: {value}" for key, value in entries])
    else:
        lines.append("    pass")
    return lines


def _target_type_for(target: tuple[str, bool] | None, specs: dict[str, _TypeSpec]) -> str:
    if target is not None and target[0] in specs:
        return f"{specs[target[0]].class_name} | int"
    return "Any"


def _render_resource_class(
    spec: _TypeSpec,
    specs: dict[str, _TypeSpec],
    renderer: _Renderer,
) -> list[str]:
    cls = spec.resource_class
    name = spec.class_name
    annotations = cls._annotations()
    query_name = f"{name}Query"
    get_query_name = f"{name}GetQuery"
    edit_name = f"{name}Edit"
    collection_name = f"{name}Collection"

    lines = [f"class {name}(Resource):"]
    body = [f'_type: ClassVar[str] = "{spec.type_name}"']

    attr_types: dict[str, Any] = {"id": annotations.get("id", spec.pk_type)}
    for attr in cls._attributes:
        attr_types[attr] = annotations.get(attr, Any)
    body.append("_attribute_types: ClassVar[dict[str, Any]] = {")
    body += _indent([f'"{key}": {renderer.render(tp)},' for key, tp in attr_types.items()])
    body.append("}")

    rel_types = [
        *(f'"{field}": ("{target}", False),' for field, target in cls._singular_rels),
        *(f'"{field}": ("{target}", True),' for field, target in cls._plural_rels),
    ]
    if rel_types:
        body.append("_relationship_types: ClassVar[dict[str, tuple[str, bool]]] = {")
        body += _indent(rel_types)
        body.append("}")

    if spec.capabilities:
        caps = '", "'.join(sorted(spec.capabilities))
        body.append("_capabilities: ClassVar[frozenset[str]] = frozenset(")
        body.append(f'    {{"{caps}"}}')
        body.append(")")
    else:
        body.append("_capabilities: ClassVar[frozenset[str]] = frozenset()")

    if spec.rel_capabilities:
        body.append("_relationship_capabilities: ClassVar[dict[str, frozenset[str]]] = {")
        for rel, ops in sorted(spec.rel_capabilities.items()):
            ops_str = '", "'.join(sorted(ops))
            body.append(f'    "{rel}": frozenset({{"{ops_str}"}}),')
        body.append("}")

    if "get_many" in spec.capabilities:
        body.append(f"_collection_class: ClassVar = {collection_name}")

    body.append("")
    body.append(f"id: {renderer.render(annotations.get('id', spec.pk_type))}")
    for attr in cls._attributes:
        body.append(f"{attr}: {renderer.render(annotations.get(attr, Any))}")

    for rel, target in sorted(spec.rel_targets.items()):
        if target is not None and target[0] in specs:
            target_name = specs[target[0]].class_name
            if target[1]:
                ret = (
                    f"{target_name}Collection"
                    if "get_many" in specs[target[0]].capabilities
                    else f"Collection[{target_name}]"
                )
            else:
                nullable = _is_optional(annotations.get(rel))
                ret = f"{target_name} | None" if nullable else target_name
        elif target is not None:
            ret = f'Collection["{target[0].capitalize()}"]' if target[1] else "Resource | None"
        else:
            ret = "Any" if not target[1] else "Collection[Any]"
        body.append(f"{rel}: {ret}")

    if "get_many" in spec.capabilities:
        body += [
            "",
            "@classmethod",
            f"def list(cls) -> {collection_name}:",
            f"    return cast({collection_name}, super().list())",
        ]

    if "get_one" in spec.capabilities:
        pk = renderer.render(spec.pk_type)
        id_annotation = "str | None" if pk == "str" else f"{pk} | str | None"
        has_get_filter_params = any(p.name.startswith("filter__") for p in spec.get_query_params)
        if has_get_filter_params:
            get_query = f"Unpack[{get_query_name}]"
        else:
            get_query = "Any"
        get_includes = sorted(spec.get_include_types)
        if get_includes:
            renderer.imports.add("from typing import Literal")
            includes_literal = ", ".join(f'"{inc}"' for inc in sorted(get_includes))
            body += [
                "",
                "@overload",
                "@classmethod",
                f"async def get(cls, id: int | str, *includes: Literal[{includes_literal}]) -> {name}: ...",
            ]
        body += [
            "",
            "@overload",
            "@classmethod",
            f"async def get(cls, id: int | str, *includes: str) -> {name}: ...",
            "@classmethod",
            f"async def get(cls, id: {id_annotation} = None, *includes: str, **query: {get_query}) -> {name}:",
            f"    return cast({name}, await super().get(id, *includes, **query))",
        ]

    if "get_many" in spec.capabilities:
        find_includes = sorted(spec.find_include_types)
        if find_includes:
            renderer.imports.add("from typing import Literal")
            includes_literal = ", ".join(f'"{inc}"' for inc in sorted(find_includes))
            body += [
                "",
                "@overload",
                "@classmethod",
                f"async def find(cls, *includes: Literal[{includes_literal}], **query: Unpack[{query_name}]) -> {name}: ...",
            ]
        body += [
            "",
            "@overload",
            "@classmethod",
            f"async def find(cls, *includes: str, **query: Unpack[{query_name}]) -> {name}: ...",
            "@classmethod",
            f"async def find(cls, *includes: str, **query: Unpack[{query_name}]) -> {name}:",
            f"    return cast({name}, await super().find(*includes, **query))",
        ]

    if "create" in spec.capabilities:
        singular_rels = dict(cls._singular_rels)
        plural_rels = dict(cls._plural_rels)
        required, optional = [], []
        for f in cls._create_fields:
            annotation = annotations.get(f, Any)
            raw = renderer.render(annotation)

            if f in singular_rels and singular_rels[f] in specs:
                target_name = specs[singular_rels[f]].class_name
                rendered = f"{target_name} | {raw}"
            elif f in plural_rels and plural_rels[f] in specs:
                target_name = specs[plural_rels[f]].class_name
                rendered = f"list[{target_name} | {raw}]"
            else:
                rendered = raw

            if f in cls._required_create_fields:
                required.append((f, rendered))
            else:
                if rendered != "Any" and "None" not in rendered.split(" | "):
                    rendered = f"{rendered} | None"
                optional.append((f, rendered))
        params = [f"{f}: {tp}" for f, tp in required]
        params += [f"{f}: {tp} = None" for f, tp in optional]
        signature = f"cls, *, {', '.join(params)}" if params else "cls"
        body += ["", "@classmethod"]
        body.append(f"async def create({signature}) -> {name}:")
        if required:
            kwargs = ", ".join(f'"{f}": {f}' for f, _ in required)
            body.append(f"    kwargs: dict[str, Any] = {{{kwargs}}}")
        else:
            body.append("    kwargs: dict[str, Any] = {}")
        for f, _ in optional:
            body += [f"    if {f} is not None:", f'        kwargs["{f}"] = {f}']
        body.append(f"    return cast({name}, await super().create(**kwargs))")

    if "edit" in spec.capabilities:
        body += [
            "",
            "async def save(",
            f"    self, *fields: str, force_create: bool = False, **kwargs: Unpack[{edit_name}]",
            ") -> None:",
            "    await super().save(*fields, force_create=force_create, **kwargs)",
        ]

    edit_rels = [
        (rel, spec.rel_targets.get(rel))
        for rel, ops in sorted(spec.rel_capabilities.items())
        if "edit" in ops
    ]
    if edit_rels:
        renderer.imports.add("from typing import Literal")
        body.append("")
        for rel, target in edit_rels:
            target_type = _target_type_for(target, specs)
            body += [
                "@overload",
                f'async def edit(self, relationship: Literal["{rel}"], resource: {target_type}) -> None: ...',
            ]
        body += [
            "@overload",
            "async def edit(self, relationship: str, resource: Any) -> None: ...",
            "async def edit(self, relationship: str, resource: Any) -> None:",
            "    return await super().edit(relationship, resource)",
        ]

    for cap in ("add", "remove", "reset"):
        rels = [
            (rel, spec.rel_targets.get(rel))
            for rel, ops in sorted(spec.rel_capabilities.items())
            if cap in ops
        ]
        if rels:
            renderer.imports.add("from typing import Literal")
            body.append("")
            for rel, target in rels:
                target_type = _target_type_for(target, specs)
                body += [
                    "@overload",
                    f'async def {cap}(self, relationship: Literal["{rel}"], *resources: {target_type}) -> None: ...',
                ]
            body += [
                "@overload",
                f"async def {cap}(self, relationship: str, *resources: Any) -> None: ...",
                f"async def {cap}(self, relationship: str, *resources: Any) -> None:",
                f"    return await super().{cap}(relationship, *resources)",
            ]

    if spec.rpc_actions:
        renderer.imports.add("from typing import Any")
        renderer.imports.add("from typing import Literal")
        actions_repr = ", ".join(f"{a!r}: {m!r}" for a, m in spec.rpc_actions.items())
        body.append("")
        body.append(f"_rpc_methods: ClassVar[dict[str, str]] = {{{actions_repr}}}")
        actions_literal = " | ".join(f"{a!r}" for a in spec.rpc_actions)
        body.append("")
        body += [
            f"async def rpc(self, action: Literal[{actions_literal}], payload: Any = None, mimetype: str | None = None) -> Any:",
            "    return await super().rpc(action, payload=payload, mimetype=mimetype)",
        ]

    lines += _indent(body)
    return lines


def _is_optional(tp: type | None) -> bool:
    if tp is None:
        return True
    origin = get_origin(tp)
    return origin in (Union, UnionType) and NoneType in get_args(tp)


def _strip_prefix(name: str) -> str:
    for prefix in ("filter__", "page__", "extra__"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _sparse_literal(
    type_name: str, specs: dict[str, _TypeSpec], renderer: _Renderer
) -> str | None:
    target = specs.get(type_name)
    if target is None:
        return None
    cls = target.resource_class
    field_names: list[str] = ["id"] if "id" in cls.__annotations__ else []
    field_names.extend(cls._attributes)
    for f, _ in cls._singular_rels:
        field_names.append(f)
    for f, _ in cls._plural_rels:
        field_names.append(f)
    if not field_names:
        return None
    lit = "Literal[" + ", ".join(repr(f) for f in field_names) + "]"
    renderer.imports.add("from typing import Literal")
    return f"list[{lit}]"


def _ts_sparse_fieldset(type_name: str, specs: dict[str, _TypeSpec]) -> str | None:
    target = specs.get(type_name)
    if target is None:
        return None
    cls = target.resource_class
    field_names: list[str] = ["id"] if "id" in cls.__annotations__ else []
    field_names.extend(cls._attributes)
    for f, _ in cls._singular_rels:
        field_names.append(f)
    for f, _ in cls._plural_rels:
        field_names.append(f)
    if not field_names:
        return None
    lit = " | ".join(repr(f) for f in field_names)
    if len(field_names) == 1:
        return f"{lit}[]"
    return f"({lit})[]"


def _render_resources(specs: dict[str, _TypeSpec]) -> str:
    renderer = _Renderer()
    sections: list[str] = []

    for spec in specs.values():
        query_entries = [
            (_strip_prefix(p.name), renderer.render(p.annotation))
            for p in spec.query_params
            if p.name.startswith("filter__")
        ]
        get_entries = [
            (_strip_prefix(p.name), renderer.render(p.annotation)) for p in spec.get_query_params
        ]
        if "get_many" in spec.capabilities:
            sections.append(
                "\n".join(_render_typed_dict(f"{spec.class_name}Query", query_entries))
            )
        if "get_one" in spec.capabilities:
            sections.append(
                "\n".join(_render_typed_dict(f"{spec.class_name}GetQuery", get_entries))
            )
        if "edit" in spec.capabilities:
            cls = spec.resource_class
            annotations = cls._annotations()
            singular_rels = dict(cls._singular_rels)
            plural_rels = dict(cls._plural_rels)
            edit_entries = []
            for f in spec.resource_class._edit_fields:
                if f in singular_rels and singular_rels[f] in specs:
                    target_name = specs[singular_rels[f]].class_name
                    rendered = f"{target_name} | {renderer.render(annotations.get(f, Any))} | None"
                elif f in plural_rels and plural_rels[f] in specs:
                    target_name = specs[plural_rels[f]].class_name
                    rendered = (
                        f"list[{target_name}] | {renderer.render(annotations.get(f, Any))} | None"
                    )
                else:
                    rendered = renderer.render_optional(annotations.get(f, Any))
                edit_entries.append((f, rendered))
            sections.append("\n".join(_render_typed_dict(f"{spec.class_name}Edit", edit_entries)))

    for spec in specs.values():
        if "get_many" in spec.capabilities:
            sort_param = next((p for p in spec.query_params if p.name == "sort"), None)
            sort_lines = []
            if sort_param is not None:
                annotation = sort_param.annotation
                if annotation is not inspect.Parameter.empty:
                    origin = get_origin(annotation)
                    args = get_args(annotation)
                    if origin is Literal:
                        rendered = renderer.render(annotation)
                        sort_lines.append(f"    def sort(self, *fields: {rendered}) -> Self:")
                        sort_lines.append("        return super().sort(*fields)")
                    elif origin is list and args:
                        item_origin = get_origin(args[0])
                        if item_origin is Literal:
                            rendered = renderer.render(args[0])
                            sort_lines.append(f"    def sort(self, *fields: {rendered}) -> Self:")
                            sort_lines.append("        return super().sort(*fields)")
            fields_lines = []
            if spec.allowed_sparse:
                fields_params = sorted(
                    (name, _sparse_literal(name, specs, renderer)) for name in spec.allowed_sparse
                )
                fields_params = [(name, lit) for name, lit in fields_params if lit is not None]
                if fields_params:
                    args = ", ".join(f"{name}: {lit} | None = None" for name, lit in fields_params)
                    fields_lines.append(f"    def fields(self, *, {args}) -> Self:")
                    fields_lines.append("        kwargs = {}")
                    for name, _ in fields_params:
                        fields_lines.append(f"        if {name} is not None:")
                        fields_lines.append(f"            kwargs[{name!r}] = {name}")
                    fields_lines.append("        return super().fields(**kwargs)")
            filter_params = [p for p in spec.query_params if p.name.startswith("filter__")]
            filter_lines = []
            if filter_params:
                filter_lines.append(
                    f"    def filter(self, **kwargs: Unpack[{spec.class_name}Query]) -> Self:"
                )
                filter_lines.append("        return super().filter(**kwargs)")
            body = (
                f'class {spec.class_name}Collection(Collection["{spec.class_name}"]):\n'
                + "\n".join(filter_lines)
            )
            if sort_lines:
                body += "\n" + "\n".join(sort_lines)
            if fields_lines:
                body += "\n" + "\n".join(fields_lines)
            sections.append(body)

    for spec in specs.values():
        sections.append("\n".join(_render_resource_class(spec, specs, renderer)))

    typing_names = sorted(
        {"Any", "ClassVar", "Self", "TypedDict", "Unpack", "cast", "overload"}
        | {
            imp[len("from typing import ") :]
            for imp in renderer.imports
            if imp.startswith("from typing import ")
        }
    )
    header = [
        "from __future__ import annotations",
        "",
        *sorted(imp for imp in renderer.imports if not imp.startswith("from typing")),
        f"from typing import {', '.join(typing_names)}",
        "",
        "from ._runtime.collection import Collection",
        "from ._runtime.resource import Resource",
    ]
    return "\n".join(header) + "\n\n\n" + "\n\n\n".join(sections) + "\n"


def _render_sdk(specs: dict[str, _TypeSpec]) -> str:
    names = ", ".join(sorted(spec.class_name for spec in specs.values()))
    lines = [
        "from __future__ import annotations",
        "",
        "from typing import ClassVar",
        "",
        "from ._runtime.sdk import DjsonApiSdk",
        f"from .resources import {names}",
        "",
        "",
        "class SDK(DjsonApiSdk):",
        "    _resource_classes: ClassVar = {",
    ]
    for spec in specs.values():
        lines.append(f'        "{spec.type_name}": {spec.class_name},')
    lines.append("    }")
    lines.append("")
    for spec in specs.values():
        lines.append(f"    {spec.type_name}: type[{spec.class_name}]")
    lines += ["", "", "sdk = SDK()"]
    return "\n".join(lines) + "\n"


def _render_init(specs: dict[str, _TypeSpec]) -> str:
    names = sorted(spec.class_name for spec in specs.values())
    all_names = sorted(names + ["Collection", "Resource", "SDK", "sdk"])
    return (
        "import logging\n"
        "\n"
        "from ._runtime.collection import Collection\n"
        "from ._runtime.resource import Resource\n"
        f"from .resources import {', '.join(names)}\n"
        "from .sdk import SDK, sdk\n"
        "\n"
        "logger = logging.getLogger(__name__)\n"
        "\n"
        f"__all__ = {all_names!r}\n"
    )


# ── TypeScript generator ─────────────────────────────────────────────


class _TSRenderer:
    def render(self, tp: Any) -> str:
        if tp is inspect.Parameter.empty or tp is None or tp is Any:
            return "unknown"
        origin = get_origin(tp)
        args = get_args(tp)
        if origin is ClassVar:
            return self.render(args[0]) if args else "unknown"
        if origin is Annotated:
            return self.render(args[0])
        if origin is Literal:
            return " | ".join(repr(a) for a in args)
        if origin in (Union, types.UnionType):
            return " | ".join("null" if a is type(None) else self.render(a) for a in args)
        if origin is list or origin is collections.abc.Sequence:
            return f"{self.render(args[0]) if args else 'unknown'}[]"
        if origin is dict:
            return "Record<string, unknown>"
        if tp is type(None):
            return "null"
        if tp in (str, int, float, bool):
            return {"str": "string", "int": "number", "float": "number", "bool": "boolean"}[
                tp.__name__
            ]
        if tp is datetime.datetime or tp is datetime.date or tp is datetime.time:
            return "string"
        if tp is uuid.UUID:
            return "string"
        return "unknown"

    def render_optional(self, tp: Any) -> str:
        rendered = self.render(tp)
        if rendered != "unknown" and not _is_optional(tp):
            return f"{rendered} | null"
        return rendered


def _render_ts_typed_interface(name: str, entries: list[tuple[str, str]]) -> list[str]:
    if entries:
        lines = [f"export interface {name} {{"]
        lines += _indent([f"{key}?: {value};" for key, value in entries])
        lines.append("}")
    else:
        lines = [f"export interface {name} {{}}"]
    return lines


def _ts_target_type(target: tuple[str, bool] | None, specs: dict[str, _TypeSpec]) -> str:
    if target is not None and target[0] in specs:
        return f"{specs[target[0]].class_name} | number"
    return "unknown"


def _render_ts_collection_class(
    spec: _TypeSpec, specs: dict[str, _TypeSpec], renderer: _TSRenderer
) -> list[str]:
    name = spec.class_name
    query_name = f"{name}Query"
    lines: list[str] = []

    sort_lines: list[str] = []
    sort_param = next((p for p in spec.query_params if p.name == "sort"), None)
    if sort_param is not None:
        annotation = sort_param.annotation
        if annotation is not inspect.Parameter.empty:
            origin = get_origin(annotation)
            args = get_args(annotation)
            if origin is Literal:
                lit = " | ".join(repr(a) for a in args)
                sort_lines.append(f"  sort(...fields: ({lit})[]): this {{")
                sort_lines.append("    return super.sort(...fields);")
                sort_lines.append("  }")
            elif origin is list and args:
                item_origin = get_origin(args[0])
                if item_origin is Literal:
                    lit = " | ".join(repr(a) for a in get_args(args[0]))
                    sort_lines.append(f"  sort(...fields: ({lit})[]): this {{")
                    sort_lines.append("    return super.sort(...fields);")
                    sort_lines.append("  }")

    fields_lines: list[str] = []
    if spec.allowed_sparse:
        fields_params = sorted((n, _ts_sparse_fieldset(n, specs)) for n in spec.allowed_sparse)
        fields_params = [(n, lit) for n, lit in fields_params if lit is not None]
        if fields_params:
            entries = ", ".join(f"{n}?: {lit} | null" for n, lit in fields_params)
            fields_lines.append(f"  fields(params: {{ {entries} }}): this {{")
            fields_lines.append("    const converted: Record<string, string[]> = {};")
            fields_lines.append("    for (const [k, v] of Object.entries(params)) {")
            fields_lines.append("      if (v != null) converted[k] = v;")
            fields_lines.append("    }")
            fields_lines.append("    return super.fields(converted);")
            fields_lines.append("  }")

    filter_params = [p for p in spec.query_params if p.name.startswith("filter__")]
    if filter_params:
        lines.append(f"export class {name}Collection extends Collection<{name}> {{")
        lines.append(f"  filter(kwargs: Partial<{query_name}>): this {{")
        lines.append("    return super.filter(kwargs as Record<string, unknown>);")
        lines.append("  }")
        if sort_lines:
            lines += sort_lines
        if fields_lines:
            lines += fields_lines
        lines.append("}")
    else:
        body_parts = []
        body_parts.append(f"export class {name}Collection extends Collection<{name}> {{")
        if sort_lines:
            body_parts += sort_lines
        if fields_lines:
            body_parts += fields_lines
        if not sort_lines and not fields_lines:
            body_parts[-1] = body_parts[-1] + "}" if body_parts else "}"
        else:
            body_parts.append("}")
        lines = body_parts if body_parts else []

    if not filter_params and not sort_lines and not fields_lines:
        lines = [
            f"export class {name}Collection extends Collection<{name}> {{",
            "}",
        ]

    return lines


def _render_ts_resource_class(
    spec: _TypeSpec, specs: dict[str, _TypeSpec], renderer: _TSRenderer
) -> list[str]:
    cls = spec.resource_class
    name = spec.class_name
    annotations = cls._annotations()
    query_name = f"{name}Query"
    edit_name = f"{name}Edit"
    collection_name = f"{name}Collection"

    lines: list[str] = []

    if "get_many" in spec.capabilities:
        lines += _render_ts_collection_class(spec, specs, renderer)

    lines.append(f"export class {name} extends Resource {{")

    body: list[str] = []
    body.append(f'  static _type = "{spec.type_name}";')

    attr_types: dict[str, Any] = {"id": annotations.get("id", spec.pk_type)}
    for attr in cls._attributes:
        attr_types[attr] = annotations.get(attr, Any)
    body.append("  static _attributeTypes: Record<string, string> = {")
    for key, tp in attr_types.items():
        body.append(f"    {key!r}: {renderer.render(tp)!r},")
    body.append("  };")

    rel_entries = list(cls._singular_rels) + list(cls._plural_rels)
    if rel_entries:
        body.append("  static _relationshipTypes: Record<string, [string, boolean]> = {")
        for field, target in sorted(spec.rel_targets.items()):
            body.append(f"    {field!r}: [{target[0]!r}, {str(target[1]).lower()}],")
        body.append("  };")

    caps = sorted(spec.capabilities)
    body.append(f"  static _capabilities = new Set({caps!r} as const);")

    for attr in cls._attributes:
        tp = renderer.render(annotations.get(attr, Any))
        body.append(f"  get {attr}(): {tp} {{ return this.get({attr!r}) as {tp}; }}")
        body.append(f"  set {attr}(value: {tp}) {{ this.set({attr!r}, value); }}")

    for rel, target in sorted(spec.rel_targets.items()):
        if target is not None and target[0] in specs:
            target_name = specs[target[0]].class_name
            if target[1]:
                ret = f"{target_name}Collection | null"
            else:
                nullable = _is_optional(annotations.get(rel))
                ret = f"{target_name} | null" if nullable else target_name
        elif target is not None:
            ret = "Collection<any> | null" if target[1] else "Resource | null"
        else:
            ret = "unknown" if not target[1] else "Collection<unknown>"
        body.append(f"  get {rel}(): {ret} {{ return this.get({rel!r}) as {ret}; }}")
        body.append(f"  set {rel}(value: {ret}) {{ this.set({rel!r}, value); }}")

    if "get_many" in spec.capabilities:
        body += [
            "",
            f"  static list(): {collection_name} {{",
            f"    return super.list() as unknown as {collection_name};",
            "  }",
        ]

    if "get_one" in spec.capabilities:
        id_type = "string"
        get_includes = sorted(spec.get_include_types)
        if get_includes:
            includes_literal = " | ".join(f'"{inc}"' for inc in get_includes)
            if len(get_includes) > 1:
                includes_literal = f"({includes_literal})"
            body += [
                "",
                f"  static async get(id: {id_type}, ...includes: {includes_literal}[]): Promise<{name}>;",
            ]
        body += [
            "",
            f"  static async get(id: {id_type}, ...includes: string[]): Promise<{name}> {{",
            f"    return super.get(id as any, ...includes) as unknown as Promise<{name}>;",
            "  }",
        ]

    if "get_many" in spec.capabilities:
        find_includes = sorted(spec.find_include_types)
        if find_includes:
            includes_literal = " | ".join(f'"{inc}"' for inc in find_includes)
            if len(find_includes) > 1:
                includes_literal = f"({includes_literal})"
            body += [
                "",
                f"  static async find(query: Partial<{query_name}>, ...includes: {includes_literal}[]): Promise<{name}>;",
            ]
        body += [
            "",
            f"  static async find(query?: Partial<{query_name}>): Promise<{name}> {{",
            f"    return super.find(query as Record<string, unknown>) as unknown as Promise<{name}>;",
            "  }",
        ]

    if "create" in spec.capabilities:
        create_name = f"{name}Create"
        body += [
            "",
            f"  static async create(props?: {create_name}): Promise<{name}> {{",
            f"    return super.create(props as unknown as Record<string, unknown>) as unknown as Promise<{name}>;",
            "  }",
        ]

    if "edit" in spec.capabilities:
        body += [
            "",
            f"  async save(props?: {edit_name}): Promise<void> {{",
            "    await super.save(props as Record<string, unknown>);",
            "  }",
        ]

    edit_rels = [
        (rel, spec.rel_targets.get(rel))
        for rel, ops in sorted(spec.rel_capabilities.items())
        if "edit" in ops
    ]
    if edit_rels:
        body.append("")
        for rel, target in edit_rels:
            target_type = _ts_target_type(target, specs)
            body += [
                f"  async edit(relationship: {rel!r}, resource: {target_type}): Promise<void>;",
            ]
        body += [
            "  async edit(relationship: string, resource: unknown): Promise<void> {",
            "    await super.edit(relationship, resource);",
            "  }",
        ]

    for cap in ("add", "remove", "reset"):
        rels = [
            (rel, spec.rel_targets.get(rel))
            for rel, ops in sorted(spec.rel_capabilities.items())
            if cap in ops
        ]
        if rels:
            body.append("")
            for rel, target in rels:
                target_type = _ts_target_type(target, specs)
                arr_type = f"({target_type})" if " | " in target_type else target_type
                body += [
                    f"  async {cap}(relationship: {rel!r}, ...resources: {arr_type}[]): Promise<void>;",
                ]
            body += [
                f"  async {cap}(relationship: string, ...resources: unknown[]): Promise<void> {{",
                f"    await super.{cap}(relationship, ...resources);",
                "  }",
            ]

    if spec.rpc_actions:
        actions_repr = ", ".join(f"{a!r}: {m!r}" for a, m in spec.rpc_actions.items())
        body.append("")
        body.append(f"  static _rpcMethods: Record<string, string> = {{{actions_repr}}}")
        actions_literal = " | ".join(f"'{a}'" for a in spec.rpc_actions)
        body.append("")
        body += [
            f"  async rpc(action: {actions_literal}, payload?: unknown, mimetype?: string): Promise<any> {{",
            "    return super.rpc(action as string, payload, mimetype);",
            "  }",
        ]

    lines += _indent(body)
    lines.append("}")
    return lines


def _render_ts_resources(specs: dict[str, _TypeSpec]) -> str:
    renderer = _TSRenderer()
    sections: list[str] = []

    query_interfaces: list[str] = []
    for spec in specs.values():
        query_entries = [
            (_strip_prefix(p.name), renderer.render(p.annotation))
            for p in spec.query_params
            if p.name.startswith("filter__")
        ]
        get_entries = [
            (_strip_prefix(p.name), renderer.render(p.annotation)) for p in spec.get_query_params
        ]
        if "get_many" in spec.capabilities:
            query_interfaces.append(
                "\n".join(_render_ts_typed_interface(f"{spec.class_name}Query", query_entries))
            )
        if "get_one" in spec.capabilities:
            query_interfaces.append(
                "\n".join(_render_ts_typed_interface(f"{spec.class_name}GetQuery", get_entries))
            )
        if "edit" in spec.capabilities:
            cls = spec.resource_class
            annotations = cls._annotations()
            singular_rels = dict(cls._singular_rels)
            plural_rels = dict(cls._plural_rels)
            edit_entries: list[tuple[str, str]] = []
            for f in cls._edit_fields:
                if f in singular_rels and singular_rels[f] in specs:
                    target_name = specs[singular_rels[f]].class_name
                    rendered = f"{target_name} | {renderer.render(annotations.get(f, Any))} | null"
                elif f in plural_rels and plural_rels[f] in specs:
                    target_name = specs[plural_rels[f]].class_name
                    rendered = (
                        f"{target_name}[] | {renderer.render(annotations.get(f, Any))} | null"
                    )
                else:
                    rendered = renderer.render_optional(annotations.get(f, Any))
                edit_entries.append((f, rendered))
            query_interfaces.append(
                "\n".join(_render_ts_typed_interface(f"{spec.class_name}Edit", edit_entries))
            )
        if "create" in spec.capabilities:
            cls = spec.resource_class
            annotations = cls._annotations()
            singular_rels = dict(cls._singular_rels)
            plural_rels = dict(cls._plural_rels)
            create_entries: list[tuple[str, str]] = []
            for f in cls._create_fields:
                annotation = annotations.get(f, Any)
                raw = renderer.render(annotation)
                # Strip | null — interface field optionality handled via ?
                if raw.endswith(" | null"):
                    raw = raw[:-7]
                if f in singular_rels and singular_rels[f] in specs:
                    target_name = specs[singular_rels[f]].class_name
                    rendered = f"{target_name} | {raw}"
                elif f in plural_rels and plural_rels[f] in specs:
                    target_name = specs[plural_rels[f]].class_name
                    rendered = f"({target_name} | {raw})[]"
                else:
                    rendered = raw
                if f in cls._required_create_fields:
                    create_entries.append((f, rendered))
                else:
                    if rendered != "unknown" and "null" not in rendered.split(" | "):
                        rendered = f"{rendered} | null"
                    create_entries.append((f, rendered))
            query_interfaces.append(
                "\n".join(_render_ts_typed_interface(f"{spec.class_name}Create", create_entries))
            )
    sections.append("\n\n".join(query_interfaces))

    for spec in specs.values():
        sections.append("\n".join(_render_ts_resource_class(spec, specs, renderer)))

    return (
        'import { Collection } from "./_runtime/collection.js";\n'
        'import { Resource } from "./_runtime/resource.js";\n'
        "\n" + "\n\n".join(sections) + "\n"
    )


def _render_ts_sdk(specs: dict[str, _TypeSpec]) -> str:
    lines = [
        'import { DjsonApiSdk } from "./_runtime/sdk.js";',
        'import { Resource } from "./_runtime/resource.js";',
        'import type { SdkConfig } from "./_runtime/sdk.js";',
    ]
    names = sorted(spec.class_name for spec in specs.values())
    lines.append(f'import {{ {", ".join(names)} }} from "./resources.js";')
    lines.append("")

    lines.append("export class SDK extends DjsonApiSdk {")
    lines.append("  static _resourceClasses: Record<string, typeof Resource> = {")
    for spec in specs.values():
        lines.append(f"    {spec.type_name!r}: {spec.class_name},")
    lines.append("  };")
    lines.append("")
    for spec in specs.values():
        lines.append(f"  declare readonly {spec.type_name}: typeof {spec.class_name};")
    lines.append("}")
    lines.append("")
    lines.append("function _register(sdk: SDK): void {")
    lines.append("  for (const [typeName, cls] of Object.entries(SDK._resourceClasses)) {")
    lines.append("    ;(sdk as any)._registry.set(typeName, cls);")
    lines.append("    cls._sdk = sdk;")
    lines.append("  }")
    lines.append("}")
    lines.append("")
    lines.append("export const sdk: SDK = DjsonApiSdk._withProxy(new SDK()) as SDK;")
    lines.append("_register(sdk);")
    return "\n".join(lines) + "\n"


def _render_ts_init(specs: dict[str, _TypeSpec]) -> str:
    names = sorted(spec.class_name for spec in specs.values())
    lines = [
        'export { DjsonApiSdk } from "./_runtime/sdk.js";',
        'export type { SdkConfig, ResourceConstructor, RequestOptions } from "./_runtime/sdk.js";',
        'export { Resource } from "./_runtime/resource.js";',
        'export { Collection } from "./_runtime/collection.js";',
        f'export {{ {", ".join(names)} }} from "./resources.js";',
    ]
    for spec in specs.values():
        n = spec.class_name
        type_exports = [f"{n}Query"]
        if "create" in spec.capabilities:
            type_exports.append(f"{n}Create")
        if "get_one" in spec.capabilities:
            type_exports.append(f"{n}GetQuery")
        if "edit" in spec.capabilities:
            type_exports.append(f"{n}Edit")
        lines.append(f'export type {{ {", ".join(type_exports)} }} from "./resources.js";')
    lines.append('export { SDK, sdk } from "./sdk.js";')
    return "\n".join(lines) + "\n"


def generate_typescript(
    api: DjsonApi, output_dir: str | Path, package_name: str | None = None
) -> Path:
    """Generate a typed TypeScript SDK for ``api`` into ``output_dir``."""
    output_dir = Path(output_dir).expanduser().resolve()

    specs = _collect(api)

    output_dir.mkdir(parents=True, exist_ok=True)

    _this_file = Path(__file__).resolve()
    runtime_src = _this_file.parent.parent / "djsonapi_client_ts" / "src"
    if not runtime_src.is_dir():
        raise RuntimeError(f"Cannot find djsonapi_client_ts/src at {runtime_src}")
    runtime_dst = output_dir / "_runtime"
    if runtime_dst.exists():
        shutil.rmtree(runtime_dst)
    shutil.copytree(runtime_src, runtime_dst)

    (output_dir / "resources.ts").write_text(_render_ts_resources(specs))
    (output_dir / "sdk.ts").write_text(_render_ts_sdk(specs))
    (output_dir / "index.ts").write_text(_render_ts_init(specs))
    return output_dir


def generate(api: DjsonApi, output_dir: str | Path, package_name: str | None = None) -> Path:
    """Generate a typed SDK package for ``api`` into ``output_dir``."""
    output_dir = Path(output_dir).expanduser().resolve()
    package_name = package_name or output_dir.name
    if not package_name.isidentifier():
        raise ValueError(f"Invalid package name: {package_name}")

    specs = _collect(api)

    output_dir.mkdir(parents=True, exist_ok=True)

    import djsonapi_client_py

    runtime_src = Path(djsonapi_client_py.__file__).parent
    runtime_dst = output_dir / "_runtime"
    if runtime_dst.exists():
        shutil.rmtree(runtime_dst)
    runtime_dst.mkdir()
    for source in sorted(runtime_src.glob("*.py")):
        shutil.copy(source, runtime_dst / source.name)

    (output_dir / "resources.py").write_text(_render_resources(specs))
    (output_dir / "sdk.py").write_text(_render_sdk(specs))
    (output_dir / "__init__.py").write_text(_render_init(specs))
    (output_dir / "py.typed").write_text("")
    return output_dir
