import logging
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy

from django.db.transaction import atomic
from django.http import HttpResponse, JsonResponse
from django.urls import NoReverseMatch, path, reverse
from django.views.decorators.csrf import csrf_exempt

from .exceptions import (BadRequest, DjsonApiException, DjsonApiExceptionMulti,
                         NotFound, ServerError)

logger = logging.getLogger(__name__)


def JsonApiResponse(*args, **kwargs):
    result = JsonResponse(*args, **kwargs)
    result['Content-Type'] = "application/vnd.api+json"
    return result


class Resource:
    def __init__(self, obj):
        self.obj = obj

    def __hash__(self):
        if self.obj:
            return hash(self.obj)

    def __eq__(self, other):
        try:
            return hash(self) == hash(other)
        except Exception:
            return super().__eq__(other)

    @classmethod
    def exception_handler(cls, exc):
        if isinstance(exc, DjsonApiException):
            return JsonApiResponse({'errors': exc.render()}, status=exc.status)
        else:
            logger.exception(str(exc))
            exc = ServerError()
            return JsonApiResponse({'errors': exc.render()}, status=exc.status)

    @classmethod
    def as_views(cls):
        result = []
        if (hasattr(cls, 'get_one') or
                hasattr(cls, 'edit_one') or
                hasattr(cls, 'delete_one')):
            result.append(path(f"{cls.TYPE}/<str:obj_id>",
                               cls._one_view,
                               name=f"{cls.TYPE}_object"))
        if hasattr(cls, 'get_many') or hasattr(cls, 'create_one'):
            result.append(path(f"{cls.TYPE}", cls._many_view,
                          name=f"{cls.TYPE}_list"))
        plural_relationships = set()
        for attr in dir(cls):
            match = re.search(r'^change_([^_]+)$', attr)
            if match:
                relationship_name = match.groups()[0]
                result.append(path(
                    f"{cls.TYPE}/<str:obj_id>/relationships/"
                    f"{relationship_name}",
                    cls._change_view,
                    kwargs={'relationship_name': relationship_name},
                    name=f"{cls.TYPE}_{relationship_name}_relationship"
                ))

            match = re.search(r'^(add|remove|reset)_([^_]+)$', attr)
            if match:
                _, relationship_name = match.groups()

                if relationship_name in plural_relationships:
                    continue
                plural_relationships.add(relationship_name)

                result.append(path(
                    f"{cls.TYPE}/<str:obj_id>/relationships/"
                    f"{relationship_name}",
                    cls._change_plural_view,
                    kwargs={'relationship_name': relationship_name},
                    name=f"{cls.TYPE}_{relationship_name}_plural_relationship",
                ))

            match = re.search(r'^get_([^_]+)$', attr)
            if match:
                relationship_name = match.groups()[0]
                if relationship_name in ('one', 'many'):
                    continue
                result.append(path(
                    f"{cls.TYPE}/<str:obj_id>/{relationship_name}",
                    cls._get_related_view,
                    kwargs={'relationship_name': relationship_name},
                    name=f"{cls.TYPE}_get_{relationship_name}",
                ))
        return result

    @classmethod
    @csrf_exempt
    def _one_view(cls, request, obj_id):
        mappings = {'GET': ('get_one', '_get_one_view'),
                    'PATCH': ('edit_one', '_edit_one_view'),
                    'DELETE': ('delete_one', '_delete_one_view')}
        try:
            inner_method, outer_method = mappings[request.method]
        except KeyError:
            cls._raise_unsupported_verb_error(request)
        if not hasattr(cls, inner_method):
            cls._raise_unsupported_verb_error(request)

        method = getattr(cls, outer_method)
        try:
            return method(request, obj_id)
        except Exception as exc:
            return cls.exception_handler(exc)

    @classmethod
    def _get_one_view(cls, request, obj_id):
        result = cls.get_one(request, obj_id)
        if isinstance(result, HttpResponse):
            return result
        result = cls._process_one(request, result)
        result.setdefault('links', {}).setdefault('self',
                                                  request.get_full_path())
        return JsonApiResponse(result)

    @classmethod
    def _edit_one_view(cls, request, obj_id):
        with atomic():
            result = cls.edit_one(request, obj_id)
            if isinstance(result, HttpResponse):
                return result
            result = cls._process_one(request, result)
            result.setdefault('links', {}).setdefault('self',
                                                      request.get_full_path())
            return JsonApiResponse(result)

    @classmethod
    def _delete_one_view(cls, request, obj_id):
        result = cls.delete_one(request, obj_id)
        if isinstance(result, HttpResponse):
            return result
        return HttpResponse('', status=204)

    @classmethod
    @csrf_exempt
    def _many_view(cls, request):
        mappings = {'GET': ('get_many', '_get_many_view'),
                    'POST': ('create_one', '_create_one_view')}
        try:
            inner_method, outer_method = mappings[request.method]
        except KeyError:
            cls._raise_unsupported_verb_error(request)
        if not hasattr(cls, inner_method):
            cls._raise_unsupported_verb_error(request)

        method = getattr(cls, outer_method)
        try:
            return method(request)
        except Exception as exc:
            return cls.exception_handler(exc)

    @classmethod
    def _create_one_view(cls, request):
        with atomic():
            result = cls.create_one(request)
            if isinstance(result, HttpResponse):
                return result
            result = cls._process_one(request, result)
            try:
                self_link = reverse(f"{cls.TYPE}_object",
                                    kwargs={'obj_id': result['data']['id']})
            except NoReverseMatch:
                self_link = None
            else:
                result.\
                    setdefault('links', {}).\
                    setdefault('self', self_link)
            response = JsonApiResponse(result, status=201)
            if self_link is not None:
                response['Location'] = self_link
            return response

    @classmethod
    def _get_many_view(cls, request):
        result = cls.get_many(request)
        if isinstance(result, HttpResponse):
            return result
        if not isinstance(result, Mapping):
            result = {'data': result}

        data = []
        for obj in result['data']:
            serialized = cls.serialize(obj)
            serialized = cls._decorate_serialized(request, serialized)
            data.append(serialized)
        result['data'] = data

        if 'included' in result:
            result['included'] = cls._process_included(request,
                                                       result['included'])

        result['links'] = cls._process_links(request, result.get('links'))

        return JsonApiResponse(result)

    @classmethod
    @csrf_exempt
    def _change_view(cls, request, relationship_name, obj_id):
        if request.method != "PATCH":
            cls._raise_unsupported_verb_error(request)
        method = getattr(cls, f"change_{relationship_name}")
        try:
            method(request, obj_id)
        except Exception as exc:
            return cls.exception_handler(exc)
        return HttpResponse('', status=204)

    @classmethod
    @csrf_exempt
    def _change_plural_view(cls, request, relationship_name, obj_id):
        mapping = {'POST': "add", 'DELETE': "remove", 'PATCH': "reset"}
        try:
            prefix = mapping[request.method]
        except KeyError:
            cls._raise_unsupported_verb_error(request)
        if not hasattr(cls, f"{prefix}_{relationship_name}"):
            cls._raise_unsupported_verb_error(request)

        method = getattr(cls, f"{prefix}_{relationship_name}")
        try:
            with atomic():
                result = method(request, obj_id)
                if isinstance(result, HttpResponse):
                    return result
        except Exception as exc:
            return cls.exception_handler(exc)
        return HttpResponse('', status=204)

    @classmethod
    def _get_related_view(cls, request, relationship_name, obj_id):
        if request.method != "GET":
            cls._raise_unsupported_verb_error(request)

        method = getattr(cls, f"get_{relationship_name}")
        try:
            result = method(request, obj_id)
            if isinstance(result, HttpResponse):
                return result
        except Exception as exc:
            return cls.exception_handler(exc)

        if not isinstance(result, Mapping):
            result = {'data': result}

        if isinstance(result['data'], Sequence):
            data = []
            for obj in result['data']:
                serialized = obj.serialize(obj.obj)
                serialized = obj._decorate_serialized(request, serialized)
                data.append(serialized)
            result['data'] = data
        else:
            obj = result['data']
            serialized = obj.serialize(obj.obj)
            serialized = obj._decorate_serialized(request, serialized)
            result['data'] = serialized

        if 'included' in result:
            result['included'] = cls._process_included(request,
                                                       result['included'])

        result['links'] = cls._process_links(request, result.get('links'))

        return JsonApiResponse(result)

    @classmethod
    def _process_included(cls, request, included):
        result = []
        for obj in included:
            if isinstance(obj, Resource):
                serialized = obj.serialize(obj.obj)
                serialized = obj._decorate_serialized(request, serialized)
            else:
                serialized = obj

            serialized = cls._limit_fields(request, serialized)
            result.append(serialized)

        return result

    @classmethod
    def _process_one(cls, request, result):
        try:
            result['data']
        except TypeError:
            result = {'data': result}

        # data
        data = cls.serialize(result['data'])
        data = cls._decorate_serialized(request, data)
        result['data'] = data

        # included
        if 'included' in result:
            result['included'] = cls._process_included(request,
                                                       result['included'])

        return result

    @classmethod
    def _process_links(cls, request, links):
        if links is None:
            result = {}
        else:
            result = deepcopy(links)

        result.setdefault('self', request.get_full_path())

        for key, value in result.items():
            if key == "self":
                continue
            params = request.GET.copy()
            try:
                items = value.items()
            except AttributeError:
                pass
            else:
                for inner_key, inner_value in items:
                    params[inner_key] = inner_value
                result[key] = (request.path +
                               '?' +
                               params.urlencode(safe="[]"))
        return result

    @classmethod
    def serialize(cls, obj):
        return obj

    @classmethod
    def _decorate_serialized(cls, request, serialized):
        result = deepcopy(serialized)

        # type, 'self' link
        result.setdefault('type', cls.TYPE)
        try:
            self_link = reverse(f"{cls.TYPE}_object",
                                kwargs={'obj_id': result['id']})
        except NoReverseMatch:
            pass
        else:
            result.setdefault('links', {}).setdefault('self', self_link)

        # Relationhips
        for key, relationship in result.get('relationships', {}).items():
            # To-one relationship
            if (isinstance(relationship, Resource) or
                    (isinstance(relationship, Mapping) and
                     'data' in relationship and
                     not isinstance(relationship['data'], Sequence))):
                # data
                if not isinstance(relationship, Mapping):
                    relationship = result['relationships'][key] = {
                        'data': relationship,
                    }
                if isinstance(relationship['data'], Resource):
                    relationship['data'] = {
                        'type': relationship['data'].TYPE,
                        'id': str(relationship['data'].obj),
                    }
                data = relationship['data']

                # 'related' link
                try:
                    url = reverse(f"{cls.TYPE}_get_{key}",
                                  kwargs={'obj_id': result['id']})
                except NoReverseMatch:
                    pass
                else:
                    relationship.\
                        setdefault('links', {}).\
                        setdefault('related', url)
                try:
                    url = reverse(f"{data['type']}_object",
                                  kwargs={'obj_id': data['id']})
                except NoReverseMatch:
                    pass
                else:
                    relationship.\
                        setdefault('links', {}).\
                        setdefault('related', url)
                # 'self' link
                try:
                    url = reverse(f"{cls.TYPE}_{key}_relationship",
                                  kwargs={'obj_id': result['id']})
                except NoReverseMatch:
                    pass
                else:
                    relationship.\
                        setdefault('links', {}).\
                        setdefault('self', url)

            # To-many relationship
            elif isinstance(relationship, (Sequence, Mapping)):
                if isinstance(relationship, Sequence):
                    result['relationships'][key] = {'data': relationship}
                    relationship = result['relationships'][key]
                # data
                if 'data' in relationship:
                    relationship['data'] = [
                        {'type': item.TYPE, 'id': str(item.obj)}
                        if isinstance(item, Resource)
                        else item
                        for item in relationship['data']
                    ]

                # 'self' link
                try:
                    url = reverse(f"{cls.TYPE}_{key}_plural_relationship",
                                  kwargs={'obj_id': result['id']})
                except NoReverseMatch:
                    pass
                else:
                    relationship.\
                        setdefault('links', {}).\
                        setdefault('self', url)

                # 'related' link
                try:
                    url = reverse(f"{cls.TYPE}_get_{key}",
                                  kwargs={'obj_id': result['id']})
                except NoReverseMatch:
                    pass
                else:
                    relationship.\
                        setdefault('links', {}).\
                        setdefault('related', url)

        # Fields
        result = cls._limit_fields(request, result)

        return result

    @classmethod
    def _raise_unsupported_verb_error(cls, request):
        raise NotFound(f"Endpoint '{request.path}' does not support method "
                       f"{request.method}")

    @classmethod
    def _limit_fields(cls, request, obj):
        obj = deepcopy(obj)

        key = f"fields[{obj['type']}]"
        if key not in request.GET:
            return obj

        fields = set(request.GET[key].split(','))
        existing_fields = (set(obj.get('attributes', {}).keys()) |
                           set(obj.get('relationships', {}).keys()))
        errors = [BadRequest(f"Field in 'fields' parameter is not part of the "
                             f"response ('{field}' was unexpected)",
                             source={'patameter': key})
                  for field in fields - existing_fields]
        if errors:
            raise DjsonApiExceptionMulti(*errors)

        if 'attributes' in obj:
            obj['attributes'] = {
                key: value
                for key, value in obj['attributes'].items()
                if key in fields}
            if not obj['attributes']:
                del obj['attributes']
        if 'relationships' in obj:
            obj['relationships'] = {
                key: value
                for key, value in obj['relationships'].items()
                if key in fields
            }
            if not obj['relationships']:
                del obj['relationships']

        return obj

    @classmethod
    def map_to_method(cls, request, other_resource, method_name, filters):
        params = request.GET.copy()
        params.update(filters)
        old_get = request.GET
        request.GET = params
        method = getattr(other_resource, method_name)
        result = method(request)
        try:
            result['data']
        except TypeError:
            result = {'data': result}

        try:
            result['data'] = [other_resource(item) for item in result['data']]
        except TypeError:
            result['data'] = other_resource(result['data'])
        request.GET = old_get
        return result
