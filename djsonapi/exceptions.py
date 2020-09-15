from functools import reduce


def class_name_to_title(text):
    """ 'NotFound' => 'Not found' """

    result = ''.join((f" {char.lower()}" if char.isupper() else char
                      for char in text))
    if result.startswith(' '):  # pragma: no cover
        result = result[1:]
    result = ''.join((result[:1].upper(), result[1:]))
    return result


def class_name_to_code(text):
    """ 'NotFound' => 'not_found' """

    result = ''.join((f"_{char.lower()}" if char.isupper() else char
                      for char in text))
    if result.startswith('_'):  # pragma: no cover
        result = result[1:]
    return result


class DjsonApiException(Exception):
    def render(self):
        raise NotImplementedError()  # pragma: no cover

    @property
    def status(self):
        raise NotImplementedError()  # pragma: no cover


class DjsonApiExceptionSingle(DjsonApiException):
    STATUS = 500
    CODE = None
    TITLE = None
    DETAIL = "Something went wrong"
    SOURCE = None

    def __init__(self, detail=None, title=None, source=None):
        if detail is None:
            detail = self.DETAIL
        if title is None:
            if self.TITLE is None:
                title = class_name_to_title(self.__class__.__name__)
            else:
                title = self.TITLE
        if source is None:
            source = self.SOURCE
        # Exception's `__str__` method works best when initialized with only
        # positional arguments.
        super().__init__(title, detail, source)

    def render(self):
        status = str(self.STATUS)
        code = self.CODE
        if code is None:
            code = class_name_to_code(self.__class__.__name__)

        title, detail, source = self.args

        result = {
            'status': status, 'code': code, 'title': title, 'detail': detail,
        }
        if source is not None:
            result['source'] = source

        return [result]

    @property
    def status(self):
        return int(self.STATUS)

    title = property(fget=lambda self: self.args[0],
                     fset=lambda self, value: self._set(0, value))
    detail = property(fget=lambda self: self.args[1],
                      fset=lambda self, value: self._set(1, value))
    source = property(fget=lambda self: self.args[2],
                      fset=lambda self, value: self._set(2, value))

    def _set(self, pos, value):
        args = list(self.args)
        args[pos] = value
        self.args = tuple(args)


class DjsonApiExceptionMulti(DjsonApiException):
    """ Exception class to group several jsonapi exceptions together. Supports
        `render()` and `status` like the single exceptions.

        Usage:

            >>> try:
            ...     raise DjsonApiExceptionMulti(
            ...         NotFound("Happiness not found"),
            ...         Conflict("I am conflicted"),
            ...     )
            ... except JsonApiError as exc:
            ...     print(exc.render())
            <<< [{'status': "404", 'code': "not_found",
            ...   'title': "Object not found",
            ...   'detail': "Happiness not found"},
            ...  {'status': "409", 'code': "conflict",
            ...   'title': "Conflict", 'detail': "I am conflicted"}]

        Most likely you will use it like this in views:

            >>> errors = []
            >>> try:
            ...     do_something()
            ... except JsonApiError as exc:
            ...     errors.append(exc)
            >>> try:
            ...     do_something_else()
            ... except JsonApiError as exc:
            ...     errors.append(exc)
            >>> if errors:
            ...     raise DjsonApiExceptionMulti(*errors)
    """

    def render(self):
        return reduce(list.__add__, (arg.render() for arg in self.args), [])

    @property
    def status(self):
        """ Returns the most generally applicable HTTP error code. eg,

            404, 404 => 404
            400, 401 => 400
            401, 404 => 400
            401, 500 => 500
            401, 502 => 500
        """

        statuses = {exc.status for exc in self.args}
        if len(statuses) == 1:
            return statuses.pop()
        statuses = {(status // 100) * 100 for status in statuses}
        return max(statuses)


class ServerError(DjsonApiExceptionSingle):
    pass


class BadRequest(DjsonApiExceptionSingle):
    STATUS = 400


class NotFound(DjsonApiExceptionSingle):
    STATUS = 404


class Conflict(DjsonApiExceptionSingle):
    STATUS = 409
