from .resources import handle_exception


def DjsonApiExceptionMiddleware(get_response):
    def middleware(request):
        try:
            return get_response(middleware)
        except Exception as exc:
            return handle_exception(exc)

    return middleware