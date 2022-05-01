import json

import jsonschema

from .exceptions import BadRequest, DjsonApiExceptionMulti


def get_body(request):
    try:
        return json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise BadRequest(str(exc))


def raise_for_body(obj, schema):
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for exc in validator.iter_errors(obj):
        errors.append(
            BadRequest(exc.message, source={"pointer": "." + ".".join(exc.path)})
        )
    if errors:
        raise DjsonApiExceptionMulti(*errors)


def raise_for_params(obj, schema):
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for exc in validator.iter_errors(obj):
        path = list(exc.path)
        if path:
            source = {
                "parameter": "".join([path[0]] + [f"[{part}]" for part in path[1:]])
            }
        else:
            source = None
        errors.append(BadRequest(exc.message, source=source))
    if errors:
        raise DjsonApiExceptionMulti(*errors)


def Object(properties, required=None, **kwargs):
    if required is None:
        required = list(properties.keys())
    result = {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
    result.update(kwargs)
    return result


def String(enum=None, **kwargs):
    if isinstance(enum, str):
        enum = [enum]
    result = {"type": "string"}
    if enum is not None:
        result["enum"] = enum
    result.update(**kwargs)
    return result
