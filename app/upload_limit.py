# 在 multipart 解析之前限制整个请求体，防止文件之外的表单字段绕过文件大小限制。
# 小型本地演示允许最多约 10MB 请求在内存中缓存，再交给 FastAPI 处理。
from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        parts, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body = message.get('body', b'')
            size += len(body)
            if size > self.max_bytes:
                return await JSONResponse({'detail': '请求体超过上传限制'}, status_code=413)(scope, receive, send)
            parts.append(body)
            if not message.get('more_body', False):
                break
        used = False
        async def replay():
            nonlocal used
            if not used:
                used = True
                return {'type': 'http.request', 'body': b''.join(parts), 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)
