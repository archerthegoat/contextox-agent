"""A same-origin, session-bound web entrypoint for the one local credential."""
import secrets
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response

from contextox.credentials import CredentialUnavailableError, MacKeychain, external_key_source
from contextox.models import DeepSeekKeyRequest, DeepSeekSettings, WorkspaceError
from contextox.store import WorkspaceStoreBusyError


def _guard(request: Request, token: str, *, write=False):
    server = request.scope.get("server")
    if not server or server[0] not in ("127.0.0.1", "::ffff:127.0.0.1"):
        raise HTTPException(403, "请从本机工作台打开模型设置。")
    authority = f"127.0.0.1:{server[1]}" if server[1] != 80 else "127.0.0.1"
    origin = f"http://{authority}"
    if request.headers.get("host") != authority or request.headers.get("origin", origin) != origin:
        raise HTTPException(403, "请从本机工作台打开模型设置。")
    if write and (request.headers.get("origin") != origin or not secrets.compare_digest(
            request.headers.get("x-contextox-session", ""), token)):
        raise HTTPException(403, "请刷新本机工作台后再修改模型设置。")


class _CredentialBoundary:
    """Authenticate before parsing the small secret body; never cache responses."""
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/api/local-settings/deepseek":
            await self.app(scope, receive, send)
            return

        async def no_cache(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = message | {"headers": headers + [(b"cache-control", b"no-store")]}
            await send(message)

        async def reject(status, message):
            body = WorkspaceError(code="local_settings_request_rejected", message=message,
                request_id=f"req_{uuid4().hex}").model_dump_json().encode()
            await no_cache({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
            await no_cache({"type": "http.response.body", "body": body})

        try:
            _guard(Request(scope), self.token, write=scope.get("method") not in {"GET", "HEAD"})
        except HTTPException as error:
            await reject(error.status_code, error.detail)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 4096:
                await reject(422, "模型设置请求过大。")
                return
            if not message.get("more_body", False):
                break
        received = False

        async def bounded_receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, no_cache)


def install_settings_routes(app: FastAPI, agent_profile: str) -> None:
    token = secrets.token_urlsafe(32)
    app.add_middleware(_CredentialBoundary, token=token)

    def snapshot(response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        source = external_key_source()
        if source is None:
            try:
                source = "keychain" if MacKeychain().contains() else "missing"
            except CredentialUnavailableError:
                source = "unavailable"
        runtime = app.state.path2_runtime
        return DeepSeekSettings(source=source, configured=source in {"environment", "env_file", "keychain"},
            busy=runtime.busy if runtime else False, agent_profile=agent_profile,
            thinking="disabled" if agent_profile == "demo-fast" else "enabled", session_token=token)

    def mutate(operation):
        if external_key_source():
            raise HTTPException(409, "当前 Key 由启动环境或显式配置文件管理，请修改后重启服务。")
        runtime = app.state.path2_runtime
        if runtime is None:
            raise HTTPException(503, "请先恢复本地资料库，再修改模型设置。")
        try:
            runtime.change_credentials(operation)
        except WorkspaceStoreBusyError:
            raise HTTPException(409, "任务正在执行，请结束后再修改 Key。") from None
        except CredentialUnavailableError:
            raise HTTPException(503, "无法访问 macOS Keychain，请解锁登录钥匙串后重试。") from None

    errors = {code: {"model": WorkspaceError} for code in (403, 409, 422, 503)}

    @app.get("/api/local-settings/deepseek", response_model=DeepSeekSettings, responses=errors, tags=["local-settings"])
    def get_settings(request: Request, response: Response):
        return snapshot(response)

    @app.put("/api/local-settings/deepseek", response_model=DeepSeekSettings, responses=errors, tags=["local-settings"])
    def save_key(payload: DeepSeekKeyRequest, request: Request, response: Response,
                 session_token: str = Header(alias="X-ContextOx-Session", min_length=32, max_length=64)):
        # Instantiate Keychain inside the operation so failures use the safe envelope.
        mutate(lambda: MacKeychain().save(payload.api_key.get_secret_value()))
        return snapshot(response)

    @app.delete("/api/local-settings/deepseek", response_model=DeepSeekSettings, responses=errors, tags=["local-settings"])
    def remove_key(request: Request, response: Response,
                   session_token: str = Header(alias="X-ContextOx-Session", min_length=32, max_length=64)):
        mutate(lambda: MacKeychain().remove())
        return snapshot(response)
