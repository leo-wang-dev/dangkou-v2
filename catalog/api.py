from fastapi import FastAPI, HTTPException, Request, Response


def _auth(request: Request, token: str):
    if not token:
        return
    h = request.headers.get('X-Service-Token')
    if h != token and request.query_params.get('token') != token:
        raise HTTPException(401, 'unauthorized')


def register_routes(app: FastAPI):
    @app.get('/health')
    def health():
        return {'status': 'ready', 'version': 'v2.0'}

    @app.get('/img/{rel:path}')
    def img(rel: str, request: Request):
        _auth(request, app.state.token)
        try:
            data = app.state.storage.read(rel)
        except FileNotFoundError:
            raise HTTPException(404, 'no image')
        return Response(content=data, media_type='image/png')
