from django.utils.deprecation import MiddlewareMixin

from .resources import handle_exception


class DjsonApiExceptionMiddleware(MiddlewareMixin):
    def process_exception(self, request, exc):
        return handle_exception(exc)
