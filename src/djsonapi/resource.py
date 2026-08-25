import datetime
import uuid
from collections.abc import Sequence
from dataclasses import MISSING as DC_MISSING
from dataclasses import dataclass
from dataclasses import fields as dc_fields
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    cast,
    dataclass_transform,
    get_args,
    get_origin,
)

UNSET = object()

_PYTHON_TO_JSONSCHEMA = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    uuid.UUID: {"type": "string", "format": "uuid"},
    datetime.datetime: {"type": "string", "format": "date-time"},
    datetime.date: {"type": "string", "format": "date"},
}


def _type_to_schema(tp: Any, default: Any = UNSET) -> dict:
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is Annotated:
        result = _type_to_schema(args[0])
        for arg in args[1:]:
            if isinstance(arg, dict):
                result.update(arg)
    elif origin is Literal:
        arg_types = {type(a) for a in args}
        if len(args) == 1:
            result: dict = {"const": args[0]}
        else:
            result = {"enum": list(args)}
        if len(arg_types) == 1:
            _type = _PYTHON_TO_JSONSCHEMA.get(list(arg_types)[0])
            if _type:
                result["type"] = _type["type"]
    elif origin is list:
        item_schema = _type_to_schema(args[0]) if args else {}
        result = {"type": "array", "items": item_schema}
    elif origin is dict:
        result = {"type": "object"}
    elif type(None) in args:
        non_null = [a for a in args if a is not type(None)]
        if len(non_null) == 1:
            result = _type_to_schema(non_null[0])
            result["nullable"] = True
        else:
            result = {"anyOf": [_type_to_schema(a) for a in non_null], "nullable": True}
    elif tp in _PYTHON_TO_JSONSCHEMA:
        result = dict(_PYTHON_TO_JSONSCHEMA[tp])
    else:
        result = {"type": "string"}
    if default is not UNSET:
        result["default"] = default
    return result


@dataclass_transform()
class Resource:
    UNSET = UNSET
    id: Any
    meta: dict | None = None
    _type: ClassVar[str] = ""
    _attributes: ClassVar[list[str]] = []
    _singular_relationships: ClassVar[Sequence[str | tuple[str, str]] | dict[str, str]] = []
    _plural_relationships: ClassVar[Sequence[str | tuple[str, str]] | dict[str, str]] = []
    # Normalized by __init_subclass__ into list[tuple[str, str]]
    _singular_rels: ClassVar[list[tuple[str, str]]] = []
    _plural_rels: ClassVar[list[tuple[str, str]]] = []
    _create_fields: ClassVar[list[str]] = []
    _required_create_fields: ClassVar[list[str]] = []
    _edit_fields: ClassVar[list[str]] = []
    _read_fields: ClassVar[list[str]] = []

    @staticmethod
    def _normalize_relationships(
        rels: Any,
    ) -> list[tuple[str, str]]:
        if isinstance(rels, dict):
            return list(rels.items())
        result: list[tuple[str, str]] = []
        for item in rels:
            if isinstance(item, str):
                result.append((item, item))
            elif isinstance(item, tuple) and len(item) == 2:
                result.append(item)
            else:
                raise TypeError(f"Expected str or 2-tuple, got {type(item).__name__}")
        return result

    def __hash__(self) -> int:
        return hash((self._type, getattr(self, "id", None)))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Resource):
            return NotImplemented
        return self._type == other._type and getattr(self, "id", None) == getattr(
            other, "id", None
        )

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        dataclass(cls)
        cls._singular_rels = cls._normalize_relationships(cls._singular_relationships)
        cls._plural_rels = cls._normalize_relationships(cls._plural_relationships)

    @classmethod
    def _annotations(cls) -> dict[str, type]:
        annotations: dict[str, type] = {}
        for base in reversed(cls.__mro__):
            annotations.update(getattr(base, "__annotations__", {}))
        return annotations

    @classmethod
    def _field_map(cls) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for f in dc_fields(cast(type, cls)):
            default = f.default if f.default is not DC_MISSING else UNSET
            result[f.name] = {"type": f.type, "default": default}
        return result

    @classmethod
    def _field_schema(cls, name: str) -> dict:
        field_map = cls._field_map()
        info = field_map.get(name)
        if info:
            return _type_to_schema(info["type"], default=info["default"])
        return _type_to_schema(str)

    @classmethod
    def _rel_names(cls) -> set[str]:
        return {f for f, _ in cls._singular_rels} | {f for f, _ in cls._plural_rels}

    @staticmethod
    def _rel_schema_singular(type_name: str) -> dict:
        return {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "type": {"const": type_name},
                        "id": {"type": "string"},
                    },
                    "required": ["type", "id"],
                    "additionalProperties": False,
                }
            },
            "required": ["data"],
            "additionalProperties": False,
        }

    @staticmethod
    def _rel_schema_plural(type_name: str) -> dict:
        return {
            "type": "object",
            "properties": {
                "data": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"const": type_name},
                            "id": {"type": "string"},
                        },
                        "required": ["type", "id"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["data"],
            "additionalProperties": False,
        }

    @classmethod
    def _relationship_properties(cls, fields: set[str]) -> dict[str, dict]:
        rel_props: dict[str, dict] = {}
        for field, type_name in cls._singular_rels:
            if field in fields:
                rel_props[field] = cls._rel_schema_singular(type_name)
        for field, type_name in cls._plural_rels:
            if field in fields:
                rel_props[field] = cls._rel_schema_plural(type_name)
        return rel_props

    @classmethod
    def _attr_schema(cls, attr_fields: list[str], required_fields: list[str]) -> dict:
        attr_props: dict[str, dict] = {}
        for field in attr_fields:
            attr_props[field] = cls._field_schema(field)
        return {
            "type": "object",
            "properties": attr_props,
            "required": required_fields,
            "additionalProperties": False,
        }

    @classmethod
    def _read_attr_fields(cls) -> list[str]:
        if cls._read_fields:
            return [f for f in cls._attributes if f in cls._read_fields]
        return list(cls._attributes)

    @classmethod
    def _read_rel_names(cls) -> set[str]:
        if cls._read_fields:
            return {f for f in cls._rel_names() if f in cls._read_fields}
        return cls._rel_names()

    @classmethod
    def jsonschema_read(cls) -> dict:
        properties: dict[str, dict] = {
            "type": {"const": cls._type},
        }
        required: list[str] = ["type"]

        properties["id"] = cls._field_schema("id")
        required.append("id")

        read_attrs = cls._read_attr_fields()
        if read_attrs:
            properties["attributes"] = cls._attr_schema(read_attrs, list(read_attrs))
            required.append("attributes")

        read_rel_names = cls._read_rel_names()
        if read_rel_names:
            rel_props = cls._relationship_properties(read_rel_names)
            properties["relationships"] = {
                "type": "object",
                "properties": rel_props,
                "required": [f for f, _ in cls._singular_rels if f in read_rel_names]
                + [f for f, _ in cls._plural_rels if f in read_rel_names],
                "additionalProperties": False,
            }
            required.append("relationships")

        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    @classmethod
    def jsonschema_create(cls) -> dict:
        properties: dict[str, dict] = {
            "type": {"const": cls._type},
        }
        required: list[str] = ["type"]

        rel_names = cls._rel_names()
        create_fields = set(cls._create_fields)

        if "id" in create_fields:
            properties["id"] = {"type": ["string", "integer"]}
            if "id" in cls._required_create_fields:
                required.append("id")

        attr_fields = [f for f in cls._create_fields if f != "id" and f not in rel_names]
        if attr_fields:
            properties["attributes"] = cls._attr_schema(
                attr_fields,
                [f for f in cls._required_create_fields if f in attr_fields],
            )
            required.append("attributes")

        create_rel_fields = create_fields & rel_names
        if create_rel_fields:
            rel_props = cls._relationship_properties(create_rel_fields)
            if rel_props:
                properties["relationships"] = {
                    "type": "object",
                    "properties": rel_props,
                    "additionalProperties": False,
                }

        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    @classmethod
    def jsonschema_edit(cls) -> dict:
        properties: dict[str, dict] = {
            "type": {"const": cls._type},
            "id": {"type": ["string", "integer"]},
        }
        required: list[str] = ["type", "id"]

        rel_names = cls._rel_names()
        edit_fields = set(cls._edit_fields)

        attr_fields = [f for f in cls._edit_fields if f not in rel_names]
        if attr_fields:
            properties["attributes"] = cls._attr_schema(attr_fields, [])
            properties["attributes"]["minProperties"] = 1

        edit_rel_fields = edit_fields & rel_names
        if edit_rel_fields:
            rel_props = cls._relationship_properties(edit_rel_fields)
            if rel_props:
                properties["relationships"] = {
                    "type": "object",
                    "properties": rel_props,
                    "minProperties": 1,
                    "additionalProperties": False,
                }

        has_attrs = "attributes" in properties
        has_rels = "relationships" in properties
        if has_attrs and not has_rels:
            required.append("attributes")
        elif has_rels and not has_attrs:
            required.append("relationships")

        result: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        if has_attrs and has_rels:
            result["minProperties"] = 3

        return result

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    @staticmethod
    def _convert_value(value: Any, tp: type) -> Any:
        if tp is int:
            return int(value)
        if tp is float:
            return float(value)
        if tp is str:
            return str(value)
        if tp is bool:
            return bool(value)
        if tp is uuid.UUID:
            return uuid.UUID(value)
        if tp is datetime.datetime:
            return datetime.datetime.fromisoformat(value)
        if tp is datetime.date:
            return datetime.date.fromisoformat(value)
        return value

    @classmethod
    def _from_jsonapi_payload(cls, payload: dict, fields: list[str] | None = None) -> object:
        data = payload["data"]
        annotations = cls._annotations()
        instance = object.__new__(cls)

        if fields is None:
            fields = cls._create_fields

        for field in fields:
            if field == "id":
                instance.id = UNSET
            elif field in cls._attributes:
                setattr(instance, field, UNSET)
            elif field in cls._rel_names():
                if field in dict(cls._plural_rels):
                    setattr(instance, field, [])
                else:
                    setattr(instance, field, UNSET)

        for field in fields:
            if field == "id" and "id" in data:
                instance.id = cls._convert_value(data["id"], annotations.get("id", str))
            elif field in cls._attributes:
                attr_val = data.get("attributes", {}).get(field)
                if attr_val is not None:
                    setattr(
                        instance, field, cls._convert_value(attr_val, annotations.get(field, str))
                    )
            elif field in cls._rel_names():
                rel_data = data.get("relationships", {}).get(field, {}).get("data")
                if rel_data is not None:
                    if isinstance(rel_data, dict):
                        setattr(
                            instance,
                            field,
                            cls._convert_value(rel_data["id"], annotations.get(field, str)),
                        )
                    elif isinstance(rel_data, list):
                        tp = annotations.get(field, list)
                        args = get_args(tp)
                        item_tp = args[0] if args else str
                        setattr(
                            instance,
                            field,
                            [cls._convert_value(item["id"], item_tp) for item in rel_data],
                        )

        return instance

    def _read_filter(self) -> set[str] | None:
        return set(self._read_fields) if self._read_fields else None

    def serialize(self) -> dict:
        result: dict = {"type": self._type}
        id_val = getattr(self, "id", UNSET)
        if id_val is not UNSET:
            result["id"] = str(id_val)
        read_fields = self._read_filter()
        for field in self._attributes:
            if read_fields and field not in read_fields:
                continue
            value = getattr(self, field, UNSET)
            if value is not UNSET:
                result.setdefault("attributes", {})[field] = self._serialize_value(value)
        for field, type_name in self._singular_rels:
            if read_fields and field not in read_fields:
                continue
            value = getattr(self, field, UNSET)
            if value is not UNSET:
                rel = {}
                if value is not None:
                    rel["data"] = {"type": type_name, "id": str(value)}
                result.setdefault("relationships", {})[field] = rel
        for field, type_name in self._plural_rels:
            if read_fields and field not in read_fields:
                continue
            value = getattr(self, field, UNSET)
            if value is not UNSET:
                rel = {}
                if value is not None:
                    assert isinstance(value, Sequence)
                    rel["data"] = [{"type": type_name, "id": str(item)} for item in value]
                result.setdefault("relationships", {})[field] = rel
        meta = getattr(self, "meta", UNSET)
        if meta is not None and meta is not UNSET:
            result["meta"] = meta
        return result
