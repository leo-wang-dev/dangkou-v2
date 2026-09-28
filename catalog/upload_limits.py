"""Bound multipart bodies before Starlette spools files, including chunked uploads."""
import os
from fastapi import HTTPException


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers', []))
        if scope['type'] != 'http' or not headers.get(b'content-type',b'').startswith(b'multipart/form-data'):
            return await self.app(scope,receive,send)
        # Allow bounded multipart headers/fields above the validated image limit.
        limit = int(os.environ.get('CATALOG_UPLOAD_MAX_BYTES',20*1024*1024)) + 65536
        count = 0
        async def bounded_receive():
            nonlocal count
            message = await receive()
            count += len(message.get('body',b''))
            if count > limit:
                raise HTTPException(413,'图片超过 20MB，请压缩后重试')
            return message
        await self.app(scope,bounded_receive,send)
